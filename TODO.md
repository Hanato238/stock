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
- [~] 企業プロファイル→辞典コレクション解決層のスカフォールド（`rag/industry_map.json`, `rag/mapping.py` — c088833側で追加。単一コレクション+メタデータフィルタ方式に合わせた entries 投入は今後）
- [x] **Chroma DB のデバイス間共有（2026-08-30）** — 非公開 GCS バケットを正本にして `scripts/chroma_sync.py`（`gcloud storage rsync` ラッパー、`make chroma-pull/push/status`）で同期。手順は `docs/chroma-sync.md`。`chromadb==1.5.9` に固定（永続インデックス形式がバージョン依存のため）。バックアップ用に `scripts/chroma_export.py` / `chroma_import.py`（npz+jsonl.gz、chromadbバージョン非依存）も追加。将来 GCP VM 常時稼働後は Chroma サーバモード（`chroma run`+`HttpClient`）へ移行予定。

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
- [x] 接続テスト（`uv run python edinet/test_connection.py` — 2026-08-27 疎通確認済み）
- [x] DL検証（森永乳業 有報 S100TPOT.pdf 取得成功 — 2026-08-27）
- [x] テキスト抽出・前処理（edinet/extract.py — pymupdf + NFKC正規化。森永乳業181p/20万字で検証済み 2026-08-27）
- [x] 企業の業種コード取得・マッピング
  - [x] 有報から業種特定材料を抽出（edinet/industry.py — 【事業の内容】【関係会社の状況】。森永乳業で検証済み 2026-08-27）
  - [x] プロファイル→辞典コレクション解決層（rag/mapping.py resolve() — 空マッピング安全・仮投入で正解確認）
  - [ ] 業種分類キー = 審査辞典の巻/章立て（entries投入は Phase 2 の manifest と整合させて）

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

## Phase 4.5: マクロ経済データ取り込み（設計確定 2026-08-27）

- 目的: 主軸B（環境の文脈付け）＋補助A（業種相対の基準線）＋補助C（時系列切り分け・Phase6用）
- 指標6本: 政策金利 / USD/JPY / コアCPI / 短観業況判断DI（業種別）/ 名目GDP成長率 / 鉱工業生産指数IIP（業種別）
- ソース: ハイブリッド（全体系=FRED、業種別DI/IIP=e-Stat API・日銀）。e-Statキーは .env 直書き
- 保存: JSONキャッシュ `data/macro/{overall,tankan_di,iip,_meta}.json`（時系列で保持、確報値で割り切り）
- 注入: 環境サマリ・ブロックをGemini評価プロンプトに前置。基準線のみコードで軽く数値化
- [x] `macro/` パッケージ新設（fred.py / store.py / fetch.py / __init__.py。ruff通過・オフライン検証済み 2026-08-27）
- [x] FRED取得（GDP・政策金利・USD/JPY の3指標）← 実地検証済み。overall.json 保存 2026-08-27
  - CPIはFRED日本月次指数がOECD MEI打ち切りで現行維持なし → e-Statへ移管
- [x] CPIをe-Statから取得（月次・現行の一次ソース＝総務省）2026-08-27
  - [x] e-Statクライアント実装（macro/estat.py。検索/getStatsData/メタ/時間コード変換）
  - [x] ESTAT_APP_ID取得・.env設定
  - [x] CPI統計表ID特定（0003427113 / cat01=0161 生鮮食品を除く総合=コアCPI / area=00000 全国 / tab=1 指数）
  - [x] fetch_cpi.py実装、`macro.fetch` で overall.json に統合（4指標・最新2026-07=113.6検証済）
- [ ] e-Stat/日銀取得（業種別DI・業種別IIP）← industry_map entries とソフト依存
- [x] build_macro_context(fiscal_period, industry_key)（macro/context.py。決算期環境＋足元環境を併記、YoY算出。金利は差分%pt・他は変化率%。閏/データ範囲外も検証済 2026-08-27）
- [ ] Phase4評価プロンプトへ接続（context.pyの出力を評価プロンプト冒頭に前置）
- [ ] 更新: 手動始動 → Phase5でcron/Schedulerへ昇格

## Phase 6: 精度改善・運用

- [ ] 評価レポートのレビューとプロンプト調整
- [ ] 複数決算期の比較分析機能
- [ ] Google Drive通知連携
- [ ] モニタリング・ログ整備
- [ ] レポートHTML化 + Vercel配信（コードpublic / レポート実体は認証下。金商法の投資助言リスク回避）

---

## 進捗サマリー（2026-08-27 時点）

### ここまでの推移

**Phase 2（審査辞典RAG化）— 本番インデックス完了（2026-08-24〜25）**
- 辞典PDF（1600項目・スキャン画像）を Gemini Vision OCR → チャンク分割 → Gemini Embedding → Chroma 格納
- Batch API 版（`dictionary/build_index_batch.py`）で全1600項目・chunk 51,331件・エラー0でインデックス完了
- 単一コレクション + メタデータフィルタ（code/volume/field/section）方式

**Phase 3（EDINET取得）完了**
- EDINET API v2クライアント疎通・DL検証（森永乳業 有報 S100TPOT.pdf 181p）
- テキスト抽出（pymupdf + NFKC）、有報から業種特定材料抽出（【事業の内容】【関係会社の状況】）
- OCR不要と確定（テキストレイヤーあり／財務数値はXBRL経由）

**Phase 4.5（マクロ経済データ）— 全体系B主軸を完成**
- grill-meで設計確定：目的=主軸B（環境の文脈付け）＋補助A（業種相対）＋補助C（時系列切り分け）
- `macro/` パッケージ新設：
  - `fred.py` … FRED APIクライアント
  - `estat.py` … e-Stat APIクライアント（統計表検索・getStatsData・メタ・月次時間コード変換）
  - `store.py` … data/macro/*.json 読み書き（時系列保持）
  - `fetch.py` / `fetch_cpi.py` … 取得オーケストレーション（1コマンドで4指標→overall.json）
  - `context.py` … 決算期環境＋足元環境のサマリブロック生成（B主軸の心臓部）
- 全体系4指標を実データ検証済み（`data/macro/overall.json`）：
  - USD/JPY（FRED:DEXJPUS）／政策金利（FRED:IRSTCI01JPM156N）／名目GDP（FRED:JPNNGDP）
  - コアCPI（e-Stat:0003427113 生鮮食品を除く総合・全国・指数）
- 判明した論点と対応：
  - FRED日本CPI月次指数はOECD MEI打ち切りで現行維持なし → CPIをe-Stat（総務省・月次・現行）へ移管
  - 金利のYoYは変化率%が無意味 → 金利のみ前年差%pt、他は変化率%で表示
- 公開方針決定：GitHub+Vercel、コードpublic・レポート実体は認証下（投資助言リスク回避）

### これから行うべきこと（優先順）

1. **Phase 4: 評価エンジン本体（次の主戦場）**
   - 2段階RAG検索（業種→Chromaコレクション特定 → IRテキスト×辞典チャンク）
   - Gemini評価プロンプト設計（リスク評点・要注意項目・投資適格性コメント）
   - **`context.py` の出力を評価プロンプト冒頭に前置**（マクロ配線。ここでB主軸が実際に効く）
   - Markdownレポート生成 → Google Drive保存
2. **業種別DI/IIP追加（補助A）** — `industry_key`と配線して業種相対の基準線を成立
   - 注意：IIPは製造業・鉱業のみ（非製造業は第3次産業活動指数）。短観業種区分→辞典巻/章のマッピングが必要
3. **rag/ と dictionary/ の整合** — c088833 の `rag/industry_map.json` / `rag/mapping.py`（企業プロファイル→コレクション解決層）を、b49f1b3 の単一コレクション+メタデータフィルタ方式（`dictionary/manifest.py`）に合わせて entries を投入
4. **Phase 1: インフラ（GCP VM / Vertex AI / Drive OAuth / nanoclaw）**
5. **運用**：マクロ更新を手動→Phase5でcron/Scheduler昇格、レポートHTML化+Vercel配信

### 既知のブロッカー / 依存
- Phase 4のマクロ配線（context.py前置）は依存なしで即着手可
- 業種別DI/IIP配線は industry_map の entries 投入待ち
