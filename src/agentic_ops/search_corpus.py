"""Persistent SQLite/USearch corpus for time-bounded hybrid retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from agentic_ops.retrieval import (
    LocalRAGModels,
    ROOT,
    _stable_id,
    chunk_text,
    load_knowledge_passages,
    load_runbook_pages,
)

DEFAULT_DATA_DIR = ROOT / ".data" / "corpus"
INDEX_DIR = ROOT / ".data" / "search_index"
INDEX_DB = INDEX_DIR / "records.sqlite3"
VECTOR_PATH = INDEX_DIR / "vectors.usearch"
INDEX_MANIFEST = INDEX_DIR / "manifest.json"
_WORDS = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_VECTOR_SOURCES = {"support_tickets", "issue_tracker", "change_log", "runbooks", "knowledge_docs"}


def _tokens(query: str) -> list[str]:
    return list(dict.fromkeys(_WORDS.findall(query.casefold())))


def _text(record: dict[str, Any]) -> str:
    payload = record.get("payload", {})
    fields = ("title", "body", "reason", "description", "summary", "message", "service", "dependency", "text")
    return " ".join(str(payload[key]) for key in fields if payload.get(key)) or str(payload)


def _record_row(record: dict[str, Any], key: int, search_text: str,
                chunk_number: int, vectorized: bool) -> tuple[Any, ...]:
    payload = record.get("payload", {})
    categories = payload.get("categories", [])
    if isinstance(categories, str):
        categories = [value.strip() for value in categories.split(",") if value.strip()]
    category = str(payload.get("category") or (categories[0] if categories else ""))
    available_at = str(record.get("available_at") or record.get("simulated_at") or "")
    return (key, str(record["evidence_id"]), str(record.get("run_id", "synthetic_archive")),
            str(record.get("simulated_at") or available_at), available_at,
            str(record["source"]), str(record.get("event_type", "record")), category,
            json.dumps(categories, ensure_ascii=False), str(payload.get("source_path", "")),
            str(payload.get("section_title", "")), int(chunk_number), int(vectorized),
            json.dumps(payload, ensure_ascii=False, sort_keys=True), search_text)


def _iter_documents(data_dir: Path, models: LocalRAGModels) -> Iterable[dict[str, Any]]:
    records_path = data_dir / "records.jsonl"
    if not records_path.exists():
        raise FileNotFoundError(f"Generated corpus not found: {records_path}. Run scripts/generate_search_corpus.py first.")
    with records_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            source = str(event.get("source", ""))
            text = _text(event)
            vectorizable = source in _VECTOR_SOURCES
            for chunk_number, chunk in enumerate(chunk_text(text, models.tokenizer) if vectorizable else [text], start=1):
                record = dict(event)
                record["chunk_number"] = chunk_number
                record["search_text"] = chunk
                record["vectorized"] = vectorizable
                yield record

    for knowledge_dir in (ROOT / "data" / "knowledge_base", data_dir / "knowledge_base"):
        for passage in load_knowledge_passages(knowledge_dir, models.tokenizer):
            yield {"evidence_id": passage["evidence_id"], "run_id": "synthetic_reference",
                   "simulated_at": passage["available_at"], "available_at": passage["available_at"],
                   "source": "knowledge_docs", "event_type": "document_passage",
                   "payload": passage, "chunk_number": passage.get("chunk_number", 1),
                   "search_text": passage["text"], "vectorized": True}
    for page in load_runbook_pages(ROOT / "data" / "runbooks", models.tokenizer):
        yield {"evidence_id": page["evidence_id"], "run_id": "synthetic_reference",
               "simulated_at": page["available_at"], "available_at": page["available_at"],
               "source": "runbooks", "event_type": "runbook_page",
               "payload": page, "chunk_number": page.get("chunk_number", 1),
               "search_text": page["text"], "vectorized": True}


def build_search_index(data_dir: Path = DEFAULT_DATA_DIR, index_dir: Path = INDEX_DIR,
                       models: LocalRAGModels | None = None,
                       batch_size: int = 64) -> dict[str, Any]:
    """Build a fresh local FTS5 and cosine HNSW index using CUDA embeddings."""
    model_stack = models or LocalRAGModels(batch_size=batch_size)
    model_stack.ensure_gpu()
    import numpy as np
    from usearch.index import Index

    index_dir.mkdir(parents=True, exist_ok=True)
    database_path = index_dir / "records.sqlite3"
    vector_path = index_dir / "vectors.usearch"
    for path in (database_path, vector_path, index_dir / "manifest.json"):
        if path.exists():
            path.unlink()

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript("""
        CREATE TABLE search_records (
            record_key INTEGER PRIMARY KEY,
            evidence_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            simulated_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            source TEXT NOT NULL,
            event_type TEXT NOT NULL,
            category TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            source_path TEXT NOT NULL,
            section_title TEXT NOT NULL,
            chunk_number INTEGER NOT NULL,
            vectorized INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            search_text TEXT NOT NULL
        );
        CREATE INDEX idx_search_records_source_time ON search_records(source, available_at);
        CREATE INDEX idx_search_records_category_time ON search_records(category, available_at);
        CREATE INDEX idx_search_records_evidence ON search_records(evidence_id);
        CREATE VIRTUAL TABLE search_fts USING fts5(record_key UNINDEXED, search_text, source UNINDEXED,
                                                   category UNINDEXED, tokenize='unicode61');
    """)
    ann_index = None
    row_count = vector_count = 0
    batch_records: list[dict[str, Any]] = []
    batch_keys: list[int] = []
    batch_texts: list[str] = []

    def flush_vectors() -> None:
        nonlocal ann_index, vector_count, batch_records, batch_keys, batch_texts
        if not batch_texts:
            return
        vectors = np.asarray(model_stack.embed(batch_texts), dtype=np.float32)
        if ann_index is None:
            ann_index = Index(ndim=int(vectors.shape[1]), metric="cos", connectivity=24,
                              expansion_add=128, expansion_search=96)
        ann_index.add(np.asarray(batch_keys, dtype=np.uint64), vectors, threads=1)
        vector_count += len(batch_keys)
        batch_records, batch_keys, batch_texts = [], [], []

    insert_record = "INSERT INTO search_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    insert_fts = "INSERT INTO search_fts(record_key,search_text,source,category) VALUES(?,?,?,?)"
    try:
        for key, record in enumerate(_iter_documents(data_dir, model_stack), start=1):
            row = _record_row(record, key, record["search_text"], record["chunk_number"], record["vectorized"])
            connection.execute(insert_record, row)
            connection.execute(insert_fts, (key, record["search_text"], record["source"],
                                            row[7]))
            row_count += 1
            if record["vectorized"]:
                batch_records.append(record)
                batch_keys.append(key)
                batch_texts.append(record["search_text"])
                if len(batch_texts) >= batch_size:
                    flush_vectors()
            if row_count % 1000 == 0:
                connection.commit()
                print(f"Indexed metadata for {row_count:,} passages; embedded {vector_count:,}.")
        flush_vectors()
        connection.commit()
        if ann_index is None:
            raise RuntimeError("No vectorizable records were found in the synthetic corpus.")
        ann_index.save(vector_path)
    finally:
        connection.close()

    manifest = {"schema_version": 1, "records": row_count, "vector_count": vector_count,
                "embedding_model": model_stack.embedding_model_name,
                "reranker_model": model_stack.reranker_model_name,
                "vector_dimension": ann_index.ndim, "vector_metric": "cosine",
                "device": "cuda:0", "gpu": model_stack.gpu_name,
                "database": database_path.name, "vectors": vector_path.name}
    (index_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


class CorpusSearch:
    """Search a prebuilt archive using FTS5, HNSW, reciprocal rank fusion and a cross-encoder."""

    def __init__(self, index_dir: Path = INDEX_DIR, models: LocalRAGModels | None = None,
                 lexical_pool: int = 60, vector_pool: int = 60, rerank_pool: int = 30):
        self.index_dir = index_dir
        self.database_path = index_dir / "records.sqlite3"
        self.vector_path = index_dir / "vectors.usearch"
        self.models = models or LocalRAGModels()
        self.lexical_pool = lexical_pool
        self.vector_pool = vector_pool
        self.rerank_pool = rerank_pool
        self._index: Any | None = None
        self._dimension: int | None = None

    @property
    def ready(self) -> bool:
        return self.database_path.exists() and self.vector_path.exists()

    def _load_index(self) -> Any:
        if self._index is None:
            if not self.ready:
                raise FileNotFoundError("The local search index is missing. Build it with scripts/build_search_index.py.")
            from usearch.index import Index

            manifest = json.loads((self.index_dir / "manifest.json").read_text(encoding="utf-8"))
            self._dimension = int(manifest["vector_dimension"])
            self._index = Index(ndim=self._dimension, metric="cos")
            self._index.load(self.vector_path)
        return self._index

    def _filter(self, record: dict[str, Any], cutoff: str | None, start_at: str | None,
                sources: tuple[str, ...] | None, category: str | None,
                service: str | None = None) -> bool:
        if cutoff and record["available_at"] > cutoff:
            return False
        if start_at and record["available_at"] < start_at:
            return False
        if sources and record["source"] not in sources:
            return False
        if category:
            categories = json.loads(record["categories_json"])
            if record["category"] not in {"", category} and category not in categories:
                return False
        if service and str(json.loads(record["payload_json"]).get("service", "")).casefold() != service.casefold():
            return False
        return True

    @staticmethod
    def _as_event(row: sqlite3.Row, retrieval: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        if row["chunk_number"] > 1:
            payload = {**payload, "text": row["search_text"], "chunk_number": row["chunk_number"]}
        elif row["source"] in {"runbooks", "knowledge_docs"}:
            payload = {**payload, "text": row["search_text"], "chunk_number": row["chunk_number"]}
        return {"evidence_id": row["evidence_id"], "run_id": row["run_id"],
                "simulated_at": row["simulated_at"], "source": row["source"],
                "event_type": row["event_type"], "payload": payload,
                "available_at": row["available_at"], "retrieval": retrieval,
                "source_record_id": row["evidence_id"]}

    def search(self, query: str, cutoff: str | None = None, start_at: str | None = None,
               sources: tuple[str, ...] | None = None, category: str | None = None,
               limit: int = 8, rerank: bool = True,
               service: str | None = None) -> list[dict[str, Any]]:
        if not self.ready or not query.strip():
            return []
        index = self._load_index()
        sql_filters: list[str] = []
        params: list[Any] = []
        if cutoff:
            sql_filters.append("r.available_at<=?")
            params.append(cutoff)
        if start_at:
            sql_filters.append("r.available_at>=?")
            params.append(start_at)
        if sources:
            sql_filters.append("r.source IN (" + ",".join("?" for _ in sources) + ")")
            params.extend(sources)
        if category:
            sql_filters.append("(r.category IN ('', ?) OR r.categories_json LIKE ?)")
            params.extend([category, f'%"{category}"%'])
        if service:
            sql_filters.append("r.payload_json LIKE ?")
            params.append(f'%"service": "{service}"%')
        filter_sql = (" AND " + " AND ".join(sql_filters)) if sql_filters else ""

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        rows_by_key: dict[int, sqlite3.Row] = {}
        lexical_keys: list[int] = []
        vector_keys: list[int] = []
        lexical_query = " OR ".join(f'"{token}"' for token in _tokens(query))
        if lexical_query:
            lexical_rows = connection.execute(
                "SELECT r.*, bm25(search_fts) AS lexical_score FROM search_fts "
                "JOIN search_records r ON r.record_key=CAST(search_fts.record_key AS INTEGER) "
                f"WHERE search_fts MATCH ?{filter_sql} ORDER BY lexical_score LIMIT ?",
                [lexical_query, *params, self.lexical_pool],
            ).fetchall()
            for row in lexical_rows:
                key = int(row["record_key"])
                rows_by_key[key] = row
                lexical_keys.append(key)

        import numpy as np

        query_vector = np.asarray(self.models.embed([query])[0], dtype=np.float32)
        candidate_count = min(max(self.vector_pool * 8, 200), max(len(index), 1))
        matches = index.search(query_vector, count=candidate_count, threads=1)
        match_keys = [int(match.key) for match in matches]
        for offset in range(0, len(match_keys), 500):
            keys = match_keys[offset:offset + 500]
            if not keys:
                continue
            placeholders = ",".join("?" for _ in keys)
            rows = connection.execute(f"SELECT * FROM search_records WHERE record_key IN ({placeholders})", keys).fetchall()
            for row in rows:
                if self._filter(row, cutoff, start_at, sources, category, service):
                    rows_by_key[int(row["record_key"])] = row
        connection.close()
        for key in match_keys:
            if key in rows_by_key and rows_by_key[key]["vectorized"]:
                vector_keys.append(key)
            if len(vector_keys) >= self.vector_pool:
                break

        lexical_rank = {key: rank for rank, key in enumerate(lexical_keys, start=1)}
        vector_rank = {key: rank for rank, key in enumerate(vector_keys, start=1)}
        fused_scores = {key: (1 / (60 + lexical_rank[key]) if key in lexical_rank else 0.0)
                        + (1 / (60 + vector_rank[key]) if key in vector_rank else 0.0)
                        for key in set(lexical_rank) | set(vector_rank)}
        ordered_keys = sorted(fused_scores, key=lambda key: fused_scores[key], reverse=True)
        pool = []
        seen_ids: set[str] = set()
        for key in ordered_keys:
            row = rows_by_key[key]
            evidence_id = str(row["evidence_id"])
            if evidence_id in seen_ids:
                continue
            seen_ids.add(evidence_id)
            retrieval = {"rrf_score": round(fused_scores[key], 6),
                         "keyword_rank": lexical_rank.get(key),
                         "vector_rank": vector_rank.get(key),
                         "reranker_rank": None,
                         "embedding_model": self.models.embedding_model_name}
            pool.append(self._as_event(row, retrieval))
            if len(pool) >= max(self.rerank_pool, limit):
                break
        if not pool:
            return []

        if rerank:
            rerank_candidates = pool[:min(self.rerank_pool, len(pool))]
            scores = self.models.rerank(query, [_text(item) for item in rerank_candidates])
            ordered = sorted(zip(rerank_candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True)
            result = []
            for rank, (item, score) in enumerate(ordered, start=1):
                value = dict(item)
                value["retrieval"] = {**item["retrieval"], "reranker_score": round(score, 6),
                                      "reranker_rank": rank}
                result.append(value)
            result.extend(pool[len(rerank_candidates):])
        else:
            result = pool
        return result[:max(1, limit)]

    def search_structured(self, query: str = "", sources: tuple[str, ...] = ("product_logs",),
                          cutoff: str | None = None, start_at: str | None = None,
                          category: str | None = None, service: str | None = None,
                          limit: int = 20) -> list[dict[str, Any]]:
        """Filter structured telemetry and records directly without embedding numeric fields."""
        if not self.database_path.exists():
            return []
        clauses = ["source IN (" + ",".join("?" for _ in sources) + ")"]
        params: list[Any] = list(sources)
        if cutoff:
            clauses.append("available_at<=?")
            params.append(cutoff)
        if start_at:
            clauses.append("available_at>=?")
            params.append(start_at)
        if category:
            clauses.append("(category=? OR categories_json LIKE ?)")
            params.extend([category, f'%"{category}"%'])
        if service:
            clauses.append("payload_json LIKE ?")
            params.append(f'%"service": "{service}"%')
        if query.strip():
            terms = _tokens(query)
            clauses.append("record_key IN (SELECT CAST(record_key AS INTEGER) FROM search_fts WHERE search_fts MATCH ?)")
            params.append(" OR ".join(f'"{term}"' for term in terms))
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM search_records WHERE " + " AND ".join(clauses)
                                  + " ORDER BY available_at DESC LIMIT ?", [*params, max(limit, 1)]).fetchall()
        connection.close()
        return [self._as_event(row, {"retrieval_method": "structured_filter"}) for row in rows]


def load_corpus_records(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict[str, Any]]:
    """Return event records from the generated archive for inspection or evaluation."""
    records_path = data_dir / "records.jsonl"
    if not records_path.exists():
        return []
    with records_path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
