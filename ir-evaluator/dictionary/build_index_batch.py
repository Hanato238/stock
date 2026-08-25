"""Gemini Batch API を使った審査辞典RAGパイプライン（Standard APIの半額。結果はすぐには出ない）

1バッチジョブに大量の項目を積むリスクを避けるため、--batch-size件ずつに区切って
「アップロード→OCRバッチ→チャンク分割→Embeddingバッチ→Chroma格納」を繰り返す。
各区切りの完了時点でChromaに格納されるため、途中で止めても再実行時は完了済み分をスキップして再開する。

流れ（1バッチ区切りごと）:
  1. 対象項目のPDFをFiles APIにアップロード
  2. OCRバッチジョブを投入・完了待ち（metadataのcodeで結果と項目を対応付け）
  3. 結果をクリーニング・チャンク分割
  4. Embeddingバッチジョブを投入・完了待ち（チャンク数が多い場合はさらに分割、リスト順で対応付け）
  5. Chromaに格納

使い方:
  uv run python -m dictionary.build_index_batch --limit 5              # 動作確認用
  uv run python -m dictionary.build_index_batch --batch-size 200       # 全件を200件ずつ区切って実行
"""

import argparse
import sys
import time

from google.genai import types

from .batch import get_client, upload_file, wait_for_batch
from .chunk import split_text
from .embed import EMBEDDING_MODEL
from .manifest import DictItem, scan_dictionary
from .ocr import MAX_OUTPUT_TOKENS, OCR_MODEL, OCR_PROMPT, _clean_ocr_text
from .store import get_collection

UPLOAD_RETRIES = 3
EMBED_JOB_CHUNK_SIZE = 2000  # 1バッチジョブあたりの最大リクエスト数


def _upload_with_retry(item: DictItem) -> types.File:
    for attempt in range(UPLOAD_RETRIES):
        try:
            return upload_file(item.pdf_path, mime_type="application/pdf", display_name=item.code)
        except Exception as e:
            if attempt == UPLOAD_RETRIES - 1:
                raise
            print(f"  upload retry {item.code}: {e}", file=sys.stderr)
            time.sleep(2**attempt)


def _run_ocr_batch(items: list[DictItem]) -> dict[str, str]:
    """PDFをアップロードしてOCRバッチジョブを実行し、{code: text}を返す。"""
    print(f"OCR: {len(items)}件をアップロード中...")
    files = {}
    for n, item in enumerate(items, 1):
        files[item.code] = _upload_with_retry(item)
        if n % 100 == 0:
            print(f"  uploaded {n}/{len(items)}")

    ocr_config = types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    inline_requests = [
        types.InlinedRequest(
            contents=[OCR_PROMPT, files[item.code]],
            metadata={"code": item.code},
            config=ocr_config,
        )
        for item in items
    ]

    print(f"OCR: バッチジョブ投入中（{len(inline_requests)}件）...")
    job = get_client().batches.create(
        model=OCR_MODEL,
        src=inline_requests,
        config=types.CreateBatchJobConfig(display_name="dictionary-ocr-batch"),
    )
    job = wait_for_batch(job)

    results = {}
    for resp in job.dest.inlined_responses:
        code = (resp.metadata or {}).get("code")
        if resp.error:
            print(f"  ERROR {code}: {resp.error}", file=sys.stderr)
            continue
        results[code] = resp.response.text or ""
    return results


def _run_embed_batch(records: list[tuple[str, str]]) -> dict[str, list[float]]:
    """[(chunk_id, text), ...] をEmbeddingバッチジョブで処理し、{chunk_id: embedding}を返す（リスト順で対応付け）。
    EmbeddingsBatchJobSource.inlined_requestsは1件のEmbedContentBatchのみを受け付け、
    そのcontentsに複数テキストをまとめて渡す（1ジョブ=1リクエスト=複数テキスト）。
    """
    embeddings = {}
    for start in range(0, len(records), EMBED_JOB_CHUNK_SIZE):
        batch = records[start : start + EMBED_JOB_CHUNK_SIZE]
        texts = [text for _, text in batch]
        print(f"Embedding: バッチジョブ投入中（{len(batch)}件、{start}〜）...")
        request = types.EmbedContentBatch(
            contents=texts,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        job = get_client().batches.create_embeddings(
            model=EMBEDDING_MODEL,
            src=types.EmbeddingsBatchJobSource(inlined_requests=request),
            config=types.CreateEmbeddingsBatchJobConfig(display_name="dictionary-embed-batch"),
        )
        job = wait_for_batch(job)

        responses = job.dest.inlined_embed_content_responses
        for (chunk_id, _), resp in zip(batch, responses, strict=True):
            if resp.error:
                print(f"  ERROR {chunk_id}: {resp.error}", file=sys.stderr)
                continue
            embeddings[chunk_id] = resp.response.embedding.values
    return embeddings


def _process_batch(items: list[DictItem], collection) -> None:
    """1区切り分の項目をOCR→チャンク分割→Embedding→Chroma格納まで処理する。"""
    ocr_results = _run_ocr_batch(items)

    chunk_records: list[tuple[DictItem, list[str]]] = []
    embed_records: list[tuple[str, str]] = []
    for item in items:
        raw = ocr_results.get(item.code)
        if raw is None:
            print(f"skip (OCR失敗): {item.code}_{item.name}")
            continue
        cleaned, _ = _clean_ocr_text(raw)
        chunks = split_text(cleaned)
        if not chunks:
            print(f"warn (空文書): {item.code}_{item.name}")
            continue
        chunk_records.append((item, chunks))
        for i, chunk in enumerate(chunks):
            embed_records.append((f"{item.code}-{i:03d}", chunk))

    print(f"Embedding対象チャンク数: {len(embed_records)}")
    embeddings = _run_embed_batch(embed_records)

    for item, chunks in chunk_records:
        ids = [f"{item.code}-{i:03d}" for i in range(len(chunks))]
        if not all(cid in embeddings for cid in ids):
            print(f"skip (Embedding欠落): {item.code}_{item.name}", file=sys.stderr)
            continue
        metadatas = [
            {
                "code": item.code,
                "name": item.name,
                "volume": item.volume,
                "field": item.field,
                "section_code": item.section_code,
                "section_name": item.section_name,
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]
        collection.add(
            ids=ids,
            embeddings=[embeddings[cid] for cid in ids],
            documents=chunks,
            metadatas=metadatas,
        )
        print(f"indexed: {item.code}_{item.name} ({len(chunks)} chunks)")


def build_index_batch(limit: int | None = None, batch_size: int = 200):
    items = scan_dictionary()
    if limit:
        items = items[:limit]

    collection = get_collection()
    pending = [item for item in items if not collection.get(where={"code": item.code}, limit=1)["ids"]]
    print(f"対象項目数: {len(items)} (未処理: {len(pending)}, バッチサイズ: {batch_size})")
    if not pending:
        return

    num_batches = (len(pending) + batch_size - 1) // batch_size
    for n, start in enumerate(range(0, len(pending), batch_size), 1):
        batch = pending[start : start + batch_size]
        print(f"=== バッチ {n}/{num_batches}（{len(batch)}件） ===")
        _process_batch(batch, collection)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="処理する項目数の上限（動作確認用）")
    parser.add_argument("--batch-size", type=int, default=200, help="1区切りあたりの項目数（デフォルト200）")
    args = parser.parse_args()
    build_index_batch(limit=args.limit, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
