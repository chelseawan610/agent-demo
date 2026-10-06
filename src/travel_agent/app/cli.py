import asyncio
import argparse
import json
import os
from pathlib import Path

from travel_agent.agents.travel import (
    create_travel_agent,
    create_trip_formatter,
    create_trip_reviewer,
)
from travel_agent.agents.intake import create_intake_agent
from travel_agent.app.output import format_plan, render_trip_plan, render_trip_request
from travel_agent.app.run_log import new_run_id, write_run_record
from travel_agent.app.trace import TokenPricing, TraceRecorder
from travel_agent.clients.fallback import FallbackAgent
from travel_agent.config import Settings
from travel_agent.schemas.trip import TripPlan
from travel_agent.schemas.request import TripRequest
from travel_agent.memory.store import JsonMemoryStore
from travel_agent.schemas.checkpoint import WorkflowCheckpoint
from travel_agent.workflows.checkpoint import CheckpointStore
from travel_agent.workflows.intake import collect_trip_request
from travel_agent.workflows.offline import build_offline_trip_plan
from travel_agent.workflows.prompts import build_planner_prompt
from travel_agent.workflows.maf import run_plan_review_workflow
from travel_agent.workflows.trip_planning import plan_trip_structured
from travel_agent.workflows.search import build_search_plan, execute_search_plan
from travel_agent.quality import check_workflow_invariants
from travel_agent.rag.retriever import KnowledgeRetriever
from travel_agent.rag.vector import DEFAULT_EMBEDDING_MODEL, create_knowledge_retriever


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


async def ask_request_clarification(request: TripRequest) -> str:
    """Show missing normalized fields and collect the user's answer."""

    print(render_trip_request(request))
    return await asyncio.to_thread(input, "请补充以上信息（直接回车结束）：")


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

    print("离线模式：不调用 LLM，只执行结构化 workflow 和免费旅行工具。")
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
        status="complete",
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

    if args.output == "json":
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(render_trip_plan(result))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the travel planning agent")
    parser.add_argument(
        "--mode",
        choices=["auto", "direct", "convert", "text"],
        help="Override TRIP_OUTPUT_MODE for this run",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Choose the display format for a validated TripPlan",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the received input, parsed TripRequest, and SearchPlan",
    )
    parser.add_argument(
        "--debug-prompts",
        action="store_true",
        help="Print each user message sent to the LLM and its structured-output options",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the LLM and run the typed workflow from --request-json",
    )
    parser.add_argument(
        "--request-json",
        help="Inline TripRequest JSON, or @path/to/request.json, for --offline",
    )
    parser.add_argument(
        "--model",
        help="Override LLM_MODEL for this run, for example qwen/qwen3.7-flash",
    )
    parser.add_argument(
        "--fallback-model",
        help="Optional model used only after a transient provider failure",
    )
    parser.add_argument(
        "--log-file",
        help="Append one redacted JSON record per run to this JSONL file",
    )
    parser.add_argument(
        "--log-input",
        action="store_true",
        help="Include the raw CLI input in --log-file; omit by default",
    )
    parser.add_argument(
        "--log-content",
        action="store_true",
        help="Include bounded LLM output previews in the trajectory",
    )
    parser.add_argument(
        "--memory-file",
        help="Optional local JSON file for explicit reusable preferences",
    )
    parser.add_argument(
        "--user-id",
        default="local-user",
        help="User key used by --memory-file",
    )
    parser.add_argument(
        "--forget-memory",
        action="store_true",
        help="Delete this user's preferences from --memory-file before running",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Run one optional read-only LLM review after the plan is complete",
    )
    parser.add_argument(
        "--checkpoint-file",
        help="Optional local JSON file for workflow checkpoints",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a searched checkpoint with the planner, or render a completed one",
    )
    parser.add_argument(
        "--knowledge-dir",
        default="knowledge",
        help="Local Markdown knowledge directory used by the RAG retriever",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="Skip local knowledge retrieval for this run",
    )
    parser.add_argument(
        "--rag-engine",
        choices=["hybrid", "vector", "bm25"],
        default=os.getenv("RAG_ENGINE", "hybrid"),
        help="Local RAG engine: hybrid semantic+BM25, vector, or dependency-free bm25",
    )
    parser.add_argument(
        "--rag-model",
        default=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help="Sentence Transformers embedding model used by vector/hybrid RAG",
    )
    parser.add_argument(
        "--rag-db-dir",
        default=os.getenv("RAG_DB_DIR", ".local/chroma_rag"),
        help="Persistent Chroma directory for the local vector index",
    )
    args = parser.parse_args()
    args.run_id = new_run_id()

    resume_checkpoint: WorkflowCheckpoint | None = None
    if args.resume:
        if not args.checkpoint_file:
            print("--resume 需要同时提供 --checkpoint-file")
            return
        try:
            checkpoint = CheckpointStore(args.checkpoint_file).load()
        except Exception as error:
            print(f"读取 checkpoint 失败：{type(error).__name__}: {error}")
            return
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
            return
        if checkpoint.phase != "searched" or checkpoint.search_plan is None or checkpoint.search_results is None:
            print(
                f"checkpoint 当前阶段为 {checkpoint.phase}，需要 searched 或 complete "
                "checkpoint 才能恢复。"
            )
            return
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
            print(f"离线 workflow 失败：{type(error).__name__}: {error}")
        return

    print("正在初始化旅行 Agent...")
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
    formatter = (
        create_with_optional_fallback(create_trip_formatter)
        if settings.output_mode in {"convert", "auto"}
        else None
    )
    reviewer = (
        create_with_optional_fallback(create_trip_reviewer)
        if args.review
        else None
    )
    print("Agent 已就绪。")

    request = (
        resume_checkpoint.original_request
        or resume_checkpoint.request.model_dump_json(ensure_ascii=False)
        if resume_checkpoint is not None
        else input("请输入你的旅行需求：")
    )
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

    print("正在请求模型，请稍候...")

    try:
        if resume_checkpoint is not None:
            print("正在从 searched checkpoint 恢复，跳过需求解析和外部工具调用...")
            trip_request = resume_checkpoint.request
            search_plan = resume_checkpoint.search_plan
            tool_results = resume_checkpoint.search_results
        else:
            trip_request, _ = await collect_trip_request(
                intake_agent,
                request,
                ask_clarification=ask_request_clarification,
                on_prompt=prompt_observer,
                on_trace=trace.record_llm,
            )
        if args.debug:
            print("\n[debug] 程序实际收到的输入：")
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
            return
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
            raise RuntimeError("checkpoint 缺少 SearchPlan 或 SearchResults")
        if args.debug:
            print("[debug] ToolCallRecord：")
            print(
                json.dumps(
                    [record.model_dump(mode="json") for record in tool_results.tool_calls],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        planner_request = build_planner_prompt(
            request,
            trip_request,
            search_plan,
            tool_results,
            memory=memory,
        )
        if args.review:
            if reviewer is None or settings.output_mode == "text":
                raise ValueError("--review requires a structured output mode")
            result = await run_plan_review_workflow(
                planner=agent,
                reviewer=reviewer,
                planner_prompt=planner_request,
                request=trip_request,
                search_plan=search_plan,
                search_results=tool_results,
                formatter=formatter,
                mode=settings.output_mode,
                on_prompt=prompt_observer,
                on_trace=trace.record_llm,
            )
        else:
            result = await plan_trip_structured(
                agent,
                planner_request,
                formatter=formatter,
                mode=settings.output_mode,
                tools=[],
                request_context=trip_request,
                search_results=tool_results,
                on_prompt=prompt_observer,
                on_trace=trace.record_llm,
            )
        if memory_store is not None and isinstance(result, TripPlan):
            memory_store.remember_preferences(args.user_id, trip_request.preferences)
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
        print(f"模型请求失败：{type(error).__name__}: {error}")
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
        return

    print("模型已返回结果。")
    write_run_record(
        args.log_file,
        run_id=args.run_id,
        mode=settings.output_mode,
        model=(fallback_model if getattr(agent, "used_fallback", False) else primary_model),
        status="complete",
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
    if settings.output_mode == "text":
        print(format_plan(result.text))
    else:
        # Structured modes are intentionally strict: a text fallback here
        # would make downstream JSON/database/evaluation code unreliable.
        if not isinstance(result, TripPlan):
            raise TypeError("structured mode returned a non-TripPlan result")
        if args.debug:
            print("[debug] WorkflowChecks：")
            for check in check_workflow_invariants(
                trip_request, search_plan, tool_results, result
            ):
                status = "PASS" if check.passed else "FAIL"
                print(f"[{status}] {check.name}: {check.message}")
        if args.output == "json":
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            print(render_trip_plan(result))


if __name__ == "__main__":
    asyncio.run(main())
