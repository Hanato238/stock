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

- [ ] 業種マッピングJSON作成（業種コード → PDF名 → Chromaネームスペース）
- [ ] PDF → テキスト抽出（pymupdf）
- [ ] チャンク分割（業種の章・節単位で意味を保持）
- [ ] Gemini Embedding（text-multilingual-embedding-002）でベクトル化
- [ ] Chroma DBに格納（業種別コレクション）
- [ ] 検索精度テスト

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
