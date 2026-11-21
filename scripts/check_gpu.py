"""Verify that local embedding and reranking models run on the configured GPU."""

from __future__ import annotations

import json

from agentic_ops.retrieval import LocalRAGModels


def main() -> None:
    models = LocalRAGModels()
    models.ensure_gpu()
    vectors = models.embed(["payment authorization timeout", "order status update"])
    scores = models.rerank("payment timeout", [
        "Payment authorization timed out during checkout.",
        "The order status has not changed.",
    ])
    print(json.dumps({"cuda_available": True, "gpu": models.gpu_name,
                      "embedding_model": models.embedding_model_name,
                      "embedding_dimension": len(vectors[0]),
                      "reranker_model": models.reranker_model_name,
                      "reranker_scores": scores}, indent=2))


if __name__ == "__main__":
    main()
