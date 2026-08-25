# IR評価システム TODO

## 設計サマリー

| 項目 | 決定 |
|------|------|
| アウトプット | 投資適格性評価レポート（Markdown + Google Drive保存） |
| 入力IR | 有価証券報告書 + 決算短信（EDINET API） |
| 辞典活用 | RAG（Chroma）+ 業種マッピングJSON |
| 企業指定 | 手動オンデマンド → バッチ拡張 |
| LLM | Gemini（Vertex AI） |
| インフラ | GCP 単一VM + nanoclaw（フルスクラッチ） |
| Vector DB | Chroma（VM上ローカル） |

---

## Phase 1: インフラ構築

- [ ] GCP VMプロビジョニング（e2-standard-4、Ubuntu 22.04）
- [ ] Vertex AI API有効化
- [ ] GCPサービスアカウント作成・権限設定
- [ ] Google Drive API / GWS OAuth設定
- [x] EDINET アカウント登録（APIキー取得待ち — 平日に再ログイン）
- [ ] nanoclaw インストール・初期設定
- [x] Python環境構築（uv + 依存パッケージ — ir-evaluator/.venv 作成済み）

## Phase 2: 審査辞典RAG化

> 設計変更メモ（2026-08-23）:
> - 辞典PDFは「業種別」フォルダに1600項目単位で分割済み・`split_manifest.json`にコード/項目名メタデータありと判明。静的な業種マッピングJSONは作らず、`dictionary/manifest.py`がディレクトリを直接スキャンして項目一覧（コード・項目名・巻・分野・章）を構築する方式に変更。
> - PDFはテキスト層のないスキャン画像と判明（pymupdf等では0文字）。そのためOCRが必須。Gemini Vision（マルチモーダル）で項目単位（PDF全ページ→1回のAPI呼び出し）でMarkdown書き起こしする方式に変更（`dictionary/ocr.py`）。
> - Chromaコレクションは項目数(1600)×業種区分だと粒度過多のため、「単一コレクション + メタデータフィルタ（code/volume/field/section）」方式に変更。
> - `google-generativeai`は非推奨のため`google-genai`（新SDK）に移行済み。

- [x] 辞典ディレクトリスキャン・項目一覧構築（`dictionary/manifest.py`、業種マッピングJSONの代替）
- [x] PDF → テキスト化（`dictionary/ocr.py`、Gemini Vision OCR。pymupdfではなくpypdfium2でページ画像化→Gemini書き起こし）
- [x] チャンク分割（`dictionary/chunk.py`、項目＝辞典の最小意味単位のため段落パッキング方式）
- [x] Gemini Embedding実装（`dictionary/embed.py`。モデルは`text-embedding-004`が廃止済みだったため`gemini-embedding-001`に変更）
- [x] Chroma DB格納実装（`dictionary/store.py`、単一コレクション+メタデータフィルタ方式）
- [x] 検索精度テスト用CLI実装（`dictionary/search.py`、`uv run python -m dictionary.search "クエリ"`）
- [x] 30項目の小規模テスト実行・パイプライン全体（OCR→チャンク分割→Embedding→Chroma格納→検索）の動作確認 completed（2026-08-23）
- [x] **全1600項目の本番実行 completed（2026-08-24）** — Gemini Batch APIに作り直して実行（コスト半減のため）。結果: 1600/1600項目インデックス済み、chunk総数51,331、エラー0件。検索精度も確認済み。
  - バッチ実行では1件（`4044_マンホール蓋製造業`）が空文書でスキップされたが、Standard API版パイプラインで個別に再OCR・再格納し解消（2026-08-25）。

> 実行方式メモ（2026-08-24）: 本番投入直前にコスト試算（Standard API見積 約$45〜50）を提示したところ、結果は急がなくてよいとのことでBatch API（半額、約$25）に作り直した。
> - `dictionary/build_index_batch.py`（新規）: PDFをFiles APIにアップロード→OCRバッチジョブ（`InlinedRequest`、metadataのcodeで対応付け）→チャンク分割→Embeddingバッチジョブ（`EmbeddingsBatchJobSource`は1ジョブ=1リクエストで複数テキストをcontentsにまとめる、レスポンスはテキスト数と同数返るのでリスト順で対応付け）→Chroma格納、という流れ。`--batch-size`（デフォルト200）で区切り、区切りごとに確定即Chroma格納するため、途中で止めても再開できる。
> - `dictionary/build_index.py`（Standard API・同期並列版）は動作確認用として残置。実運用では使っていない。
> - Batch API移行で新たに踏んだ地雷: (1) Files APIアップロード時、SDKが日本語ファイル名をそのままHTTPヘッダー`X-Goog-Upload-File-Name`に載せてUnicodeEncodeErrorになる → パスではなく開いたファイルオブジェクトを渡すことで回避（`dictionary/batch.py`）。(2) `EmbeddingsBatchJobSource.inlined_requests`は`EmbedContentBatch`のリストではなく単体オブジェクトで、`contents`にテキストのリストを渡す仕様だった（型のドキュメントがなく実際にSDKの型定義を読んで判明）。(3) 1600項目を1本の巨大バッチジョブに積むリスクを避けるため200件区切りに分割。

> 実行メモ（2026-08-23、小規模テスト中に発見・修正した問題。Standard API版 `build_index.py` 開発時）:
> - **stdout バッファリング**: バックグラウンド実行時に進捗ログが失われる → `python -u`（unbuffered）で解決。
> - **pypdfium2のスレッド不安全性**: `--workers`で並列化するとPDFレンダリングの同時実行でセグフォルトし、プロセスごと無言で落ちる（exit code 0で全く進捗なし、という紛らわしい症状）。`dictionary/ocr.py`でレンダリング部分のみ`threading.Lock`で直列化し、ネットワーク待ちのAPI呼び出し部分は並列のまま維持することで解決。
> - **OCRの暴走（表崩れ）**: 複雑な表を含む一部項目でGeminiが空白や同一行を延々出力し、コスト増・chunk数の異常な膨張（最大206chunks/項目）を引き起こす現象を確認。対策として (1) `thinking_budget=0`でthinking無効化（本来不要なタスクなので副次的に高速化にも寄与）、(2) `max_output_tokens`で出力上限を設定、(3) 同一行の連続繰り返しや異常に長い行を検知して打ち切る後処理（`_clean_ocr_text`）、(4) 打ち切られて内容がほぼ残らなかった場合は自動リトライ、を実装。
> - 20項目・10並列での実測: 2分36秒（約7.8秒/項目）。全1600項目なら概算3.5時間。有料枠のためレート制限は現時点で未発生。

## Phase 3: EDINET取得パイプライン

- [x] EDINET API v2クライアント実装（ir-evaluator/edinet/client.py）
  - [x] 企業名/証券コード → EDINETコード変換
  - [x] 有報・決算短信の書類一覧取得
  - [x] PDF/XBRLダウンロード
- [x] 企業名でのIR取得ハイレベルAPI（ir-evaluator/edinet/fetch.py）
- [ ] 接続テスト（APIキー取得後に `uv run python edinet/test_connection.py`）
- [ ] テキスト抽出・前処理
- [ ] 企業の業種コード取得・マッピング

## Phase 4: 評価エンジン

- [ ] 2段階RAG検索実装
  - [ ] 企業業種 → 対応ChromaコレクションをJSONで特定
  - [ ] IRテキスト × 審査辞典チャンクの関連検索
- [ ] Gemini評価プロンプト設計
  - [ ] リスク評点（財務・事業・経営）
  - [ ] 要注意項目リスト
  - [ ] 投資適格性コメント
- [ ] Markdownレポート生成
- [ ] Google Driveへの保存（Drive API）

## Phase 5: nanoclaw統合

- [ ] オンデマンドフロー実装（「〇〇社を評価」→ レポート返却）
- [ ] ウォッチリスト管理（JSON or SQLite）
- [ ] バッチ処理スケジューラ（cron または Cloud Scheduler）
- [ ] エラーハンドリング・リトライ

## Phase 6: 精度改善・運用

- [ ] 評価レポートのレビューとプロンプト調整
- [ ] 複数決算期の比較分析機能
- [ ] Google Drive通知連携
- [ ] モニタリング・ログ整備
