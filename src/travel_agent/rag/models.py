"""Typed evidence models for local retrieval augmented generation."""

from pydantic import BaseModel, Field


class KnowledgeHit(BaseModel):
    """One ranked, source-addressable chunk returned by the retriever."""

    chunk_id: str
    source: str
    title: str
    section: str
    text: str
    score: float
    matched_terms: list[str] = Field(default_factory=list)


class KnowledgeCitation(BaseModel):
    """Small citation copied into the final plan without the full chunk text."""

    chunk_id: str
    source: str
    title: str
    section: str
    score: float
