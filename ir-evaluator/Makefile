.PHONY: chroma-pull chroma-push chroma-status chroma-backup macro-refresh lint test

# scripts/chroma_sync.py は標準ライブラリのみ。任意の Python 3.9+ で動く。
# 既定は uv 経由（--no-project でプロジェクト .venv に触れない = OS 混在フォルダでも安全）。
# uv が無ければ  make chroma-pull PYTHON=python3  等で上書き。
PYTHON ?= uv run --no-project python

# Chroma DB を非公開 GCS バケットと同期する。詳細は docs/chroma-sync.md
chroma-pull:   ## バケット（正本）→ ローカル chroma_db
	$(PYTHON) scripts/chroma_sync.py pull

chroma-push:   ## ローカル chroma_db → バケット（インデックス再構築時のみ）
	$(PYTHON) scripts/chroma_sync.py push --yes

chroma-status: ## ローカルとバケットの概況・差分
	$(PYTHON) scripts/chroma_sync.py status

# バージョン非依存ダンプ（npz + jsonl.gz + manifest）を日付付きで書き出す。
# Drive 等へは出力された chroma_backups/<日付>/ をアップロードする（rclone 等）。
# export は chromadb / numpy が要るのでプロジェクト venv を使う（--no-project 不可）。
BACKUP_DATE := $(shell date +%Y-%m-%d)
BACKUP_DIR  ?= chroma_backups/$(BACKUP_DATE)

chroma-backup: ## Chroma を chroma_backups/<日付>/ にダンプ（Google Drive 退避用）
	uv run python scripts/chroma_export.py --out $(BACKUP_DIR)/chroma_dump
	@echo "→ $(BACKUP_DIR)/ を Google Drive 等へアップロードしてください（docs/chroma-sync.md §5）"

# マクロ経済モニターの更新パイプライン一括実行（japan_market→overall→us→sectors→sectors_extra→report）。
# 週次更新想定（Phase 5 で cron/Scheduler 化する際もこのコマンドが実行単位）。
macro-refresh:
	uv run python -m macro.refresh

lint:
	uv run ruff check .

test:
	uv run pytest
