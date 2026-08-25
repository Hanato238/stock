"""Chroma DB格納（単一コレクション + メタデータフィルタ方式）"""

import os
from pathlib import Path

import chromadb

DEFAULT_CHROMA_DIR = Path(__file__).resolve().parents[1] / "chroma_db"
COLLECTION_NAME = "review_dictionary"


def get_collection(chroma_dir: Path | None = None):
    chroma_dir = chroma_dir or Path(os.environ.get("CHROMA_DIR", DEFAULT_CHROMA_DIR))
    client = chromadb.PersistentClient(path=str(chroma_dir))
    return client.get_or_create_collection(COLLECTION_NAME)


def existing_ids(collection, ids: list[str]) -> set[str]:
    """既に格納済みのチャンクIDを返す（再実行時のスキップ用）。"""
    if not ids:
        return set()
    result = collection.get(ids=ids, include=[])
    return set(result["ids"])
