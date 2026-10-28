import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

from travel_agent.agents.travel import (
    create_follow_up_router,
    create_travel_agent,
    create_trip_formatter,
    create_trip_reviewer,
)
from travel_agent.agents.intake import create_intake_agent
from travel_agent.app.output import (
    format_plan,
    render_service_menu,
    render_trip_plan,
    render_trip_request,
)
from travel_agent.app.run_log import new_run_id, write_run_record
from travel_agent.app.trace import TokenPricing, TraceRecorder
from travel_agent.clients.fallback import FallbackAgent
from travel_agent.config import Settings
from travel_agent.schemas.trip import TripPlan
from travel_agent.schemas import request as request_fields
from travel_agent.schemas.request import TripRequest
from travel_agent.memory.store import JsonMemoryStore, durable_preferences
from travel_agent.schemas.checkpoint import WorkflowCheckpoint
from travel_agent.workflows.checkpoint import CheckpointStore
from travel_agent.workflows.gate import plan_with_invariant_gate
from travel_agent.workflows.followup import (
    answer_plan_question,
    classify_follow_up,
    looks_like_end,
)
from travel_agent.workflows.intake import (
    apply_evidence_guards,
    collect_trip_request,
    extract_trip_request,
    merge_trip_requests,
)
from travel_agent.workflows.offline import build_offline_trip_plan
from travel_agent.workflows.prompts import (
    build_planner_prompt,
    build_trip_narrative_prompt,
)
from travel_agent.workflows.maf import run_plan_review_workflow
from travel_agent.workflows.trip_planning import plan_trip_structured
from travel_agent.workflows.search import build_search_plan, execute_search_plan
from travel_agent.quality import check_workflow_invariants
from travel_agent.rag.retriever import KnowledgeRetriever
from travel_agent.rag.vector import DEFAULT_EMBEDDING_MODEL, create_knowledge_retriever


def _configure_stdio_encoding() -> None:
    """Keep CLI input/output predictable for Chinese and English terminals.

    A terminal can inherit a non-UTF-8 locale, or a redirected stream can be
    created with a different encoding.  Python's text streams support a safe
    runtime reconfiguration; streams that do not expose it are left alone.
    """

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # A notebook, test capture, or embedded host may reject runtime
            # reconfiguration.  It should not prevent the workflow from run.
            continue


async def read_user_line(prompt: str) -> str:
    """Read one line, echoing it when input is piped.

    A terminal echoes what the user types, a pipe does not. A recorded
    conversation is unreadable without the questions and answers interleaved,
    so a scripted run echoes the line it consumed.
    """

    value = await asyncio.to_thread(input, prompt)
    if not sys.stdin.isatty():
        print(value, flush=True)
    return value


def friendly_error(error: Exception) -> str:
    """Explain a failure in user terms; the raw cause stays in the run log."""

    name = type(error).__name__
    text = str(error)
    if "Timeout" in name or "timeout" in text.lower():
        return "The model timed out. Retry, or pick a faster --model."
    if "401" in text or "Authentication" in name or "Unauthorized" in text:
        return "The model key was rejected. Check LLM_API_KEY in .env."
    if "429" in text or "RateLimit" in name:
        return "The model is rate limited. Retry later, or set --fallback-model."
    if "Validation" in name:
        return (
            "The model returned a structure that failed validation; it is in "
            "the run log. Retrying or switching models usually resolves it."
        )
    if "Connection" in name or "Connection" in text:
        return "Could not reach the model service. Check the network or LLM_BASE_URL."
    return f"Run failed ({name}). Use --debug for the full error."


def report_gate(outcome) -> None:
    """Tell every user, not only --debug, when an invariant failed.

    A violated contract is the one thing a reader cannot discover from the
    plan itself, so it is printed on the normal path.
    """

    if outcome.regenerated:
        print("(the previous draft failed a deterministic check; regenerated once)")
    if outcome.degraded:
        print("(still failing after regeneration; assembled from tool evidence instead)")
    for check in outcome.failures:
        print(f"! check failed [{check.layer}/{check.category}] {check.name}: {check.message}")


def report_review(plan) -> None:
    """Surface a needs_revision verdict instead of burying it in the page.

    The reviewer's opinion deliberately does not gate the release -- the
    deterministic invariants do that -- but a verdict that only appears as a
    line near the bottom of a long Markdown page is effectively ignored.
    """

    review = getattr(plan, "review", None)
    if review is None or review.status != "needs_revision":
        return
    print(
        "! the read-only reviewer flagged this plan for revision "
        "(not blocking; tool results remain authoritative):"
    )
    for issue in review.issues or ["The reviewer gave no specific issue."]:
        print(f"  - {issue}")


def render_failure_summary(tool_results) -> str:
    """Name the failing step, not just the fact that something failed.

    Every tool call already carries a workflow stage and a normalized error
    category, so the degraded step can be located without reading a provider
    stack trace or the JSONL log.
    """

    failures = [
        record
        for record in getattr(tool_results, "tool_calls", [])
        if record.status in {"error", "unavailable"}
    ]
    if not failures:
        return "[debug] FailurePoints: none (every tool call succeeded or was skipped)"
    lines = ["[debug] FailurePoints:"]
    for record in failures:
        lines.append(
            f"  stage={record.stage} tool={record.tool_name} "
            f"provider={record.provider} status={record.status} "
            f"category={record.error_category or record.error_code} "
            f"retryable={record.retryable}"
        )
    return "\n".join(lines)


def _build_knowledge_retriever(args: argparse.Namespace) -> KnowledgeRetriever | None:
    """Create the local RAG index unless the caller explicitly disables it."""

    if args.no_rag:
        return None
    return create_knowledge_retriever(
        args.knowledge_dir,
        engine=args.rag_engine,
        db_dir=args.rag_db_dir,
        model_name=args.rag_model,
    )


# One natural question per missing field. ``missing_fields`` is already
# computed deterministically by ``TripRequest.required_missing_fields``, so
# asking like a person costs no extra model call; only the wording changes.
CLARIFICATION_QUESTIONS = {
    request_fields.DESTINATION: "Which city do you want to visit?",
    request_fields.ORIGIN: "Which city are you departing from?",
    request_fields.DEPARTURE_DATE: "What date do you leave? (e.g. 2026-10-01)",
    request_fields.CHECK_IN_DATE: "What date do you check in? (e.g. 2026-10-01)",
    request_fields.TRIP_LENGTH_OR_RETURN: "How many days, or when do you return?",
    request_fields.RETURN_OR_TRIP_LENGTH: "How many days, or when do you return?",
    request_fields.TRIP_LENGTH_OR_NIGHTS: "How many nights, or when do you check out?",
    request_fields.NIGHTS_OR_CHECK_OUT: "How many nights, or when do you check out?",
}


def build_clarification_question(request: TripRequest) -> str:
    """Ask for the first missing field in words, not as a field dump."""

    missing = request.missing_fields
    if not missing:
        return "A little more information is needed."
    head = missing[0]
    question = CLARIFICATION_QUESTIONS.get(head, f"Please provide the {head}.")
    if len(missing) > 1:
        question += f" (if convenient, also: {', '.join(missing[1:])})"
    return question


def make_clarification_asker(debug: bool = False):
    """Build the interactive clarification callback used by intake."""

    async def ask_request_clarification(request: TripRequest) -> str:
        if debug:
            # The normalized field dump stays available for teaching and
            # evaluation, but it is no longer the user-facing question.
            print(render_trip_request(request))
        print(f"\n{build_clarification_question(request)}")
        return await read_user_line("> ")

    return ask_request_clarification


SERVICE_TYPE_HINTS = {
    "1": "full trip plan",
    "2": "round-trip flights",
    "3": "hotels",
    "4": "guide and activities",
}


async def ask_initial_request() -> str:
    """Show the service menu, then keep the actual request in natural language."""

    print(render_service_menu())
    selection = await read_user_line(
        "Pick a number, or just describe your trip (Chinese or English): "
    )
    selection = selection.strip()
    if selection not in SERVICE_TYPE_HINTS:
        return selection
    details = await read_user_line("Describe what you need: ")
    return (
        f"Selected service type: {SERVICE_TYPE_HINTS[selection]}\n"
        f"Request: {details.strip()}"
    )


def _read_request_json(value: str) -> str:
    """Read inline JSON, or ``@path`` for a JSON file."""

    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


async def run_offline(args: argparse.Namespace) -> None:
    """Run search tools and rendering without any LLM/API model call."""

    if not args.request_json:
        raise ValueError(
            "--offline requires --request-json; offline mode does not parse "
            "natural-language input."
        )
    request = TripRequest.model_validate_json(_read_request_json(args.request_json))
    memory_store = JsonMemoryStore(args.memory_file) if args.memory_file else None
    checkpoint_store = (
        CheckpointStore(args.checkpoint_file) if args.checkpoint_file else None
    )
    knowledge_retriever = _build_knowledge_retriever(args)
    trace = TraceRecorder(None, run_id=args.run_id)
    if request.status != "ready":
        if checkpoint_store is not None:
            checkpoint_store.save(
                WorkflowCheckpoint.create(
                    args.run_id,
                    "intake",
                    request,
                    original_request=args.request_json,
                )
            )
        print(render_trip_request(request))
        write_run_record(
            args.log_file,
            run_id=args.run_id,
            mode="offline",
            model=None,
            status="needs_clarification",
            request=request,
            trajectory=trace.build_trajectory(),
            trace_id=trace.trace_id,
            metrics=trace.build_metrics(),
            include_input=args.log_input,
        )
        return

    print("Offline mode: no model call; running the typed workflow and free tools only.")
    search_plan = build_search_plan(request)
    if checkpoint_store is not None:
        checkpoint_store.save(
            WorkflowCheckpoint.create(
                args.run_id,
                "intake",
                request,
                original_request=args.request_json,
                search_plan=search_plan,
            )
        )
    tool_results = execute_search_plan(
        search_plan,
        knowledge_retriever=knowledge_retriever,
        run_id=args.run_id,
        trace_id=trace.trace_id,
    )
    if checkpoint_store is not None:
        checkpoint_store.save(
            WorkflowCheckpoint.create(
                args.run_id,
                "searched",
                request,
                original_request=args.request_json,
                search_plan=search_plan,
                search_results=tool_results,
            )
        )
    result = build_offline_trip_plan(request, tool_results)
    if memory_store is not None:
        memory_store.remember_preferences(args.user_id, request.preferences)
    if checkpoint_store is not None:
        checkpoint_store.save(
            WorkflowCheckpoint.create(
                args.run_id,
                "complete",
                request,
                original_request=args.request_json,
                search_plan=search_plan,
                search_results=tool_results,
                final_plan=result,
            )
        )

    write_run_record(
        args.log_file,
        run_id=args.run_id,
        mode="offline",
        model=None,
        status=result.status,
        request=request,
        search_plan=search_plan,
        tool_results=tool_results,
        final_plan=result,
        trajectory=trace.build_trajectory(tool_results),
        trace_id=trace.trace_id,
        metrics=trace.build_metrics(tool_results),
        fallback_used=False,
        include_input=args.log_input,
    )

    if args.debug:
        print("[debug] TripRequest：")
        print(request.model_dump_json(indent=2))
        print("[debug] SearchPlan：")
        print(search_plan.model_dump_json(indent=2))
        print("[debug] ToolCallRecord：")
        print(
            json.dumps(
                [record.model_dump(mode="json") for record in tool_results.tool_calls],
                ensure_ascii=False,
                indent=2,
            )
        )
        print("[debug] WorkflowChecks：")
        for check in check_workflow_invariants(request, search_plan, tool_results, result):
            status = "PASS" if check.passed else "FAIL"
            print(f"[{status}] {check.name}: {check.message}")
        print(render_failure_summary(tool_results))

    if args.output == "json":
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(render_trip_plan(result))


async def main() -> int:
    _configure_stdio_encoding()
    parser = argparse.ArgumentParser(
        description="Run the travel planning agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "A normal run needs no flags. --debug, --offline, --review and the "
            "RAG options are for development, teaching and evaluation. When "
            "several local users share one memory file, override --user-id."
        ),
    )
    common = parser.add_argument_group("common")
    common.add_argument(
        "--mode",
        choices=["auto", "direct", "convert", "text"],
        default=os.getenv("TRIP_OUTPUT_MODE", "auto").lower(),
        help="Model structured-output strategy; leave on auto",
    )
    common.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format; JSON is for programs and evaluation",
    )
    common.add_argument(
        "--user-id",
        default="local-user",
        help="Local user key for memory; override when sharing a file",
    )
    common.add_argument(
        "--no-follow-up",
        action="store_true",
        help="Exit after the plan; no follow-up turns (for scripts and evaluation)",
    )
    common.add_argument(
        "--follow-up",
        action="store_true",
        help="Enter follow-up turns even when input is piped (for recording a conversation)",
    )

    diagnostics = parser.add_argument_group("debugging and evaluation")
    diagnostics.add_argument(
        "--debug",
        action="store_true",
        help="Print the request, SearchPlan, tool records and deterministic checks",
    )
    diagnostics.add_argument(
        "--debug-prompts",
        action="store_true",
        help="Print the dynamic messages and response_format sent to the model",
    )
    diagnostics.add_argument(
        "--offline",
        action="store_true",
        help="Skip the model; requires --request-json",
    )
    diagnostics.add_argument(
        "--request-json",
        help="A structured TripRequest JSON, or @path/to/request.json",
    )
    diagnostics.add_argument(
        "--review",
        action="store_true",
        help="Run one extra read-only reviewer pass after the plan",
    )

    model_options = parser.add_argument_group("model and logging")
    model_options.add_argument(
        "--model",
        help="Override LLM_MODEL for this run",
    )
    model_options.add_argument(
        "--fallback-model",
        help="Fallback model for retryable provider errors",
    )
    model_options.add_argument(
        "--log-file",
        help="Append a redacted JSONL record of each run to this file",
    )
    model_options.add_argument(
        "--log-input",
        action="store_true",
        help="Write the raw user input to the log (off by default)",
    )
    model_options.add_argument(
        "--log-content",
        action="store_true",
        help="Keep a bounded preview of model output in the trajectory",
    )

    state_options = parser.add_argument_group("local state")
    state_options.add_argument(
        "--memory-file",
        help="Optional local JSON memory file; without it nothing is persisted",
    )
    state_options.add_argument(
        "--forget-memory",
        action="store_true",
        help="Delete all stored preferences for this user-id before running",
    )
    state_options.add_argument(
        "--checkpoint-file",
        help="Optional local workflow checkpoint file",
    )
    state_options.add_argument(
        "--resume",
        action="store_true",
        help="Resume a searched checkpoint, or render a complete one",
    )

    rag_options = parser.add_argument_group("RAG")
    rag_options.add_argument(
        "--knowledge-dir",
        default="knowledge",
        help="Local Markdown knowledge directory",
    )
    rag_options.add_argument(
        "--no-rag",
        action="store_true",
        help="Disable knowledge retrieval for this run",
    )
    rag_options.add_argument(
        "--rag-engine",
        choices=["hybrid", "vector", "bm25"],
        default=os.getenv("RAG_ENGINE", "hybrid"),
        help="RAG engine; hybrid by default, falling back to BM25",
    )
    rag_options.add_argument(
        "--rag-model",
        default=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help="Embedding model used by vector/hybrid retrieval",
    )
    rag_options.add_argument(
        "--rag-db-dir",
        default=os.getenv("RAG_DB_DIR", ".local/chroma_rag"),
        help="Chroma vector index directory",
    )
    args = parser.parse_args()

    if args.offline and not args.request_json:
        parser.error("--offline requires --request-json")
    if args.request_json and not args.offline:
        parser.error("--request-json is only valid with --offline")
    if args.resume and not args.checkpoint_file:
        parser.error("--resume requires --checkpoint-file")
    if args.forget_memory and not args.memory_file:
        parser.error("--forget-memory requires --memory-file")
    if (args.log_input or args.log_content) and not args.log_file:
        parser.error("--log-input/--log-content require --log-file")

    args.run_id = new_run_id()

    resume_checkpoint: WorkflowCheckpoint | None = None
    if args.resume:
        try:
            checkpoint = CheckpointStore(args.checkpoint_file).load()
        except Exception as error:
            print(f"Could not read the checkpoint: {type(error).__name__}: {error}")
            return 1
        if checkpoint.phase == "complete" and checkpoint.final_plan is not None:
            if args.output == "json":
                print(
                    json.dumps(
                        checkpoint.final_plan.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(render_trip_plan(checkpoint.final_plan))
            return 0
        if checkpoint.phase != "searched" or checkpoint.search_plan is None or checkpoint.search_results is None:
            print(
                f"The checkpoint is at phase {checkpoint.phase}; resuming needs a "
                "searched or complete checkpoint."
            )
            return 1
        resume_checkpoint = checkpoint

    memory_store = JsonMemoryStore(args.memory_file) if args.memory_file else None
    if memory_store is not None and args.forget_memory:
        memory_store.forget(args.user_id)
    memory = memory_store.get(args.user_id) if memory_store is not None else None
    checkpoint_store = (
        CheckpointStore(args.checkpoint_file) if args.checkpoint_file else None
    )

    if args.offline:
        try:
            await run_offline(args)
        except Exception as error:
            print(f"Offline workflow failed: {friendly_error(error)}")
            return 1
        return 0

    print("Starting the travel agent...")
    primary_model = args.model or os.getenv("LLM_MODEL")
    fallback_model = args.fallback_model or os.getenv("LLM_FALLBACK_MODEL")
    settings = Settings.from_env(output_mode=args.mode, model=args.model)
    pricing = TokenPricing(
        input_usd_per_million=settings.input_price_usd_per_million,
        output_usd_per_million=settings.output_price_usd_per_million,
    )
    pricing_by_model = (
        {model_name: pricing for model_name in (primary_model, fallback_model) if model_name}
        if pricing.input_usd_per_million is not None
        or pricing.output_usd_per_million is not None
        else {}
    )
    trace = TraceRecorder(
        primary_model,
        run_id=args.run_id,
        pricing_by_model=pricing_by_model,
        capture_content=args.log_content,
    )

    def create_with_optional_fallback(factory):
        primary = factory(model=args.model)
        if not fallback_model or fallback_model == primary_model:
            return primary
        return FallbackAgent(
            primary,
            factory(model=fallback_model),
            fallback_model,
            primary_model=primary_model,
        )

    agent = create_with_optional_fallback(create_travel_agent)
    intake_agent = create_with_optional_fallback(create_intake_agent)
    # ``auto`` uses the compact TripNarrative response and does not need a
    # second formatter model. ``direct`` intentionally exercises the full
    # TripPlan structured-output contract. Keep the formatter only for legacy
    # ``convert`` mode, where it is still the requested two-stage workflow.
    formatter = (
        create_with_optional_fallback(create_trip_formatter)
        if settings.output_mode == "convert"
        else None
    )
    reviewer = (
        create_with_optional_fallback(create_trip_reviewer)
        if args.review
        else None
    )
    print("Agent ready.")

    request = (
        resume_checkpoint.original_request
        or resume_checkpoint.request.model_dump_json(ensure_ascii=False)
        if resume_checkpoint is not None
        else await ask_initial_request()
    )
    # Everything the user actually typed, in order. Citations are checked
    # against this, so a fact given in any turn stays supported in later ones.
    user_turns: list[str] = [request]
    trip_request = None
    search_plan = None
    tool_results = None

    prompt_observer = None
    if args.debug_prompts:
        def prompt_observer(stage, prompt, options):
            option_names = {
                key: getattr(value, "__name__", repr(value))
                for key, value in (options or {}).items()
            }
            print(f"\n[debug] LLM stage={stage}")
            print(f"[debug] options={option_names}")
            print("[debug] user_message:")
            print(prompt)

    print("Calling the model, please wait...")

    try:
        if resume_checkpoint is not None:
            print("Resuming from a searched checkpoint; skipping intake and tools...")
            trip_request = resume_checkpoint.request
            search_plan = resume_checkpoint.search_plan
            tool_results = resume_checkpoint.search_results
        else:
            # Every clarification answer is part of what the user said, so it
            # has to join the text that citations are checked against. Without
            # this, an origin supplied in a clarification round looks uncited
            # on the next turn and is cleared.
            asker = make_clarification_asker(args.debug)

            async def ask_and_record(current: TripRequest) -> str:
                answer = await asker(current)
                if answer.strip():
                    user_turns.append(answer.strip())
                return answer

            trip_request, _ = await collect_trip_request(
                intake_agent,
                request,
                ask_clarification=ask_and_record,
                on_prompt=prompt_observer,
                on_trace=trace.record_llm,
            )
        if args.debug:
            print("\n[debug] raw input received:")
            print(repr(request))
            print("[debug] TripRequest：")
            print(trip_request.model_dump_json(indent=2))
        if trip_request.status != "ready":
            if checkpoint_store is not None:
                checkpoint_store.save(
                    WorkflowCheckpoint.create(
                        args.run_id,
                        "intake",
                        trip_request,
                        original_request=request,
                    )
                )
            print(render_trip_request(trip_request))
            write_run_record(
                args.log_file,
                run_id=args.run_id,
                mode=settings.output_mode,
                model=primary_model,
                status="needs_clarification",
                request_text=request,
                request=trip_request,
                trajectory=trace.build_trajectory(tool_results),
                trace_id=trace.trace_id,
                metrics=trace.build_metrics(tool_results),
                include_input=args.log_input,
            )
            return 0
        if resume_checkpoint is None:
            search_plan = build_search_plan(trip_request)
            if checkpoint_store is not None:
                checkpoint_store.save(
                    WorkflowCheckpoint.create(
                        args.run_id,
                        "intake",
                        trip_request,
                        original_request=request,
                        search_plan=search_plan,
                    )
                )
            if args.debug:
                print("[debug] SearchPlan：")
                print(search_plan.model_dump_json(indent=2))
            tool_results = execute_search_plan(
                search_plan,
                knowledge_retriever=_build_knowledge_retriever(args),
                run_id=args.run_id,
                trace_id=trace.trace_id,
            )
            if checkpoint_store is not None:
                checkpoint_store.save(
                    WorkflowCheckpoint.create(
                        args.run_id,
                        "searched",
                        trip_request,
                        original_request=request,
                        search_plan=search_plan,
                        search_results=tool_results,
                    )
                )
        if search_plan is None or tool_results is None:
            raise RuntimeError("the checkpoint has no SearchPlan or SearchResults")
        if args.debug:
            print("[debug] ToolCallRecord：")
            print(
                json.dumps(
                    [record.model_dump(mode="json") for record in tool_results.tool_calls],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        async def run_planner(
            current_request: TripRequest,
            current_plan,
            current_results,
            planner_text: str,
            feedback: str | None = None,
        ):
            """Run only the planner stage over existing typed tool evidence."""

            prompt_builder = (
                build_trip_narrative_prompt
                if settings.output_mode == "auto"
                else build_planner_prompt
            )
            planner_request = prompt_builder(
                planner_text,
                current_request,
                current_plan,
                current_results,
                memory=memory,
            )
            if feedback:
                # A retry must carry what was wrong; at temperature 0 an
                # identical prompt reproduces the identical bad plan.
                planner_request = f"{planner_request}\n\n{feedback}"
            if args.review:
                if reviewer is None or settings.output_mode == "text":
                    raise ValueError("--review requires a structured output mode")
                return await run_plan_review_workflow(
                    planner=agent,
                    reviewer=reviewer,
                    planner_prompt=planner_request,
                    request=current_request,
                    search_plan=current_plan,
                    search_results=current_results,
                    formatter=formatter,
                    mode=settings.output_mode,
                    on_prompt=prompt_observer,
                    on_trace=trace.record_llm,
                )
            return await plan_trip_structured(
                agent,
                planner_request,
                formatter=formatter,
                mode=settings.output_mode,
                tools=[],
                request_context=current_request,
                search_results=current_results,
                on_prompt=prompt_observer,
                on_trace=trace.record_llm,
            )

        async def planned(
            current_request: TripRequest, current_plan, current_results, planner_text: str
        ):
            """Plan, then let the deterministic invariants gate the result."""

            if settings.output_mode == "text":
                return await run_planner(
                    current_request, current_plan, current_results, planner_text
                )
            outcome = await plan_with_invariant_gate(
                lambda feedback: run_planner(
                    current_request,
                    current_plan,
                    current_results,
                    planner_text,
                    feedback,
                ),
                current_request,
                current_plan,
                current_results,
            )
            report_gate(outcome)
            report_review(outcome.plan)
            return outcome.plan

        result = await planned(trip_request, search_plan, tool_results, request)
        if memory_store is not None and isinstance(result, TripPlan):
            memory_store.remember_preferences(
                args.user_id, durable_preferences(trip_request)
            )
        if checkpoint_store is not None and isinstance(result, TripPlan):
            checkpoint_store.save(
                WorkflowCheckpoint.create(
                    args.run_id,
                    "complete",
                    trip_request,
                    original_request=request,
                    search_plan=search_plan,
                    search_results=tool_results,
                    final_plan=result,
                )
            )
    except Exception as error:
        print(friendly_error(error))
        if args.debug:
            print(f"[debug] {type(error).__name__}: {error}")
        write_run_record(
            args.log_file,
            run_id=args.run_id,
            mode=args.mode or os.getenv("TRIP_OUTPUT_MODE", "auto"),
            model=primary_model,
            status="error",
            request_text=request,
            request=trip_request,
            search_plan=search_plan,
            tool_results=tool_results,
            trajectory=trace.build_trajectory(tool_results),
            trace_id=trace.trace_id,
            metrics=trace.build_metrics(tool_results),
            error=f"{type(error).__name__}: {error}",
            fallback_used=any(
                getattr(item, "used_fallback", False)
                for item in (agent, intake_agent, formatter, reviewer)
                if item is not None
            ),
            include_input=args.log_input,
        )
        return 1

    print("Model responded.")
    write_run_record(
        args.log_file,
        run_id=args.run_id,
        mode=settings.output_mode,
        model=(fallback_model if getattr(agent, "used_fallback", False) else primary_model),
        status=(result.status if isinstance(result, TripPlan) else "complete"),
        request_text=request,
        request=trip_request,
        search_plan=search_plan,
        tool_results=tool_results,
        final_plan=result if isinstance(result, TripPlan) else None,
        trajectory=trace.build_trajectory(tool_results),
        trace_id=trace.trace_id,
        metrics=trace.build_metrics(tool_results),
        fallback_used=any(
            getattr(item, "used_fallback", False)
            for item in (agent, intake_agent, formatter, reviewer)
            if item is not None
        ),
        include_input=args.log_input,
    )
    def show(plan: TripPlan) -> None:
        if args.debug:
            print("[debug] WorkflowChecks：")
            for check in check_workflow_invariants(
                trip_request, search_plan, tool_results, plan
            ):
                status = "PASS" if check.passed else "FAIL"
                print(f"[{status}] {check.name}: {check.message}")
            print(render_failure_summary(tool_results))
        if args.output == "json":
            print(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            print(render_trip_plan(plan))

    if settings.output_mode == "text":
        print(format_plan(result.text))
        return 0
    # Structured modes are intentionally strict: a text fallback here
    # would make downstream JSON/database/evaluation code unreliable.
    if not isinstance(result, TripPlan):
        print("The structured workflow returned something other than a TripPlan.")
        return 1
    show(result)

    # The loop normally needs a terminal, so a piped eval run does not sit
    # waiting on input. ``--follow-up`` is the explicit opt-in that lets a
    # script drive the conversation through an ordinary pipe.
    if args.no_follow_up or not (args.follow_up or sys.stdin.isatty()):
        return 0

    # Follow-up turns. The plan, the request, and the typed SearchResults stay
    # in memory, which is exactly the state a ``searched`` checkpoint holds.
    # A turn that changes no tool query therefore costs one planner call and
    # no external request at all.
    router = create_with_optional_fallback(create_follow_up_router)

    async def revise_request(latest: str):
        """Re-extract the request from every user turn, newest taking priority."""

        spoken = "\n".join(f"{i}. {t}" for i, t in enumerate(user_turns, 1))
        revised = await extract_trip_request(
            intake_agent,
            "Everything the user has said, in order (user text only):\n"
            f"{spoken}\n\n"
            f"The user's latest change:\n{latest}\n\n"
            "Extract fields only from the user text above. Where a later turn "
            "conflicts with an earlier one, the latest wins.",
            on_prompt=prompt_observer,
            on_trace=trace.record_llm,
        )
        revised = merge_trip_requests(trip_request, revised)
        return apply_evidence_guards(revised, "\n".join(user_turns))

    turn = 0
    while True:
        try:
            answer = (
                await read_user_line(
                    "\nAnything to change? (e.g. make it 4 days / more detail "
                    "on day two / why isn't X scheduled). Chinese or English. "
                    "Press Enter to finish.\n> "
                )
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not answer:
            return 0
        turn += 1
        user_turns.append(answer)

        if looks_like_end(answer):
            return 0
        intent = await classify_follow_up(
            router, answer, on_prompt=prompt_observer, on_trace=trace.record_llm
        )
        if args.debug:
            print(f"[debug] follow-up action={intent.action} reason={intent.reason}")
        if intent.action == "end":
            return 0
        if intent.action == "out_of_scope":
            print(
                "This demo does not book, pay, or rebook. You can change the "
                "length, dates, city, budget, or scope, or ask for a day in "
                "more detail."
            )
            continue

        # Follow-up changes are transactional: until a replacement plan is
        # ready, keep the previous request, evidence and plan as one snapshot.
        previous_state = (request, trip_request, search_plan, tool_results, result)
        turn_error = None
        failed_state = None
        try:
            if intent.action == "ask_about_plan":
                print(
                    "\n"
                    + await answer_plan_question(
                        agent,
                        answer,
                        trip_request,
                        tool_results,
                        result,
                        on_prompt=prompt_observer,
                        on_trace=trace.record_llm,
                    )
                )
                continue

            if intent.action == "refine_narrative":
                # Facts are unchanged, so no tool runs again.
                result = await planned(
                    trip_request,
                    search_plan,
                    tool_results,
                    f"{request}\n\nAdditional presentation request from the user: {answer}",
                )
                if not isinstance(result, TripPlan):
                    print("The structured workflow returned something other than a TripPlan.")
                    return 1
                show(result)
                continue

            # modify_request: re-extract, then merge with the same
            # stated_fields rule the clarification rounds use.
            request = f"{request}\n\nUser added: {answer}"
            revised = await revise_request(answer)
            rounds = 0
            while revised.status != "ready" and rounds < 2:
                # A modification that leaves the request incomplete needs a
                # real question with a real answer. Printing the question and
                # continuing dropped the whole turn: the next thing the user
                # typed was read as a new follow-up, not as the answer.
                rounds += 1
                print(f"\n{build_clarification_question(revised)}")
                try:
                    reply = (await read_user_line("> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if not reply:
                    break
                user_turns.append(reply)
                request = f"{request}\n\nUser added: {reply}"
                revised = await revise_request(reply)
            if revised.status != "ready":
                request = previous_state[0]
                print("Still incomplete, so this change was not applied; the plan is unchanged.")
                continue

            next_plan = build_search_plan(revised)
            if next_plan == search_plan:
                # SearchPlan compares by value: an unchanged query set means
                # the existing evidence is still exactly right.
                print("(query unchanged; reusing the existing tool results)")
            else:
                tool_results = execute_search_plan(
                    next_plan,
                    knowledge_retriever=_build_knowledge_retriever(args),
                    run_id=args.run_id,
                    trace_id=trace.trace_id,
                )
            trip_request, search_plan = revised, next_plan
            result = await planned(
                trip_request, search_plan, tool_results, request
            )
            if not isinstance(result, TripPlan):
                print("The structured workflow returned something other than a TripPlan.")
                return 1
            show(result)
            if checkpoint_store is not None:
                checkpoint_store.save(
                    WorkflowCheckpoint.create(
                        args.run_id,
                        "complete",
                        trip_request,
                        original_request=request,
                        search_plan=search_plan,
                        search_results=tool_results,
                        final_plan=result,
                    )
                )
        except Exception as error:
            # One failed follow-up must not discard a plan the user already has.
            failed_state = (request, trip_request, search_plan, tool_results)
            request, trip_request, search_plan, tool_results, result = previous_state
            turn_error = f"{type(error).__name__}: {error}"
            print(f"\nThis turn could not be completed: {friendly_error(error)}")
            if args.debug:
                print(f"[debug] {type(error).__name__}: {error}")
            continue
        finally:
            # The conversation rolls back, but the failed attempt's evidence
            # must remain auditable rather than disappearing with that state.
            log_text, log_request, log_plan, log_results = failed_state or (
                request, trip_request, search_plan, tool_results
            )
            write_run_record(
                args.log_file,
                run_id=args.run_id,
                mode=settings.output_mode,
                model=primary_model,
                status=(
                    "error" if turn_error is not None
                    else result.status if isinstance(result, TripPlan) else "complete"
                ),
                error=turn_error,
                request_text=log_text,
                request=log_request,
                search_plan=log_plan,
                tool_results=log_results,
                final_plan=result if turn_error is None and isinstance(result, TripPlan) else None,
                trajectory=trace.build_trajectory(log_results),
                trace_id=trace.trace_id,
                metrics=trace.build_metrics(log_results),
                fallback_used=any(
                    getattr(item, "used_fallback", False)
                    for item in (agent, intake_agent, formatter, reviewer, router)
                    if item is not None
                ),
                include_input=args.log_input,
            )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
