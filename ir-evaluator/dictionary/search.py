"""検索精度テスト用CLI

使い方:
  uv run python -m dictionary.search "経営コンサルタントの資金使途"
"""

import sys

from .embed import embed_texts
from .store import get_collection


def search(query: str, n_results: int = 5):
    collection = get_collection()
    [query_embedding] = embed_texts([query], task_type="RETRIEVAL_QUERY")
    results = collection.query(query_embeddings=[query_embedding], n_results=n_results)

    for i, (doc, meta, distance) in enumerate(
        zip(results["documents"][0], results["metadatas"][0], results["distances"][0]), 1
    ):
        print(f"--- [{i}] distance={distance:.4f} {meta['code']}_{meta['name']} ({meta['section_name']}) ---")
        print(doc[:300])
        print()


def main():
    if len(sys.argv) < 2:
        print("使い方: uv run python -m dictionary.search \"検索クエリ\"")
        sys.exit(1)
    search(sys.argv[1])


if __name__ == "__main__":
    main()
