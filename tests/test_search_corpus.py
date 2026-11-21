import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agentic_ops.search_corpus import CorpusSearch, build_search_index


class _WhitespaceTokenizer:
    def encode(self, text, **kwargs):
        return text.split()

    def decode(self, tokens, **kwargs):
        return " ".join(tokens)


class _FakeModels:
    embedding_model_name = "deterministic-test-encoder"
    reranker_model_name = "deterministic-test-reranker"
    tokenizer = _WhitespaceTokenizer()
    gpu_name = "test device"

    def ensure_gpu(self):
        return None

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * 32
            for token in text.casefold().split():
                index = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % len(vector)
                vector[index] += 1
            norm = sum(value * value for value in vector) ** 0.5 or 1
            vectors.append([value / norm for value in vector])
        return vectors

    def rerank(self, query, passages):
        query_tokens = set(query.casefold().split())
        return [float(len(query_tokens & set(passage.casefold().split()))) for passage in passages]


class SearchCorpusTests(unittest.TestCase):
    def test_search_obeys_category_service_and_availability_cutoffs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "archive"
            data_dir.mkdir()
            records = [
                {"evidence_id": "T-OLD", "run_id": "archive", "simulated_at": "2025-10-01T01:00:00Z",
                 "available_at": "2025-10-01T01:00:00Z", "source": "support_tickets",
                 "event_type": "ticket_created", "payload": {"category": "payments",
                 "title": "Payment authorization timed out"}},
                {"evidence_id": "T-LATE", "run_id": "archive", "simulated_at": "2025-10-02T01:00:00Z",
                 "available_at": "2025-10-02T01:00:00Z", "source": "support_tickets",
                 "event_type": "ticket_created", "payload": {"category": "payments",
                 "title": "Payment authorization timed out"}},
                {"evidence_id": "C-PAY", "run_id": "archive", "simulated_at": "2025-10-01T02:00:00Z",
                 "available_at": "2025-10-01T02:00:00Z", "source": "change_log",
                 "event_type": "change_record", "payload": {"category": "payments",
                 "service": "checkout-payments", "summary": "Gateway retry configuration update"}},
                {"evidence_id": "C-CATALOG", "run_id": "archive", "simulated_at": "2025-10-01T03:00:00Z",
                 "available_at": "2025-10-01T03:00:00Z", "source": "change_log",
                 "event_type": "change_record", "payload": {"category": "payments",
                 "service": "catalog-search", "summary": "Gateway retry configuration update"}},
                {"evidence_id": "L-PAY", "run_id": "archive", "simulated_at": "2025-10-01T04:00:00Z",
                 "available_at": "2025-10-01T04:00:00Z", "source": "product_logs",
                 "event_type": "service_log_summary", "payload": {"category": "payments",
                 "service": "checkout-payments", "requests": 100, "errors": 20,
                 "message": "Authorization timeout"}},
            ]
            with (data_dir / "records.jsonl").open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record) + "\n")

            models = _FakeModels()
            index_dir = root / "index"
            manifest = build_search_index(data_dir, index_dir, models=models, batch_size=2)
            self.assertGreaterEqual(manifest["records"], 5)
            self.assertGreaterEqual(manifest["vector_count"], 4)
            search = CorpusSearch(index_dir=index_dir, models=models)
            tickets = search.search("payment authorization timeout", cutoff="2025-10-01T12:00:00Z",
                                    sources=("support_tickets",), category="payments", limit=5)
            self.assertEqual([item["evidence_id"] for item in tickets], ["T-OLD"])

            changes = search.search("gateway retry configuration", cutoff="2025-10-02T00:00:00Z",
                                    sources=("change_log",), category="payments",
                                    service="checkout-payments", limit=5)
            self.assertEqual([item["evidence_id"] for item in changes], ["C-PAY"])

            logs = search.search_structured(cutoff="2025-10-02T00:00:00Z",
                                            sources=("product_logs",), category="payments",
                                            service="checkout-payments")
            self.assertEqual([item["evidence_id"] for item in logs], ["L-PAY"])


if __name__ == "__main__":
    unittest.main()
