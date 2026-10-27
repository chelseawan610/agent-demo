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
        return "模型响应超时。可以直接重试，或用 --model 换一个更快的模型。"
    if "401" in text or "Authentication" in name or "Unauthorized" in text:
        return "模型密钥无效，请检查 .env 里的 LLM_API_KEY。"
    if "429" in text or "RateLimit" in name:
        return "模型被限流了，稍后重试，或配置 --fallback-model。"
    if "Validation" in name:
        return "模型返回的结构不符合 schema，本次已记录到日志；重试或换模型通常可以解决。"
    if "Connection" in name or "Connection" in text:
        return "无法连接模型服务，请检查网络或 LLM_BASE_URL。"
    return f"运行失败（{name}）。加 --debug 可以看完整信息。"


def report_gate(outcome) -> None:
    """Tell every user, not only --debug, when an invariant failed.

    A violated contract is the one thing a reader cannot discover from the
    plan itself, so it is printed on the normal path.
    """

    if outcome.regenerated:
        print("（上一次生成未通过确定性检查，已重新生成一次）")
    if outcome.degraded:
        print("（重新生成后仍未通过，已改用工具证据直接组装）")
    for check in outcome.failures:
        print(f"⚠ 检查未通过 [{check.layer}/{check.category}] {check.name}：{check.message}")


def report_review(plan) -> None:
    """Surface a needs_revision verdict instead of burying it in the page.

    The reviewer's opinion deliberately does not gate the release -- the
    deterministic invariants do that -- but a verdict that only appears as a
    line near the bottom of a long Markdown page is effectively ignored.
    """

    review = getattr(plan, "review", None)
    if review is None or review.status != "needs_revision":
        return
    print("⚠ 只读复核认为本计划需要修订（不阻止输出，事实仍以工具结果为准）：")
    for issue in review.issues or ["复核未给出具体问题。"]:
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
        return "[debug] FailurePoints：无（所有工具调用成功或被显式跳过）"
    lines = ["[debug] FailurePoints："]
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
    "目的地": "你想去哪个城市？",
    "出发城市": "从哪个城市出发？",
    "出发日期": "哪天出发？（例如 10月1号，或 2026-10-01）",
    "入住日期": "哪天入住？（例如 10月1号）",
    "游玩天数或回程/退房日期": "打算玩几天，或者哪天回来？",
    "回程日期或游玩天数": "打算玩几天，或者哪天回来？",
    "游玩天数或入住晚数/退房日期": "住几晚，或者哪天退房？",
    "入住晚数或退房日期": "住几晚，或者哪天退房？",
}


def build_clarification_question(request: TripRequest) -> str:
    """Ask for the first missing field in words, not as a field dump."""

    missing = request.missing_fields
    if not missing:
        return "还需要补充一点信息。"
    head = missing[0]
    question = CLARIFICATION_QUESTIONS.get(head, f"请补充{head}。")
    if len(missing) > 1:
        question += f"（如果方便，也可以一起说：{'、'.join(missing[1:])}）"
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
    "1": "完整旅行规划",
    "2": "往返机票查询",
    "3": "酒店查询",
    "4": "攻略或活动查询",
}


async def ask_initial_request() -> str:
    """Show the service menu, then keep the actual request in natural language."""

    print(render_service_menu())
    selection = await read_user_line("请选择编号，或直接输入你的旅行需求：")
    selection = selection.strip()
    if selection not in SERVICE_TYPE_HINTS:
        return selection
    details = await read_user_line("请补充具体需求：")
    return (
        f"用户选择服务类型：{SERVICE_TYPE_HINTS[selection]}\n"
        f"用户具体需求：{details.strip()}"
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
            "普通运行直接执行本模块即可；--debug、--offline、--review 和 RAG 参数 "
            "主要用于开发、教学和评测。多个本地用户共用 Memory 文件时，使用 "
            "--user-id 覆盖默认的 local-user。"
        ),
    )
    common = parser.add_argument_group("常用运行参数")
    common.add_argument(
        "--mode",
        choices=["auto", "direct", "convert", "text"],
        default=os.getenv("TRIP_OUTPUT_MODE", "auto").lower(),
        help="模型输出策略；普通运行保持 auto 即可",
    )
    common.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="最终展示格式；JSON 适合程序和评测读取",
    )
    common.add_argument(
        "--user-id",
        default="local-user",
        help="Memory 使用的本地用户键；多人共用时手动覆盖",
    )
    common.add_argument(
        "--no-follow-up",
        action="store_true",
        help="输出计划后立即退出，不进入多轮修改；评测和脚本使用",
    )
    common.add_argument(
        "--follow-up",
        action="store_true",
        help="即使输入不是终端也进入多轮修改；用于脚本化录制对话",
    )

    diagnostics = parser.add_argument_group("调试和评测参数")
    diagnostics.add_argument(
        "--debug",
        action="store_true",
        help="打印需求、SearchPlan、工具记录和确定性检查",
    )
    diagnostics.add_argument(
        "--debug-prompts",
        action="store_true",
        help="打印发送给 LLM 的动态消息和结构化输出选项",
    )
    diagnostics.add_argument(
        "--offline",
        action="store_true",
        help="跳过 LLM；必须同时提供 --request-json",
    )
    diagnostics.add_argument(
        "--request-json",
        help="结构化 TripRequest JSON，或使用 @path/to/request.json",
    )
    diagnostics.add_argument(
        "--review",
        action="store_true",
        help="计划完成后额外运行一次只读 MAF Reviewer",
    )

    model_options = parser.add_argument_group("模型和记录参数")
    model_options.add_argument(
        "--model",
        help="临时覆盖 .env 中的 LLM_MODEL",
    )
    model_options.add_argument(
        "--fallback-model",
        help="主模型发生可重试 provider 错误时使用的备用模型",
    )
    model_options.add_argument(
        "--log-file",
        help="将每次运行的脱敏 JSONL 记录追加到指定文件",
    )
    model_options.add_argument(
        "--log-input",
        action="store_true",
        help="把原始用户输入写入日志；默认关闭以减少隐私风险",
    )
    model_options.add_argument(
        "--log-content",
        action="store_true",
        help="在轨迹中保存有限长度的 LLM 输出预览",
    )

    state_options = parser.add_argument_group("本地状态参数")
    state_options.add_argument(
        "--memory-file",
        help="可选的本地 JSON Memory 文件；不传则不保存长期偏好",
    )
    state_options.add_argument(
        "--forget-memory",
        action="store_true",
        help="运行前删除当前 user-id 的全部偏好",
    )
    state_options.add_argument(
        "--checkpoint-file",
        help="可选的本地 workflow checkpoint 文件",
    )
    state_options.add_argument(
        "--resume",
        action="store_true",
        help="从 searched checkpoint 继续，或直接渲染 complete checkpoint",
    )

    rag_options = parser.add_argument_group("RAG 参数")
    rag_options.add_argument(
        "--knowledge-dir",
        default="knowledge",
        help="本地 Markdown 知识库目录",
    )
    rag_options.add_argument(
        "--no-rag",
        action="store_true",
        help="本次运行关闭知识检索；主要用于对照评测",
    )
    rag_options.add_argument(
        "--rag-engine",
        choices=["hybrid", "vector", "bm25"],
        default=os.getenv("RAG_ENGINE", "hybrid"),
        help="RAG 引擎；默认 hybrid，缺少向量依赖时降级为 BM25",
    )
    rag_options.add_argument(
        "--rag-model",
        default=os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help="vector/hybrid 使用的 embedding 模型",
    )
    rag_options.add_argument(
        "--rag-db-dir",
        default=os.getenv("RAG_DB_DIR", ".local/chroma_rag"),
        help="Chroma 向量索引目录",
    )
    args = parser.parse_args()

    if args.offline and not args.request_json:
        parser.error("--offline 必须同时提供 --request-json")
    if args.request_json and not args.offline:
        parser.error("--request-json 只能和 --offline 一起使用")
    if args.resume and not args.checkpoint_file:
        parser.error("--resume 必须同时提供 --checkpoint-file")
    if args.forget_memory and not args.memory_file:
        parser.error("--forget-memory 必须同时提供 --memory-file")
    if (args.log_input or args.log_content) and not args.log_file:
        parser.error("--log-input/--log-content 必须同时提供 --log-file")

    args.run_id = new_run_id()

    resume_checkpoint: WorkflowCheckpoint | None = None
    if args.resume:
        try:
            checkpoint = CheckpointStore(args.checkpoint_file).load()
        except Exception as error:
            print(f"读取 checkpoint 失败：{type(error).__name__}: {error}")
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
                f"checkpoint 当前阶段为 {checkpoint.phase}，需要 searched 或 complete "
                "checkpoint 才能恢复。"
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
            print(f"离线 workflow 失败：{type(error).__name__}: {error}")
            return 1
        return 0

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
    # ``auto``/``direct`` use the compact TripNarrative response and do not
    # need a second formatter model. Keep the formatter only for legacy
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
    print("Agent 已就绪。")

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

    print("正在请求模型，请稍候...")

    try:
        if resume_checkpoint is not None:
            print("正在从 searched checkpoint 恢复，跳过需求解析和外部工具调用...")
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
                if settings.output_mode in {"auto", "direct"}
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

    print("模型已返回结果。")
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
        print("结构化 workflow 返回了非 TripPlan 结果。")
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
            "用户按时间顺序说过的全部内容（只读取用户原文）：\n"
            f"{spoken}\n\n"
            f"用户最新的修改要求：\n{latest}\n\n"
            "请只从以上用户文字提取字段；若与之前冲突，以最新要求为准。",
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
                    "\n还要调整吗？（例如：改成4天 / 第二天写详细点 / "
                    "某个地方为什么没安排；直接回车结束）\n"
                    "You can also reply in English. Press Enter to finish.\n> "
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
                "这个 demo 不做预订、支付或改签。可以调整天数、日期、城市、预算、"
                "服务范围，或者让我把某一天写得更详细。"
            )
            continue

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
                    f"{request}\n\n用户对表达方式的追加要求：{answer}",
                )
                if not isinstance(result, TripPlan):
                    print("结构化 workflow 返回了非 TripPlan 结果。")
                    return 1
                show(result)
                continue

            # modify_request: re-extract, then merge with the same
            # stated_fields rule the clarification rounds use.
            request = f"{request}\n\n用户补充信息：{answer}"
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
                request = f"{request}\n\n用户补充信息：{reply}"
                revised = await revise_request(reply)
            if revised.status != "ready":
                print("信息仍然不完整，本次修改没有生效；原计划保持不变。")
                continue

            next_plan = build_search_plan(revised)
            if next_plan == search_plan:
                # SearchPlan compares by value: an unchanged query set means
                # the existing evidence is still exactly right.
                print("（查询条件未变，复用已有工具结果）")
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
                print("结构化 workflow 返回了非 TripPlan 结果。")
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
            print(f"\n这一轮没能完成：{friendly_error(error)}")
            if args.debug:
                print(f"[debug] {type(error).__name__}: {error}")
            continue
        finally:
            write_run_record(
                args.log_file,
                run_id=args.run_id,
                mode=settings.output_mode,
                model=primary_model,
                status=(result.status if isinstance(result, TripPlan) else "complete"),
                request_text=request,
                request=trip_request,
                search_plan=search_plan,
                tool_results=tool_results,
                final_plan=result if isinstance(result, TripPlan) else None,
                trajectory=trace.build_trajectory(tool_results),
                trace_id=trace.trace_id,
                metrics=trace.build_metrics(tool_results),
                include_input=args.log_input,
            )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
