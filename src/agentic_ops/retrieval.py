"""Small, cached hybrid retrieval for synthetic incident evidence."""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

from agentic_ops.store import EventStore

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_DIR = ROOT / "data" / "runbooks"
KNOWLEDGE_BASE_DIR = ROOT / "data" / "knowledge_base"
EMBEDDING_MODEL = "text-embedding-3-small"
_TOKEN = re.compile(r"[a-z0-9]+")


def load_runbook_pages(directory: Path = RUNBOOK_DIR) -> list[dict[str, Any]]:
    """Extract searchable text page by page, retaining the source PDF and page."""
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
            pages.append({"runbook_id": runbook_id, "title": title,
                          "page_number": page_number, "source_path": str(pdf_path.relative_to(ROOT)),
                          "text": text})
    return pages


def load_knowledge_passages(directory: Path = KNOWLEDGE_BASE_DIR) -> list[dict[str, Any]]:
    """Load editable Markdown reference documents as heading-scoped passages."""
    passages: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        metadata: dict[str, str] = {}
        if lines and lines[0].strip() == "---":
            try:
                end = next(index for index, line in enumerate(lines[1:], start=1)
                           if line.strip() == "---")
            except StopIteration:
                end = 0
            if end:
                for line in lines[1:end]:
                    key, separator, value = line.partition(":")
                    if separator:
                        metadata[key.strip()] = value.strip()
                lines = lines[end + 1:]

        title = path.stem.replace("_", " ").title()
        sections: list[tuple[str, list[str]]] = []
        current_title = title
        current_lines: list[str] = []
        for line in lines:
            if line.startswith("# ") and not sections and not current_lines:
                title = line[2:].strip()
                current_title = title
            elif line.startswith("## "):
                if current_lines:
                    sections.append((current_title, current_lines))
                current_title = line[3:].strip()
                current_lines = []
            elif not line.startswith("# "):
                current_lines.append(line)
        if current_lines:
            sections.append((current_title, current_lines))

        for section_title, body_lines in sections:
            body = "\n".join(line.strip() for line in body_lines if line.strip()).strip()
            if not body:
                continue
            passages.append({
                "document_id": metadata.get("document_id", path.stem),
                "document_type": metadata.get("document_type", "reference"),
                "title": title,
                "section_title": section_title,
                "categories": [value.strip() for value in metadata.get("categories", "").split(",")
                               if value.strip()],
                "available_at": metadata.get("available_at", "2025-12-01T00:00:00Z"),
                "source_path": str(path.relative_to(ROOT)),
                "text": body,
            })
    return passages


class HybridRetriever:
    """Blend lexical relevance with cosine similarity; vectors are content-cached in SQLite."""

    def __init__(self, store: EventStore, embedder: Callable[[list[str]], list[list[float]]] | None = None,
                 model: str = EMBEDDING_MODEL):
        self.store = store
        self.embedder = embedder
        self.model = model

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
            embedder = self.embedder
            if embedder is None:
                if not os.getenv("OPENAI_API_KEY"):
                    return None
                from openai import OpenAI

                client = OpenAI()
                embedder = lambda batch: [item.embedding for item in client.embeddings.create(
                    model=self.model, input=batch
                ).data]
            # Keep each request bounded; the current corpus is intentionally small.
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

    def rank(self, records: list[dict[str, Any]], query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not records:
            return []
        query_tokens = set(_TOKEN.findall(query.casefold()))
        texts = [self._text(record) for record in records]
        lexical: list[float] = []
        for text in texts:
            tokens = set(_TOKEN.findall(text.casefold()))
            lexical.append(len(query_tokens & tokens) / max(1, len(query_tokens)))
        vectors = self._embedding([query, *texts])
        semantic = ([max(0.0, self._cosine(vectors[0], vector)) for vector in vectors[1:]]
                    if vectors is not None else [0.0] * len(records))
        if vectors is None:
            scores = lexical
        else:
            scores = [0.55 * sem + 0.45 * lex for sem, lex in zip(semantic, lexical, strict=True)]
        ranked = sorted(range(len(records)), key=lambda i: (scores[i], lexical[i], records[i].get("simulated_at", "")),
                        reverse=True)
        results = []
        for index in ranked:
            if scores[index] <= 0:
                continue
            record = dict(records[index])
            record["retrieval"] = {"score": round(scores[index], 4),
                                   "semantic_score": round(semantic[index], 4),
                                   "keyword_score": round(lexical[index], 4),
                                   "embedding_model": self.model if vectors is not None else None}
            results.append(record)
            if len(results) >= max(1, limit):
                break
        return results
