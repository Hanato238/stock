"""Chroma コレクションを chromadb バージョン非依存のダンプに書き出す。

インデックス破損や chromadb の破壊的アップグレードに備えたバックアップ用。
chroma_import.py で API 呼び出しなしに復元できる。

  uv run python scripts/chroma_export.py --out chroma_dump
  make chroma-backup                     # 日付付きディレクトリへ出力（Drive 等へ退避）

出力（--out のプレフィックス。ディレクトリは自動作成）:
  <out>.npz            … ids（文字列配列）と embeddings（float32 行列）
  <out>.jsonl.gz       … 1行1チャンク: {"id", "document", "metadata"}
  <out>.manifest.json  … 作成日時・件数・次元・chromadb バージョン
"""

import argparse
import gzip
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dictionary.store import get_collection

BATCH = 5000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="chroma_dump", help="出力プレフィックス（既定: chroma_dump）")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    col = get_collection()
    total = col.count()
    print(f"collection: {col.name} / {total} chunks")

    ids: list[str] = []
    vectors: list[np.ndarray] = []
    out_jsonl = Path(f"{out}.jsonl.gz")
    with gzip.open(out_jsonl, "wt", encoding="utf-8") as f:
        for offset in range(0, total, BATCH):
            page = col.get(
                limit=BATCH,
                offset=offset,
                include=["embeddings", "documents", "metadatas"],
            )
            for cid, doc, meta in zip(page["ids"], page["documents"], page["metadatas"], strict=True):
                f.write(json.dumps({"id": cid, "document": doc, "metadata": meta}, ensure_ascii=False) + "\n")
            ids.extend(page["ids"])
            vectors.append(np.asarray(page["embeddings"], dtype=np.float32))
            print(f"  {min(offset + BATCH, total)}/{total}")

    embeddings = np.vstack(vectors)
    out_npz = Path(f"{out}.npz")
    np.savez(out_npz, ids=np.asarray(ids, dtype=object), embeddings=embeddings)

    out_manifest = Path(f"{out}.manifest.json")
    manifest = {
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "collection": col.name,
        "count": int(embeddings.shape[0]),
        "dim": int(embeddings.shape[1]),
        "chromadb": version("chromadb"),
        "files": [out_jsonl.name, out_npz.name],
    }
    out_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {out_npz} ({embeddings.shape}) / {out_jsonl} / {out_manifest}")


if __name__ == "__main__":
    main()
