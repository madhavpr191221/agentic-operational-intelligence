"""Evaluate hybrid search with and without local cross-encoder reranking."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from agentic_ops.search_corpus import DEFAULT_DATA_DIR, CorpusSearch
from agentic_ops.retrieval import LocalRAGModels


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _reciprocal_rank(results: list[dict[str, Any]], relevant: set[str]) -> float:
    for rank, record in enumerate(results, start=1):
        if record["evidence_id"] in relevant:
            return 1 / rank
    return 0.0


def _ndcg(results: list[dict[str, Any]], relevant: set[str], limit: int) -> float:
    dcg = sum((1 / math.log2(rank + 1))
              for rank, item in enumerate(results[:limit], start=1)
              if item["evidence_id"] in relevant)
    ideal_count = min(len(relevant), limit)
    ideal = sum(1 / math.log2(rank + 1)
                for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def evaluate(data_dir: Path = DEFAULT_DATA_DIR, limit: int = 10) -> dict[str, Any]:
    query_path = data_dir / "evaluation" / "queries.jsonl"
    queries = _read_jsonl(query_path)
    corpus_manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    models = LocalRAGModels()
    models.ensure_gpu()
    search = CorpusSearch(models=models)
    if not search.ready:
        raise FileNotFoundError("Search index is missing; run scripts/build_search_index.py first.")

    rows: dict[str, list[dict[str, float]]] = {"fused": [], "reranked": []}
    hits: dict[str, int] = {"fused": 0, "reranked": 0}
    for number, query in enumerate(queries, start=1):
        relevant = set(query["relevant_record_ids"])
        fused = search.search(query["query"], cutoff="2026-01-01T00:00:00Z",
                              category=query["category"], limit=limit, rerank=False)
        reranked = search.search(query["query"], cutoff="2026-01-01T00:00:00Z",
                                 category=query["category"], limit=limit, rerank=True)
        for name, results in (("fused", fused), ("reranked", reranked)):
            if any(item["evidence_id"] in relevant for item in results):
                hits[name] += 1
            rows[name].append({"mrr": _reciprocal_rank(results, relevant),
                               "ndcg_at_k": _ndcg(results, relevant, limit)})
        if number % 20 == 0:
            print(f"Evaluated {number}/{len(queries)} queries")

    return {"corpus_seed": corpus_manifest["seed"],
            "query_count": len(queries), "k": limit, "gpu": models.gpu_name,
            "embedding_model": models.embedding_model_name,
            "reranker_model": models.reranker_model_name,
            "metrics": {name: {"hit_rate_at_k": hits[name] / max(1, len(queries)),
                               "mean_reciprocal_rank": mean(item["mrr"] for item in values),
                               f"mean_ndcg_at_{limit}": mean(item["ndcg_at_k"] for item in values)}
                        for name, values in rows.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.data_dir, args.limit)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
