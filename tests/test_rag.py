from travel_agent.rag.models import KnowledgeHit
from travel_agent.rag.retriever import MarkdownKnowledgeBase, tokenize
from travel_agent.rag.vector import ChromaKnowledgeBase, HybridKnowledgeBase, create_knowledge_retriever
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan
from travel_agent.workflows.search import build_search_plan, execute_search_plan


def test_tokenizer_supports_chinese_and_english_terms():
    tokens = tokenize("东京塔 Tokyo Tower")

    assert "东" in tokens
    assert "京" in tokens
    assert "tokyo" in tokens
    assert "tower" in tokens


def test_markdown_retriever_preserves_source_and_section(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Local guide\n\n## Tokyo\n\nTokyo Tower is a landmark.\n\n"
        "## Seoul\n\nSeoul has museums.\n",
        encoding="utf-8",
    )
    knowledge = MarkdownKnowledgeBase(tmp_path)

    hits = knowledge.search("Tokyo Tower", top_k=1)

    assert len(hits) == 1
    assert hits[0].source == "guide.md"
    assert hits[0].section == "Tokyo"
    assert "Tokyo Tower" in hits[0].text
    assert hits[0].score > 0


def test_markdown_retriever_scope_excludes_other_destination_sections(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Local guide\n\n## Tokyo\n\nTokyo Tower is a landmark.\n\n"
        "## Seoul\n\nSeoul has museums.\n\n"
        "## Planning principles\n\nGroup nearby places together.\n",
        encoding="utf-8",
    )
    knowledge = MarkdownKnowledgeBase(tmp_path)

    hits = knowledge.search("Tokyo Seoul museums", scope="Seoul", top_k=5)

    assert hits
    assert all(hit.section != "Tokyo" for hit in hits)
    assert any(hit.section == "Seoul" for hit in hits)


def test_bm25_scope_does_not_make_common_term_scores_negative(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n" + "\n\n".join(f"## {city}\n\nTravel advice for {city}." for city in ("Tokyo", "Seoul", "Paris", "Rome")),
        encoding="utf-8",
    )
    hits = MarkdownKnowledgeBase(tmp_path).search("travel", scope="Tokyo")
    assert len(hits) == 1
    assert hits[0].score > 0


def test_retrievers_respect_the_character_budget_even_for_the_first_hit(tmp_path):
    (tmp_path / "guide.md").write_text("# Guide\n\nTravel advice is too long.", encoding="utf-8")
    lexical = MarkdownKnowledgeBase(tmp_path)
    assert lexical.search("travel", max_chars=5) == []
    assert HybridKnowledgeBase(lexical, lexical).search("travel", max_chars=5) == []


def test_vector_search_enforces_top_k_after_scope_filtering_without_loading_models():
    class Embedder:
        def encode_query(self, _query):
            return [0.0]

    class Collection:
        def count(self):
            return 4

        def query(self, **_kwargs):
            return {
                "ids": [["wrong-city", "first", "second", "third"]],
                "documents": [["Seoul", "Tokyo one", "Tokyo two", "Tokyo three"]],
                "metadatas": [[{"section": city} for city in ("Seoul", "Tokyo", "Tokyo", "Tokyo")]],
                "distances": [[0.1, 0.2, 0.3, 0.4]],
            }

    knowledge = object.__new__(ChromaKnowledgeBase)
    knowledge._chunks = [object()] * 4
    knowledge._embedder = Embedder()
    knowledge._collection = Collection()
    hits = knowledge.search("Tokyo", scope="Tokyo", top_k=1)
    assert [hit.chunk_id for hit in hits] == ["first"]
    assert knowledge.search("Tokyo", top_k=0) == []
    assert knowledge.search("Tokyo", top_k=1, max_chars=2) == []


def test_search_plan_builds_rag_query_from_destination_and_preferences():
    request = TripRequest(
        destination="Tokyo",
        days=2,
        requested_services=["activities", "guide"],
        preferences=["Tokyo Tower", "anime"],
    )

    plan = build_search_plan(request)

    assert plan.knowledge_query == "Tokyo Tokyo Tower anime activities guide"
    assert plan.knowledge_scope == "Tokyo"
    assert plan.knowledge_top_k == 5
    assert plan.knowledge_max_chars == 4500


def test_rag_result_is_recorded_as_typed_tool_evidence(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n## Tokyo\n\nTokyo Tower is a landmark.\n",
        encoding="utf-8",
    )
    knowledge = MarkdownKnowledgeBase(tmp_path)

    results = execute_search_plan(
        SearchPlan(knowledge_query="Tokyo Tower"),
        knowledge_retriever=knowledge,
    )

    assert results.knowledge is not None
    assert results.knowledge.status == "ok"
    assert results.knowledge.data
    assert any(record.tool_name == "retrieve_knowledge" for record in results.tool_calls)


class _FakeRetriever:
    provider = "fake"
    chunk_count = 2

    def __init__(self, hits):
        self.hits = hits

    def search(self, query, *, top_k=5, min_score=0.0, max_chars=4500):
        return self.hits[:top_k]


def _hit(chunk_id: str, score: float) -> KnowledgeHit:
    return KnowledgeHit(
        chunk_id=chunk_id,
        source="fixture.md",
        title="Fixture",
        section="Travel",
        text=chunk_id,
        score=score,
    )


def test_hybrid_retriever_fuses_rankings_without_comparing_raw_scores():
    lexical = _FakeRetriever([_hit("lexical-only", 100.0), _hit("shared", 1.0)])
    semantic = _FakeRetriever([_hit("shared", 0.8), _hit("semantic-only", 0.7)])

    hits = HybridKnowledgeBase(lexical, semantic).search("travel", top_k=3)

    assert [hit.chunk_id for hit in hits] == ["shared", "lexical-only", "semantic-only"]
    assert hits[0].score > 0


def test_hybrid_factory_falls_back_to_bm25_when_vector_dependencies_are_unavailable(
    tmp_path, monkeypatch
):
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n## Tokyo\n\nTokyo Tower is a landmark.\n",
        encoding="utf-8",
    )

    import travel_agent.rag.vector as vector_module

    def fail_vector(*args, **kwargs):
        raise RuntimeError("optional vector dependencies are unavailable")

    monkeypatch.setattr(vector_module, "ChromaKnowledgeBase", fail_vector)
    retriever = create_knowledge_retriever(tmp_path, engine="hybrid")

    assert retriever is not None
    assert retriever.provider == "local markdown BM25"
    assert retriever.search("Tokyo Tower")
