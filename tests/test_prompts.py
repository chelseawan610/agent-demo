from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.evidence import ToolResult
from travel_agent.rag.models import KnowledgeHit
from travel_agent.memory.store import UserMemory
from travel_agent.workflows.prompts import build_planner_prompt


def test_planner_prompt_contains_one_shared_evidence_packet():
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])
    prompt = build_planner_prompt(
        "东京攻略，不需要机票酒店。",
        request,
        SearchPlan(),
        SearchResults(),
    )

    assert "<user_request>" in prompt
    assert "结构化需求（事实来源）" in prompt
    assert "搜索计划（程序生成" in prompt
    assert "工具结果（唯一事实来源）" in prompt
    assert "不要重新调用工具" in prompt


def test_planner_prompt_treats_memory_as_non_authoritative_context():
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])
    prompt = build_planner_prompt(
        "东京攻略，我想去博物馆。",
        request,
        SearchPlan(),
        SearchResults(),
        memory=UserMemory(
            user_id="u1",
            preferences=["anime"],
            updated_at="2026-01-01T00:00:00Z",
        ),
    )

    assert "anime" in prompt
    assert "不是本次明确要求" in prompt
    assert "结构化需求（事实来源）" in prompt


def test_planner_prompt_contains_rag_evidence_boundary():
    request = TripRequest(destination="Tokyo", requested_services=["guide"])
    results = SearchResults(
        knowledge=ToolResult.ok(
            "local markdown BM25",
            [
                KnowledgeHit(
                    chunk_id="guide.md:1:1",
                    source="guide.md",
                    title="Guide",
                    section="Tokyo",
                    text="Tokyo Tower is a landmark.",
                    score=1.2,
                )
            ],
        )
    )

    prompt = build_planner_prompt(
        "东京攻略",
        request,
        SearchPlan(knowledge_query="Tokyo"),
        results,
    )

    assert "本地知识检索" in prompt
    assert "Tokyo Tower is a landmark." in prompt
    assert "不要把它当成工具返回的实时数据" in prompt
