import tempfile
import unittest
from pathlib import Path

from agentic_ops.retrieval import HybridRetriever, chunk_text, load_knowledge_passages, load_runbook_pages
from agentic_ops.store import EventStore


class RetrievalTests(unittest.TestCase):
    def test_chunks_respect_token_limit_and_keep_overlap(self):
        class WhitespaceTokenizer:
            def encode(self, text, **kwargs):
                return text.split()

            def decode(self, tokens, **kwargs):
                return " ".join(tokens)

        chunks = chunk_text("one two three four five six seven eight nine ten",
                            tokenizer=WhitespaceTokenizer(), max_tokens=5, overlap=1)
        self.assertTrue(all(len(chunk.split()) <= 5 for chunk in chunks))
        self.assertEqual(chunks[0].split()[-1], chunks[1].split()[0])

    def test_runbook_pdfs_are_searchable_and_page_numbered(self):
        pages = load_runbook_pages()
        self.assertEqual({page["runbook_id"] for page in pages},
                         {"payments", "orders", "account", "delivery", "incident_response"})
        self.assertTrue(all(page["page_number"] >= 1 and page["text"] for page in pages))
        self.assertTrue(any("queue depth" in page["text"].casefold() for page in pages))
        account_pages = [page for page in pages if page["runbook_id"] == "account"]
        self.assertTrue(account_pages)
        self.assertTrue(any("identity-service" in page["text"].casefold() for page in account_pages))

    def test_editable_knowledge_documents_load_as_locatable_passages(self):
        passages = load_knowledge_passages()
        self.assertEqual({item["document_type"] for item in passages},
                         {"service_guide", "postmortem", "deployment_guide", "deployment_note"})
        self.assertTrue(any("Northstar Gateway" in item["text"] for item in passages))
        self.assertTrue(all(item["source_path"].endswith(".md") and item["section_title"]
                            and item["available_at"] for item in passages))

    def test_hybrid_search_finds_paraphrase_and_caches_vectors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "retrieval.sqlite3")
            calls = []

            def fake_embed(texts):
                calls.append(list(texts))
                vectors = []
                for text in texts:
                    lowered = text.casefold()
                    vectors.append([1.0, 0.0] if any(term in lowered for term in
                                                     ("carrier", "transport", "northstar")) else [0.0, 1.0])
                return vectors

            retriever = HybridRetriever(store, embedder=fake_embed, model="test-embeddings")
            records = [
                {"evidence_id": "relevant", "source": "product_logs", "payload": {"message": "Northstar API rate limited"}},
                {"evidence_id": "irrelevant", "source": "product_logs", "payload": {"message": "Monthly billing export completed"}},
            ]
            first = retriever.rank(records, "transport interruption", 1)
            self.assertEqual(first[0]["evidence_id"], "relevant")
            first_call_count = len(calls)
            second = retriever.rank(records, "transport interruption", 1)
            self.assertEqual(second[0]["evidence_id"], "relevant")
            self.assertEqual(len(calls), first_call_count)


if __name__ == "__main__":
    unittest.main()
