"""審査辞典PDF（スキャン画像）をOCR・チャンク分割・ベクトル化してChromaに格納するパイプライン

Standard API・同期並列版。動作確認用として残置しているのみで、実運用では使っていない
（本番投入は `build_index_batch.py` の Gemini Batch API 版で完了済み。コスト半減のため）。

OCR・Embeddingともに GOOGLE_API_KEY が必要（.env に設定）。
OCR/Embedding呼び出し（ネットワーク待ち）はスレッドプールで並列化し、Chromaへの書き込みのみ
メインスレッドで直列に行う（PersistentClientの競合を避けるため）。

使い方:
  uv run python -m dictionary.build_index                     # 全1600項目を処理
  uv run python -m dictionary.build_index --limit 5            # 動作確認用に5項目だけ処理
  uv run python -m dictionary.build_index --dry-run            # OCR・チャンク分割まで行い、Embedding/格納はスキップ
  uv run python -m dictionary.build_index --workers 15          # 並列数を指定（デフォルト8）
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .chunk import split_text
from .embed import embed_texts
from .manifest import DictItem, scan_dictionary
from .ocr import ocr_pdf
from .store import get_collection

DEFAULT_WORKERS = 8


def _process_item(item: DictItem, dry_run: bool):
    """OCR→チャンク分割→（dry_runでなければ）Embeddingまでを行う。ネットワーク呼び出しのみでChromaには触れない。"""
    text = ocr_pdf(item.pdf_path)
    chunks = split_text(text)
    if not chunks or dry_run:
        return chunks, None, None, None

    embeddings = embed_texts(chunks, task_type="RETRIEVAL_DOCUMENT")
    ids = [f"{item.code}-{i:03d}" for i in range(len(chunks))]
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
    return chunks, embeddings, ids, metadatas


def build_index(limit: int | None = None, dry_run: bool = False, workers: int = DEFAULT_WORKERS):
    items = scan_dictionary()
    if limit:
        items = items[:limit]

    collection = None if dry_run else get_collection()

    pending = []
    for item in items:
        if collection is not None and collection.get(where={"code": item.code}, limit=1)["ids"]:
            print(f"skip (既存): {item.code}_{item.name}")
            continue
        pending.append(item)

    print(f"対象項目数: {len(items)} (未処理: {len(pending)}, 並列数: {workers})")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_item, item, dry_run): item for item in pending}
        for n, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                chunks, embeddings, ids, metadatas = future.result()
                if not chunks:
                    print(f"[{n}/{len(pending)}] warn (空文書): {item.code}_{item.name}")
                    continue
                if dry_run:
                    print(f"[{n}/{len(pending)}] {item.code}_{item.name}: {len(chunks)} chunks (dry-run)")
                    continue

                collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
                print(f"[{n}/{len(pending)}] indexed: {item.code}_{item.name} ({len(chunks)} chunks)")
            except Exception as e:
                print(f"[{n}/{len(pending)}] ERROR {item.code}_{item.name}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="処理する項目数の上限（動作確認用）")
    parser.add_argument("--dry-run", action="store_true", help="OCR・チャンク分割まで行い、Embedding/格納は行わない")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="並列実行数（デフォルト8）")
    args = parser.parse_args()

    build_index(limit=args.limit, dry_run=args.dry_run, workers=args.workers)


if __name__ == "__main__":
    main()
