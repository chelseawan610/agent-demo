"""Optional semantic retrieval backed by Sentence Transformers and Chroma.

The imports are intentionally lazy.  The default BM25 workflow stays
dependency-free, while ``--rag-engine vector`` or ``hybrid`` opts into the
embedding model and persistent vector database.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from travel_agent.rag.models import KnowledgeHit
from travel_agent.rag.retriever import KnowledgeRetriever, MarkdownKnowledgeBase, _Chunk, split_markdown


DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"


class SentenceTransformerEmbedder:
    """Encode retrieval queries and passages with a local multilingual model."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise RuntimeError(
                    "向量 RAG 需要可选依赖，请运行 `uv sync --extra rag-vector`。"
                ) from error
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _encode(self, values: list[str], prefix: str) -> list[list[float]]:
        model = self._load()
        encoded = model.encode(
            [f"{prefix}{value}" for value in values],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return encoded.tolist()

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "passage: ")

    def encode_query(self, query: str) -> list[float]:
        return self._encode([query], "query: ")[0]


def _load_chunks(root: str | Path) -> list[_Chunk]:
    root_path = Path(root)
    if not root_path.exists() or not root_path.is_dir():
        return []
    chunks: list[_Chunk] = []
    for path in sorted(root_path.glob("*.md")):
        if path.is_file():
            chunks.extend(split_markdown(path))
    return chunks


class ChromaKnowledgeBase:
    """Persistent Chroma collection containing embedded Markdown chunks."""

    def __init__(
        self,
        root: str | Path,
        *,
        db_dir: str | Path = ".local/chroma_rag",
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        collection_name: str = "travel_knowledge",
    ):
        try:
            import chromadb
        except ImportError as error:
            raise RuntimeError(
                "向量 RAG 需要可选依赖，请运行 `uv sync --extra rag-vector`。"
            ) from error

        self.root = Path(root)
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.provider = f"Chroma + {model_name}"
        self._chunks = _load_chunks(self.root)
        self._embedder = SentenceTransformerEmbedder(model_name)
        self._state_path = self.db_dir / f"{collection_name}.state.json"
        self._client = chromadb.PersistentClient(path=str(self.db_dir))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._sync_index()

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def _signature(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.model_name.encode("utf-8"))
        for chunk in self._chunks:
            digest.update(chunk.chunk_id.encode("utf-8"))
            digest.update(chunk.text.encode("utf-8"))
        return digest.hexdigest()

    def _sync_index(self) -> None:
        current_ids = [chunk.chunk_id for chunk in self._chunks]
        signature = self._signature()
        state: dict[str, Any] = {}
        if self._state_path.exists():
            try:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
        if (
            state.get("signature") == signature
            and state.get("ids") == current_ids
            and self._collection.count() == len(current_ids)
        ):
            return

        old_ids = state.get("ids", [])
        if old_ids:
            self._collection.delete(ids=old_ids)
        if current_ids:
            embeddings = self._embedder.encode_documents([chunk.text for chunk in self._chunks])
            self._collection.upsert(
                ids=current_ids,
                embeddings=embeddings,
                documents=[chunk.text for chunk in self._chunks],
                metadatas=[
                    {
                        "source": chunk.source,
                        "title": chunk.title,
                        "section": chunk.section,
                    }
                    for chunk in self._chunks
                ],
            )
        self._state_path.write_text(
            json.dumps(
                {"signature": signature, "ids": current_ids},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        max_chars: int = 4500,
    ) -> list[KnowledgeHit]:
        if not query.strip() or not self._chunks:
            return []
        result = self._collection.query(
            query_embeddings=[self._embedder.encode_query(query)],
            n_results=min(max(top_k, 1), 10, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[KnowledgeHit] = []
        used_chars = 0
        for index, chunk_id in enumerate(ids):
            distance = float(distances[index]) if index < len(distances) else 1.0
            score = 1.0 - distance
            if score < min_score:
                continue
            metadata = metadatas[index] or {}
            text = documents[index] if index < len(documents) else ""
            if hits and used_chars + len(text) > max_chars:
                break
            hits.append(
                KnowledgeHit(
                    chunk_id=str(chunk_id),
                    source=str(metadata.get("source", "unknown")),
                    title=str(metadata.get("title", "")),
                    section=str(metadata.get("section", "")),
                    text=text,
                    score=round(score, 4),
                    matched_terms=[],
                )
            )
            used_chars += len(text)
        return hits


class HybridKnowledgeBase:
    """Fuse lexical and semantic rankings without mixing their score scales."""

    provider = "hybrid BM25 + vector"

    def __init__(self, lexical: KnowledgeRetriever, semantic: KnowledgeRetriever):
        self.lexical = lexical
        self.semantic = semantic

    @property
    def chunk_count(self) -> int:
        return getattr(self.lexical, "chunk_count", 0)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        max_chars: int = 4500,
    ) -> list[KnowledgeHit]:
        candidate_k = min(max(top_k * 2, top_k), 10)
        lexical_hits = self.lexical.search(
            query, top_k=candidate_k, min_score=min_score, max_chars=max_chars
        )
        semantic_hits = self.semantic.search(
            query, top_k=candidate_k, min_score=min_score, max_chars=max_chars
        )
        by_id: dict[str, KnowledgeHit] = {}
        fused: dict[str, float] = {}
        for rank, hit in enumerate(lexical_hits, start=1):
            by_id[hit.chunk_id] = hit
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1 / (60 + rank)
        for rank, hit in enumerate(semantic_hits, start=1):
            by_id.setdefault(hit.chunk_id, hit)
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1 / (60 + rank)
        ordered = sorted(
            by_id,
            key=lambda chunk_id: (-fused[chunk_id], chunk_id),
        )
        hits: list[KnowledgeHit] = []
        used_chars = 0
        for chunk_id in ordered[: max(0, min(top_k, 10))]:
            hit = by_id[chunk_id]
            if hits and used_chars + len(hit.text) > max_chars:
                break
            hits.append(hit.model_copy(update={"score": round(fused[chunk_id], 6)}))
            used_chars += len(hit.text)
        return hits


def create_knowledge_retriever(
    root: str | Path,
    *,
    engine: str = "hybrid",
    db_dir: str | Path = ".local/chroma_rag",
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> KnowledgeRetriever | None:
    """Build the selected retriever, with hybrid's explicit BM25 fallback."""

    lexical = MarkdownKnowledgeBase(root)
    if lexical.chunk_count == 0:
        return None
    if engine == "bm25":
        return lexical
    try:
        semantic = ChromaKnowledgeBase(
            root,
            db_dir=db_dir,
            model_name=model_name,
        )
    except Exception:
        if engine == "vector":
            raise
        return lexical
    if engine == "vector":
        return semantic
    return HybridKnowledgeBase(lexical, semantic)
