"""Local, GPU-backed text retrieval primitives and source-aware chunking."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Callable, Protocol

from pypdf import PdfReader

from agentic_ops.store import EventStore

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_DIR = ROOT / "data" / "runbooks"
KNOWLEDGE_BASE_DIR = ROOT / "data" / "knowledge_base"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
DEVICE = "cuda:0"
CHUNK_TOKENS = 240
CHUNK_OVERLAP = 36
_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


class Tokenizer(Protocol):
    def encode(self, text: str, **kwargs: Any) -> list[int]: ...
    def decode(self, ids: list[int], **kwargs: Any) -> str: ...


class LocalRAGModels:
    """Lazy Sentence Transformers models that require and report the NVIDIA GPU."""

    def __init__(self, embedding_model: str = EMBEDDING_MODEL,
                 reranker_model: str = RERANKER_MODEL, batch_size: int = 64,
                 rerank_batch_size: int = 16):
        self.embedding_model_name = embedding_model
        self.reranker_model_name = reranker_model
        self.batch_size = batch_size
        self.rerank_batch_size = rerank_batch_size
        self._embedder: Any | None = None
        self._reranker: Any | None = None
        self._tokenizer: Any | None = None
        self._gpu_name: str | None = None

    def ensure_gpu(self) -> None:
        if self._gpu_name is not None:
            return
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "Local search dependencies are missing. Install them with: "
                "uv sync --locked --extra rag-gpu"
            ) from exc
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is not available to PyTorch. Install the CUDA 13.0 build with "
                "uv sync --locked --extra rag-gpu, then check `uv run python scripts/check_gpu.py`."
            )
        self._gpu_name = torch.cuda.get_device_name(0)

    @property
    def gpu_name(self) -> str:
        self.ensure_gpu()
        return str(self._gpu_name)

    @property
    def tokenizer(self) -> Any:
        self.ensure_gpu()
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.embedding_model_name)
        return self._tokenizer

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.ensure_gpu()
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer(self.embedding_model_name, device=DEVICE)
        vectors = self._embedder.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
            device=DEVICE,
        )
        return vectors.tolist()

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        self.ensure_gpu()
        if self._reranker is None:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(self.reranker_model_name, device=DEVICE)
        scores = self._reranker.predict(
            [(query, passage) for passage in passages],
            batch_size=self.rerank_batch_size,
            show_progress_bar=False,
        )
        return [float(score) for score in scores]


def chunk_text(text: str, tokenizer: Tokenizer | None = None,
               max_tokens: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split one semantic section into bounded overlapping chunks."""
    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return []
    if tokenizer is None:
        words = cleaned.split()
        if len(words) <= max_tokens:
            return [cleaned]
        step = max(1, max_tokens - overlap)
        return [" ".join(words[start:start + max_tokens])
                for start in range(0, len(words), step) if words[start:start + max_tokens]]

    token_ids = tokenizer.encode(cleaned, add_special_tokens=False)
    if len(token_ids) <= max_tokens:
        return [cleaned]
    step = max(1, max_tokens - overlap)
    chunks = []
    for start in range(0, len(token_ids), step):
        ids = token_ids[start:start + max_tokens]
        if not ids:
            continue
        value = tokenizer.decode(ids, skip_special_tokens=True).strip()
        if value:
            chunks.append(value)
        if start + max_tokens >= len(token_ids):
            break
    return chunks


def load_runbook_pages(directory: Path = RUNBOOK_DIR,
                       tokenizer: Tokenizer | None = None) -> list[dict[str, Any]]:
    """Extract searchable PDF text with page citations and bounded chunks."""
    pages: list[dict[str, Any]] = []
    for pdf_path in sorted(directory.glob("*.pdf")):
        runbook_id = pdf_path.stem
        reader = PdfReader(str(pdf_path))
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = lines[0] if lines else runbook_id
            for chunk_number, chunk in enumerate(chunk_text(text, tokenizer), start=1):
                pages.append({"evidence_id": _stable_id("pdf", pdf_path.name, page_number, chunk_number),
                              "runbook_id": runbook_id, "title": title,
                              "page_number": page_number, "chunk_number": chunk_number,
                              "source_path": str(pdf_path.relative_to(ROOT)), "text": chunk,
                              "available_at": "2025-12-01T00:00:00Z"})
    return pages


def _markdown_metadata(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    if not lines or lines[0].strip() != "---":
        return {}, lines
    end = next((index for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"), None)
    if end is None:
        return {}, lines
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip().strip("'\"")
    return metadata, lines[end + 1:]


def _stable_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]
    return f"DOC-{digest}"


def load_knowledge_passages(directory: Path = KNOWLEDGE_BASE_DIR,
                            tokenizer: Tokenizer | None = None) -> list[dict[str, Any]]:
    """Load heading-scoped Markdown passages, splitting oversized sections only."""
    passages: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        metadata, lines = _markdown_metadata(lines)
        title = path.stem.replace("_", " ").title()
        breadcrumbs: list[str] = []
        sections: list[tuple[str, str]] = []
        current_heading = title
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_lines
            body = "\n".join(line.strip() for line in current_lines if line.strip()).strip()
            if body:
                sections.append((current_heading, body))
            current_lines = []

        for line in lines:
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                flush()
                level = len(heading.group(1))
                value = heading.group(2).strip()
                if level == 1 and not sections:
                    title = value
                    current_heading = title
                    breadcrumbs = [title]
                    continue
                breadcrumbs = breadcrumbs[:max(0, level - 1)]
                breadcrumbs.append(value)
                current_heading = " / ".join(breadcrumbs)
            else:
                current_lines.append(line)
        flush()

        for section_number, (section_title, body) in enumerate(sections, start=1):
            for chunk_number, chunk in enumerate(chunk_text(body, tokenizer), start=1):
                passages.append({
                    "evidence_id": _stable_id(path.name, section_number, chunk_number),
                    "document_id": metadata.get("document_id", path.stem),
                    "document_type": metadata.get("document_type", "reference"),
                    "title": title,
                    "section_title": section_title,
                    "categories": [item.strip() for item in metadata.get("categories", "").split(",")
                                   if item.strip()],
                    "available_at": metadata.get("available_at", "2025-12-01T00:00:00Z"),
                    "source_path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                    "chunk_number": chunk_number,
                    "text": f"{section_title}\n{chunk}",
                })
    return passages


class HybridRetriever:
    """Fuse keyword and local vector ranks, then rerank the best candidates."""

    def __init__(self, store: EventStore, embedder: Callable[[list[str]], list[list[float]]] | None = None,
                 model: str = EMBEDDING_MODEL, reranker: Callable[[str, list[str]], list[float]] | None = None,
                 candidate_limit: int = 40, models: LocalRAGModels | None = None):
        self.store = store
        self.embedder = embedder
        self.model = model
        self.candidate_limit = candidate_limit
        self.models = (models or LocalRAGModels(embedding_model=model)) if embedder is None else None
        self.reranker = reranker or (self.models.rerank if self.models else None)
        self._tokenizer = None

    @property
    def tokenizer(self) -> Any:
        if self._tokenizer is None and self.models is not None:
            self._tokenizer = self.models.tokenizer
        return self._tokenizer

    @staticmethod
    def _text(record: dict[str, Any]) -> str:
        payload = record.get("payload", {})
        fields = ("title", "body", "reason", "description", "update", "summary", "message",
                  "service", "dependency", "text")
        content = " ".join(str(payload[key]) for key in fields if payload.get(key))
        return content or str(payload)

    def _embedding(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        digests = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        keys = [f"{self.model}:{digest}" for digest in digests]
        cached = self.store.get_cached_embeddings(keys)
        vectors: list[list[float] | None] = [cached.get(key) for key in keys]
        missing = [index for index, vector in enumerate(vectors) if vector is None]
        if missing:
            embedder = self.embedder or (self.models.embed if self.models else None)
            if embedder is None:
                return None
            generated: list[list[float]] = []
            for offset in range(0, len(missing), 128):
                batch_indices = missing[offset:offset + 128]
                generated.extend(embedder([texts[index] for index in batch_indices]))
            cache_rows = []
            for index, vector in zip(missing, generated, strict=True):
                vectors[index] = vector
                cache_rows.append((keys[index], self.model, digests[index], vector))
            self.store.cache_embeddings(cache_rows)
        return [vector or [] for vector in vectors]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        norm_left = math.sqrt(sum(value * value for value in left))
        norm_right = math.sqrt(sum(value * value for value in right))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (norm_left * norm_right)

    def rank(self, records: list[dict[str, Any]], query: str, limit: int = 10,
             include_stages: bool = False) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        if not records:
            return {"fused": [], "reranked": []} if include_stages else []
        if not query.strip():
            ranked_records = records[-max(1, limit):]
            return {"fused": ranked_records, "reranked": ranked_records} if include_stages else ranked_records

        query_tokens = set(_TOKEN.findall(query.casefold()))
        texts = [self._text(record) for record in records]
        lexical = [len(query_tokens & set(_TOKEN.findall(text.casefold()))) / max(1, len(query_tokens))
                   for text in texts]
        vectors = self._embedding([query, *texts])
        semantic = ([max(0.0, self._cosine(vectors[0], vector)) for vector in vectors[1:]]
                    if vectors is not None else [0.0] * len(records))

        lexical_order = sorted(range(len(records)), key=lambda index: (lexical[index], index), reverse=True)
        vector_order = sorted(range(len(records)), key=lambda index: (semantic[index], index), reverse=True)
        lexical_ranks = {index: rank for rank, index in enumerate(lexical_order, start=1) if lexical[index] > 0}
        vector_ranks = {index: rank for rank, index in enumerate(vector_order, start=1) if semantic[index] > 0}
        fused = {index: (1 / (60 + lexical_ranks[index]) if index in lexical_ranks else 0.0)
                 + (1 / (60 + vector_ranks[index]) if index in vector_ranks else 0.0)
                 for index in set(lexical_ranks) | set(vector_ranks)}
        fused_order = sorted(fused, key=lambda index: fused[index], reverse=True)[:max(limit, self.candidate_limit)]
        candidates = []
        for index in fused_order:
            record = dict(records[index])
            record["retrieval"] = {"score": round(fused[index], 6),
                                   "semantic_score": round(semantic[index], 4),
                                   "keyword_score": round(lexical[index], 4),
                                   "embedding_model": self.model if vectors is not None else None,
                                   "candidate_rank": len(candidates) + 1}
            candidates.append(record)

        reranked = candidates
        if self.reranker and candidates:
            rerank_candidates = candidates[:min(self.candidate_limit, len(candidates))]
            rerank_scores = self.reranker(query, [self._text(item) for item in rerank_candidates])
            scored = list(zip(rerank_candidates, rerank_scores, strict=True))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            reranked = []
            for rank, (record, score) in enumerate(scored, start=1):
                item = dict(record)
                item["retrieval"] = {**record["retrieval"], "reranker_score": round(float(score), 6),
                                     "reranker_rank": rank}
                reranked.append(item)
            reranked.extend(candidates[len(rerank_candidates):])

        final = reranked[:max(1, limit)]
        if include_stages:
            return {"fused": candidates[:max(1, limit)], "reranked": final}
        return final
