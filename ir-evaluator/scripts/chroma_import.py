"""chroma_export.py のダンプから Chroma コレクションを復元する（API 呼び出しなし）。

  CHROMA_DIR=/tmp/restore uv run python scripts/chroma_import.py --in chroma_dump

<in>.npz と <in>.jsonl.gz を読み、CHROMA_DIR（既定は dictionary/store.py）の
コレクションへ collection.add する。既存の同一 ID は upsert で上書き。
"""

import argparse
import gzip
import json
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dictionary.store import get_collection

BATCH = 5000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="inp", default="chroma_dump", help="入力プレフィックス（既定: chroma_dump）")
    args = parser.parse_args()

    npz_path = Path(f"{args.inp}.npz")
    jsonl_path = Path(f"{args.inp}.jsonl.gz")
    manifest_path = Path(f"{args.inp}.manifest.json")
    if not npz_path.exists() or not jsonl_path.exists():
        sys.exit(f"{npz_path} または {jsonl_path} が見つかりません。")

    if manifest_path.exists():
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        installed = version("chromadb")
        print(f"manifest: {m.get('count')} chunks / dim {m.get('dim')} / chromadb {m.get('chromadb')} (created {m.get('created')})")
        if m.get("chromadb") and m["chromadb"] != installed:
            print(f"  ! ダンプ作成時 chromadb {m['chromadb']} ≠ 現在 {installed}（upsert 復元なので通常は問題なし）")

    data = np.load(npz_path, allow_pickle=True)
    vec_ids = [str(x) for x in data["ids"]]
    embeddings = data["embeddings"]

    docs_by_id: dict[str, str] = {}
    meta_by_id: dict[str, dict] = {}
    with gzip.open(jsonl_path, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            docs_by_id[rec["id"]] = rec["document"]
            meta_by_id[rec["id"]] = rec["metadata"]

    if len(vec_ids) != len(docs_by_id):
        sys.exit(f"件数不一致: npz {len(vec_ids)} vs jsonl {len(docs_by_id)}")

    col = get_collection()
    print(f"collection: {col.name} / 復元前 {col.count()} chunks → 追加 {len(vec_ids)}")

    for start in range(0, len(vec_ids), BATCH):
        ids = vec_ids[start : start + BATCH]
        col.upsert(
            ids=ids,
            embeddings=embeddings[start : start + BATCH].tolist(),
            documents=[docs_by_id[i] for i in ids],
            metadatas=[meta_by_id[i] for i in ids],
        )
        print(f"  {min(start + BATCH, len(vec_ids))}/{len(vec_ids)}")

    print(f"完了: {col.count()} chunks")


if __name__ == "__main__":
    main()
