# Chroma DB のデバイス間共有

`ir-evaluator/chroma_db/`（約1.2GB）は審査辞典1600項目・51,331チャンクのベクトルインデックス。
Gemini Batch API による一度きりの本番実行（約$25・数時間）で構築した成果物で、git 管理外
（`.gitignore` 対象）。複数の開発マシンと将来の GCP VM で**再構築せずに**同じ DB を使うため、
**非公開 GCS バケットを正本**として `scripts/chroma_sync.py` で同期する。

> リポジトリは将来 public 化予定、元データ（業種別審査辞典 第5版）は市販の有償コンテンツ。
> DB を git / Git LFS / 公開ストレージ / サードパーティのマネージド Vector DB に置かないこと。

## 1. 初回セットアップ（1回だけ）

### バケット作成

```bash
gcloud storage buckets create gs://ir-evaluator-chroma \
  --location=asia-northeast1 \
  --uniform-bucket-level-access \
  --public-access-prevention
```

- 名前はグローバル一意。既に使われていれば `gs://<project>-ir-chroma` 等に変更し `.env` も合わせる。
- ロケーションは自分 / VM に近いリージョン。

### IAM

| 主体 | ロール | 用途 |
|------|--------|------|
| push するマシンのアカウント | `roles/storage.objectAdmin` | pull + push |
| pull のみのマシン / VM のサービスアカウント | `roles/storage.objectViewer` | pull のみ |

```bash
# 例: 自分のアカウントに objectAdmin
gcloud storage buckets add-iam-policy-binding gs://ir-evaluator-chroma \
  --member="user:you@example.com" --role="roles/storage.objectAdmin"

# 例: VM のサービスアカウントに objectViewer
gcloud storage buckets add-iam-policy-binding gs://ir-evaluator-chroma \
  --member="serviceAccount:SA@PROJECT.iam.gserviceaccount.com" --role="roles/storage.objectViewer"
```

### 各マシンの準備

```bash
gcloud auth login                  # VM ではサービスアカウントなので不要
#   ブラウザが開けない環境（devcontainer 等）は:  gcloud auth login --no-launch-browser
gcloud config set project <PROJECT_ID>
# .env に CHROMA_GCS_BUCKET=gs://<バケット名> を追記（.env.example 参照）
```

### 正本の初回アップロード（現在 DB があるマシンから）

```bash
uv run --no-project python scripts/chroma_sync.py status     # remote=空 / local≈1.2GB を確認
uv run --no-project python scripts/chroma_sync.py push --yes  # 既知の良好状態（51,331チャンク）をベースラインとして push
```

## 2. 日常の使い方

`scripts/chroma_sync.py` は**標準ライブラリのみ**（`.env` も python-dotenv なしで自前で読む）。
実行方法は環境次第で選べる:

```bash
# 推奨（uv 経由。--no-project でプロジェクト .venv に触れない）
uv run --no-project python scripts/chroma_sync.py status
uv run --no-project python scripts/chroma_sync.py pull
uv run --no-project python scripts/chroma_sync.py push --yes     # push は --yes 必須

# プロジェクト venv がある環境ならこれでも可
uv run python scripts/chroma_sync.py status
.venv/bin/python scripts/chroma_sync.py status

# python3 がそのまま使える環境（多くの Mac / Linux）
python3 scripts/chroma_sync.py status

# make（既定は uv run --no-project python。上書きは PYTHON=... ）
make chroma-pull
make chroma-status PYTHON=python3
```

### 実行コマンドの注意（共有フォルダの場合）

devcontainer と Windows が**同じフォルダを共有**している場合（`..:/workspace` バインドマウント等）:

- `.venv` は OS 固有なので共有できない。**Windows 側から `uv sync` や 素の `uv run`
  （＝プロジェクト同期が走る）を実行しない**こと。Linux 用 venv が壊れる。
- Windows でこのスクリプトを動かすなら **`uv run --no-project python ...`**（venv に触れない）
  か、素の `python scripts\chroma_sync.py ...` を使う。
- gcloud は Windows の `gcloud.cmd` を自動検出し `cmd.exe` 経由で呼ぶので、スクリプト自体は
  Windows でそのまま動く。

## 3. 運用規約

- **バケットが正本。** ローカルは作業コピー。作業前に必ず `chroma-pull`。
- **書き込み者は1台だけ。** `build_index_batch` を実行したマシンだけが `chroma-push` する。
  複数マシンから push しない（rsync は last-writer-wins で、先の変更が消える）。
- **同期中に Chroma を触らない。** `build_index_batch` / `dictionary.search` 実行中は
  push / pull しない。sqlite の WAL（`chroma.sqlite3-wal`）が残っているとスクリプトが中断する。
- **正本を失っても再生成可能。** PDF（`業種別審査辞典_第5版_業種別/`）＋ `build_index_batch.py`
  から作り直せる。ただし約$25・数時間かかるので push は忘れずに。

## 4. chromadb のバージョン

永続化インデックス（HNSW）と sqlite スキーマは **`chromadb` のバージョンに紐づく**。
マシン間でバージョンが違うと、コピーした DB の読み込み失敗や無言のマイグレーションが起こりうる。

- `pyproject.toml` で `chromadb==1.5.9` に固定済み。全マシンで `uv sync` すれば揃う。
- `chromadb` をアップグレードするときは: 1台で上げて DB を読めることを確認 →
  必要ならインデックス再構築 → `chroma-push` → 他マシンで `uv sync` してから `chroma-pull`。

## 5. バックアップ（任意 / Google Drive 等）

GCS バケット（正本）とは**別系統・別形式・別プロバイダ**の冗長コピーを取れる。
インデックス破損や `chromadb` の破壊的アップグレードへの保険。

### ダンプ形式（`scripts/chroma_export.py`）

`chromadb` バージョン非依存。テキストは JSON、ベクトルはバイナリ float32:

| ファイル | 中身 | サイズ目安 |
|---|---|---|
| `chroma_dump.jsonl.gz` | 1行1チャンク `{"id","document","metadata"}` | 数〜十数 MB |
| `chroma_dump.npz` | 埋め込み行列（51,331 × 3072 float32） | 約630 MB |
| `chroma_dump.manifest.json` | 作成日時・件数・次元・`chromadb` バージョン | 数百 B |

> ベクトルを JSON 文字列にすると約4倍（≈2.8GB、gzip しても圧縮が効かない）で
> 読み書きも遅い。npz バイナリが実質最適。

### 取得（日付付き）

```bash
# make がある環境（Mac 等）
make chroma-backup

# make が無い環境（devcontainer 等）— 直接
uv run python scripts/chroma_export.py --out chroma_backups/$(date +%Y-%m-%d)/chroma_dump

#   → chroma_backups/<YYYY-MM-DD>/chroma_dump.{jsonl.gz,npz,manifest.json}
#   （export は chromadb / numpy が要る = プロジェクト venv 必須。Windows 共有フォルダ
#    からは実行しない。コンテナか Mac から。）
```

### Google Drive へアップロード

gcloud は Drive 非対応。以下のいずれか:

- **手動**: `chroma_backups/<日付>/` フォルダをそのまま Drive にドラッグ。年数回ならこれで十分。
- **rclone**（バイナリ1個、Windows / コンテナ両対応。`rclone config` で `gdrive` リモートを作成）:
  ```bash
  rclone copy chroma_backups/2026-08-30 gdrive:backups/chroma/2026-08-30
  ```

### 復元

```bash
# 空ディレクトリへ展開して確認 → 問題なければ本番 CHROMA_DIR で実行
CHROMA_DIR=/tmp/restore uv run python scripts/chroma_import.py --in chroma_backups/2026-08-30/chroma_dump
```

`collection.upsert` で戻すので API 呼び出しゼロ。manifest の `chromadb` バージョンが
現在と違っても upsert 復元なら通常問題ない（警告は出る）。

## 6. 将来: GCP VM 常時稼働後

VM が常時起動したら、同期をやめて **Chroma サーバモード**へ移行できる:

- VM 上で `chroma run --path /data/chroma_db --host 0.0.0.0 --port 8000`（systemd 常駐、
  トークン認証 `CHROMA_SERVER_AUTHN_CREDENTIALS`、ファイアウォールで接続元を限定 or IAP トンネル）
- `dictionary/store.py` に `CHROMA_HOST` 環境変数で `chromadb.HttpClient` 分岐を追加
- 全デバイス（開発マシン・nanoclaw）が VM を直接参照。コピー不要・バージョンずれなし。
