"""Gemini Embedding API ラッパー"""

import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
BATCH_SIZE = 100
MAX_RETRIES = 3

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _client


def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """テキストのリストをベクトル化する（RETRIEVAL_DOCUMENT: 格納用, RETRIEVAL_QUERY: 検索クエリ用）。"""
    embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embeddings.extend(_embed_batch(batch, task_type))
    return embeddings


def _embed_batch(batch: list[str], task_type: str) -> list[list[float]]:
    client = _get_client()
    for attempt in range(MAX_RETRIES):
        try:
            result = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(task_type=task_type),
            )
            return [e.values for e in result.embeddings]
        except Exception:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2**attempt)
