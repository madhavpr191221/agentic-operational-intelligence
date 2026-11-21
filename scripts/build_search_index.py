"""Build the local SQLite/FTS5 and GPU-vector search index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_ops.search_corpus import DEFAULT_DATA_DIR, INDEX_DIR, build_search_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    manifest = build_search_index(args.data_dir, args.index_dir, batch_size=args.batch_size)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
