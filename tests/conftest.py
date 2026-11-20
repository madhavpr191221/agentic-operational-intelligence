import pytest
import hashlib
from pathlib import Path

from agentic_ops.retrieval import LocalRAGModels
from agentic_ops.search_corpus import CorpusSearch, INDEX_DIR


@pytest.fixture(autouse=True)
def disable_paid_embedding_calls(monkeypatch):
    """Keep unit tests deterministic and independent of GPU models or the built archive."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * 384
            for token in text.casefold().split():
                index = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % len(vector)
                vector[index] += 1.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    monkeypatch.setattr(LocalRAGModels, "ensure_gpu", lambda self: setattr(self, "_gpu_name", "test GPU"))
    monkeypatch.setattr(LocalRAGModels, "embed", fake_embed)
    monkeypatch.setattr(LocalRAGModels, "rerank", lambda self, query, passages: [
        len(set(query.casefold().split()) & set(text.casefold().split())) for text in passages
    ])
    original_init = CorpusSearch.__init__

    def isolated_corpus(self, index_dir=INDEX_DIR, models=None, **kwargs):
        if Path(index_dir) == INDEX_DIR:
            index_dir = Path.cwd() / ".data" / "test-index-not-built"
        original_init(self, index_dir=index_dir, models=models, **kwargs)

    monkeypatch.setattr(CorpusSearch, "__init__", isolated_corpus)
