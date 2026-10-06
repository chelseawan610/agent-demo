"""A dependency-free, auditable Markdown BM25 retriever.

The interface is deliberately small. It can later be replaced by an embedding
or hosted search adapter without changing SearchPlan, Planner, or TripPlan.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from travel_agent.rag.models import KnowledgeHit


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]|[\u3040-\u30ff]+")


def tokenize(text: str) -> list[str]:
    """Tokenize common Latin, Chinese, and Japanese text consistently."""

    tokens: list[str] = []
    for raw in _TOKEN_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]", raw):
            tokens.append(raw)
        elif re.fullmatch(r"[\u3040-\u30ff]+", raw):
            tokens.extend(raw[i : i + 2] for i in range(max(1, len(raw) - 1)))
        else:
            tokens.append(raw)
    return tokens


@dataclass(frozen=True)
class _Chunk:
    chunk_id: str
    source: str
    title: str
    section: str
    text: str
    tokens: tuple[str, ...]


class KnowledgeRetriever(Protocol):
    """Stable boundary for BM25, embedding, hosted, or MCP retrievers."""

    provider: str

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        max_chars: int = 4500,
    ) -> list[KnowledgeHit]:
        ...


def split_markdown(path: Path) -> list[_Chunk]:
    """Turn headings and paragraphs into small source-addressable chunks."""

    lines = path.read_text(encoding="utf-8").splitlines()
    title = path.stem.replace("_", " ").strip() or path.name
    section = title
    paragraphs: list[tuple[str, str]] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            text = " ".join(part.strip() for part in current).strip()
            if text:
                paragraphs.append((section, text))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            heading = stripped.lstrip("#").strip()
            if stripped.startswith("# "):
                title = heading or title
            section = heading or section
        elif stripped:
            current.append(stripped)
        else:
            flush()
    flush()

    chunks: list[_Chunk] = []
    for index, (paragraph_section, paragraph) in enumerate(paragraphs, start=1):
        # Keep chunks small enough for a planner prompt while preserving the
        # original source paragraph and its heading.
        pieces = [paragraph[i : i + 900] for i in range(0, len(paragraph), 900)]
        for piece_index, piece in enumerate(pieces, start=1):
            chunk_id = f"{path.name}:{index}:{piece_index}"
            chunks.append(
                _Chunk(
                    chunk_id=chunk_id,
                    source=path.name,
                    title=title,
                    section=paragraph_section,
                    text=piece,
                    tokens=tuple(tokenize(f"{title} {paragraph_section} {piece}")),
                )
            )
    return chunks


class MarkdownKnowledgeBase:
    """Small local BM25 index with deterministic ranking and source metadata."""

    provider = "local markdown BM25"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._chunks = self._load_chunks()
        self._document_frequency: Counter[str] = Counter()
        for chunk in self._chunks:
            self._document_frequency.update(set(chunk.tokens))
        self._average_length = (
            sum(len(chunk.tokens) for chunk in self._chunks) / len(self._chunks)
            if self._chunks
            else 1.0
        )

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def _load_chunks(self) -> list[_Chunk]:
        if not self.root.exists() or not self.root.is_dir():
            return []
        chunks: list[_Chunk] = []
        for path in sorted(self.root.glob("*.md")):
            if path.is_file():
                chunks.extend(split_markdown(path))
        return chunks

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        max_chars: int = 4500,
    ) -> list[KnowledgeHit]:
        """Return ranked chunks; an empty index/query returns no evidence."""

        query_tokens = tokenize(query)
        if not query_tokens or not self._chunks:
            return []
        query_terms = set(query_tokens)
        total_documents = len(self._chunks)
        scored: list[tuple[float, _Chunk, list[str]]] = []
        for chunk in self._chunks:
            frequencies = Counter(chunk.tokens)
            document_length = len(chunk.tokens) or 1
            score = 0.0
            matched: list[str] = []
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue
                matched.append(term)
                document_frequency = self._document_frequency[term]
                idf = math.log(
                    1.0
                    + (total_documents - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                k1 = 1.5
                b = 0.75
                denominator = frequency + k1 * (
                    1.0 - b + b * document_length / self._average_length
                )
                score += idf * frequency * (k1 + 1.0) / denominator
            if score >= min_score and matched:
                scored.append((score, chunk, sorted(matched)))
        scored.sort(key=lambda item: (-item[0], item[1].source, item[1].chunk_id))
        hits: list[KnowledgeHit] = []
        used_chars = 0
        for score, chunk, matched in scored[: max(0, min(top_k, 10))]:
            if hits and used_chars + len(chunk.text) > max_chars:
                break
            hit = KnowledgeHit(
                chunk_id=chunk.chunk_id,
                source=chunk.source,
                title=chunk.title,
                section=chunk.section,
                text=chunk.text,
                score=round(score, 4),
                matched_terms=matched,
            )
            hits.append(hit)
            used_chars += len(chunk.text)
        return hits
