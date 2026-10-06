import pytest

from travel_agent.rag.vector import ChromaKnowledgeBase


@pytest.mark.rag_vector
def test_real_vector_index_retrieves_semantic_match(tmp_path):
    pytest.importorskip("chromadb")
    pytest.importorskip("sentence_transformers")
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n## Tokyo\n\nTokyo Tower is a famous landmark.\n\n"
        "## Seoul\n\nSeoul has museums and palaces.\n",
        encoding="utf-8",
    )
    knowledge = ChromaKnowledgeBase(
        tmp_path,
        db_dir=tmp_path / "chroma",
    )

    hits = knowledge.search("东京的地标 Tokyo Tower", top_k=1)

    assert hits
    assert hits[0].section == "Tokyo"
    assert hits[0].source == "guide.md"
