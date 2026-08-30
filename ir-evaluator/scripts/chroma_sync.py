"""Chroma DB（chroma_db/ ディレクトリ）を非公開 GCS バケットと同期する。

正本（single source of truth）は GCS バケット。各デバイスはここから pull し、
インデックスを再構築したマシンだけが push する。

  python scripts/chroma_sync.py pull            # バケット → ローカル
  python scripts/chroma_sync.py push --yes      # ローカル → バケット（--yes 必須）
  python scripts/chroma_sync.py status          # 差分の概況

標準ライブラリのみ。プロジェクトの .venv 不要で、任意の Python 3.9+ で動く
（Windows ネイティブの `python`、コンテナの `python3` など）。

設定（環境変数、または プロジェクト直下の .env）:
  CHROMA_GCS_BUCKET   同期先バケット。 gs://my-bucket または gs://my-bucket/prefix
  CHROMA_DIR          ローカルの chroma_db パス（省略時は ir-evaluator/chroma_db）

前提: gcloud CLI がインストール済みで `gcloud auth login`（または VM のサービス
アカウント）でバケットにアクセスできること。Windows では gcloud.cmd を自動検出する。
gcloud を入れたくない場合の代替: `rclone sync` か google-cloud-storage ライブラリ。

注意: Chroma に書き込み中（build_index_batch 実行中など）は同期しないこと。
sqlite の WAL/SHM が残っている場合は中断する。
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REMOTE_SUBDIR = "chroma_db"
# dictionary/store.py の DEFAULT_CHROMA_DIR と同じ場所。
DEFAULT_CHROMA_DIR = PROJECT_ROOT / "chroma_db"
IS_WINDOWS = os.name == "nt"


def _load_env_file() -> None:
    """プロジェクト直下の .env を読む（既に環境にある変数は上書きしない）。

    python-dotenv に依存せずに済ませ、スクリプトを venv 非依存に保つための最小実装。
    """
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _local_dir() -> Path:
    return Path(os.environ.get("CHROMA_DIR") or DEFAULT_CHROMA_DIR).expanduser().resolve()


def _remote_base() -> str:
    bucket = os.environ.get("CHROMA_GCS_BUCKET", "").strip().rstrip("/")
    if not bucket:
        sys.exit("CHROMA_GCS_BUCKET が未設定です（例: gs://ir-evaluator-chroma）。.env か環境変数を確認してください。")
    if not bucket.startswith("gs://"):
        sys.exit(f"CHROMA_GCS_BUCKET は gs:// で始まる必要があります: {bucket!r}")
    return f"{bucket}/{REMOTE_SUBDIR}"


def _gcloud_exe() -> str:
    """gcloud の実行パスを返す。Windows では gcloud.cmd / gcloud.CMD を拾う。"""
    exe = shutil.which("gcloud")
    if exe is None and IS_WINDOWS:
        for name in ("gcloud.cmd", "gcloud.CMD", "gcloud.bat"):
            exe = shutil.which(name)
            if exe:
                break
    if exe is None:
        sys.exit(
            "gcloud CLI が見つかりません。https://cloud.google.com/sdk/docs/install を参照。\n"
            "（インストール済みなら、新しいシェルを開くか PATH を通してください）"
        )
    return exe


def _run_gcloud(args: list[str]) -> int:
    exe = _gcloud_exe()
    print("+ gcloud", " ".join(args))
    # Windows の gcloud はバッチファイル（gcloud.cmd）で CreateProcess から直接起動できない。
    # shell=True にすると Python が cmd.exe /c 経由で呼ぶ。引数は list2cmdline で適切に引用される。
    return subprocess.run([exe, *args], shell=IS_WINDOWS, check=False).returncode


def _check_not_writing(local: Path) -> None:
    for suffix in ("-wal", "-shm"):
        p = local / f"chroma.sqlite3{suffix}"
        if p.exists() and p.stat().st_size > 0:
            sys.exit(
                f"{p.name} が存在します（Chroma が書き込み中の可能性）。"
                "build_index_batch / search を停止してから再実行してください。"
            )


def _rsync_args(*, dry_run: bool, no_delete: bool) -> list[str]:
    args = ["storage", "rsync", "-r"]
    if not no_delete:
        args.append("--delete-unmatched-destination-objects")
    if dry_run:
        args.append("--dry-run")
    return args


def cmd_pull(args: argparse.Namespace) -> int:
    remote = _remote_base()
    local = _local_dir()
    local.mkdir(parents=True, exist_ok=True)
    _check_not_writing(local)
    return _run_gcloud([*_rsync_args(dry_run=args.dry_run, no_delete=args.no_delete), remote, str(local)])


def cmd_push(args: argparse.Namespace) -> int:
    remote = _remote_base()
    local = _local_dir()
    if not (local / "chroma.sqlite3").exists():
        sys.exit(f"{local / 'chroma.sqlite3'} がありません。push するローカル DB が見つかりません。")
    _check_not_writing(local)
    if not args.yes and not args.dry_run:
        sys.exit("push は正本（バケット）を上書きします。実行するには --yes を付けてください。")
    return _run_gcloud([*_rsync_args(dry_run=args.dry_run, no_delete=args.no_delete), str(local), remote])


def cmd_status(args: argparse.Namespace) -> int:
    remote = _remote_base()
    local = _local_dir()
    print(f"local : {local}")
    if local.exists():
        size = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
        nfiles = sum(1 for f in local.rglob("*") if f.is_file())
        print(f"        {size / 1e9:.2f} GB / {nfiles} files")
        for suffix in ("-wal", "-shm"):
            if (local / f"chroma.sqlite3{suffix}").exists():
                print(f"        ! chroma.sqlite3{suffix} あり（書き込み中の可能性）")
    else:
        print("        （存在しません）")
    print(f"remote: {remote}")
    print("（初回 push 前はリモートが空のため、以下は『no objects』『Did not find existing container』になる。push 後は解消）")
    _run_gcloud(["storage", "du", "-s", "-r", remote])
    print("\n--- rsync dry-run（pull 方向の差分）---")
    return _run_gcloud([*_rsync_args(dry_run=True, no_delete=False), remote, str(local)])


def main() -> None:
    _load_env_file()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pull = sub.add_parser("pull", help="バケット → ローカル")
    p_pull.add_argument("--dry-run", action="store_true", help="実際には転送せず差分だけ表示")
    p_pull.add_argument("--no-delete", action="store_true", help="ローカルの余分なファイルを削除しない")
    p_pull.set_defaults(func=cmd_pull)

    p_push = sub.add_parser("push", help="ローカル → バケット（--yes 必須）")
    p_push.add_argument("--yes", action="store_true", help="正本の上書きを承認")
    p_push.add_argument("--dry-run", action="store_true", help="実際には転送せず差分だけ表示")
    p_push.add_argument("--no-delete", action="store_true", help="バケットの余分なファイルを削除しない")
    p_push.set_defaults(func=cmd_push)

    p_status = sub.add_parser("status", help="ローカルとバケットの概況・差分")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
