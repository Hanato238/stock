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
  - **初回 GCS アップロード完了（2026-08-30）** — 本番インデックス済み `chroma_db/`（1600項目・chunk 51,331件）を正本バケットへ push 済み。以降は他デバイスから `make chroma-pull` で取得可能。

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

> 実装メモ（2026-08-30）: `evaluation/` パッケージ新設。
> - LLM を provider 非依存化（`evaluation/llm.py`）。モデル名でプロバイダ自動判定
>   （`gemini-*`→Gemini / `claude-*`→Anthropic / `gpt-*`→OpenAI）。既定は `EVAL_MODEL`
>   （未設定なら `gemini-3.1-pro-preview`。`gemini-2.5-pro` は新規ユーザー提供終了で 404）。
>   anthropic/openai SDK は optional-dependencies で任意導入。SDK 固有例外は `LLMError` に正規化。
> - リスク評点の向き = **1 低リスク / 5 高リスク**（要注意項目と向きを揃える）。
> - Drive 保存は Phase 1（OAuth）完了まで見送り。レポートはローカル `data/reports/` のみ。
> - `pyproject.toml` の `[project.scripts]` を `ir_evaluator.cli`（未存在）→ `evaluation.cli` に修正。

> ライブ実行の知見（2026-08-30、森永乳業で準ライブ実行＝EDINET取得のみバイパス）:
> - **この環境の `.env` の EDINET_API_KEY はプレースホルダー**（実キー未反映。EDINET は 200 で
>   `{"StatusCode":401}` を返すため `raise_for_status()` を素通りし「見つかりません」になる）。
>   → `edinet/client.py` に body 内 StatusCode のチェックを足すと親切（未対応）。
> - **業種判定は LLM 抽出が必須**（`evaluation/classify.py` 新設）。有報【事業の内容】冒頭は
>   「当社グループは、〜子会社・関連会社で構成され」の定型文で業種語が埋もれ、正規表現では
>   安定しない。審査辞典は業種別の財務指標テーブルが大半で、散文より「◯◯製造業」名詞句クエリ
>   が効く。軽量モデル（`INDUSTRY_MODEL`、既定 `gemini-flash-latest`）で業種名を 5 個抽出 →
>   retrieval の主クエリに。**Gemini 3 系は thinking 既定 on で、出力上限が小さいと思考トークンを
>   使い切って空応答**になる → `llm.generate_json(light=True)` で thinking off にして回避。
> - 効果: 森永乳業で retrieval 距離 0.48（無関係）→ **0.33〜0.40（乳製品製造業[1039]・処理牛乳
>   乳飲料製造業[1038]・アイスクリームショップ 等）**。評価文が辞典を引用（「[1039]は固定比率
>   200%超で借入依存が高い傾向」等）、マクロ（円安162円・政策金利0.84%）も反映。

- [x] 2段階RAG検索実装（`evaluation/retrieval.py` + `evaluation/classify.py`）
  - [x] 企業業種 → 審査辞典メタデータフィルタ解決（`resolve_filter()`。`rag.mapping.resolve()`
        が None の間はフィルタなし＝単一コレクション全体を検索。entries 投入後に効く）
  - [x] 業種ターム LLM 抽出 → 名詞句クエリ＋与信観点クエリ＋有報のリスク/MD&A チャンク →
        ベクトル検索 → 同一項目 3 件までに絞り距離順（`_MAX_PER_CODE`）
  - [x] 実DB検証（森永乳業）: 上位が乳製品製造業・処理牛乳乳飲料製造業に一致、距離 0.33〜0.40
- [x] 評価プロンプト設計（`evaluation/prompt.py`、`schema.py`）
  - マクロ環境ブロック（`macro.context`）を冒頭に前置 → 企業プロファイル → 辞典知見（出典コード付き）
    → 有報要点 → 出力スキーマ、の順で組み立て
  - [x] リスク評点（財務・事業・経営、1〜5）／要注意項目（高中低）／投資適格性コメント
  - [x] 総合判定（適格/条件付き適格/要精査/非適格）
  - [x] LLM 出力 JSON の寛容パース（コードフェンス除去・括弧対応抽出・欠損補完・判定語の正規化）
- [x] Markdownレポート生成（`evaluation/report.py`。業種判定・リスク評点表・要注意項目・マクロ前提・
      参照辞典チャンク付録・金商法ディスクレーマ）
- [x] CLI（`evaluation/cli.py`。`--pdf --fiscal-period` / `--company --from --to`、`--model` `--industry-model`）
- [x] ユニットテスト 42 件（schema/llm/prompt/report/retrieval/classify。`tests/`、ruff 通過）
- [x] **準ライブ end-to-end 実行**（森永乳業。RAG＝実Chroma・評価＝実Gemini 3.1-pro。EDINET のみ手入力バイパス）
- [x] **完全ライブ実行 completed（2026-08-30）** — EDINET・GOOGLE 実キー投入後、`uv run python -m evaluation.cli --company 森永乳業 --from 2025-06-01 --to 2025-06-30 --model gemini-flash-latest` で通し、総合判定「適格」（財務/事業/経営とも評点2）。`data/reports/森永乳業株式会社_2025-03-31.md`
- [ ] 追加チューニング: 有報のリスク/MD&A 散文クエリはノイズ寄り（辞典は表主体）。重み下げ or 除外を検討
- [ ] 別モデル比較（`--model claude-opus-5` 等。要 `uv sync --extra anthropic`）
- [ ] Google Driveへの保存（Drive API）← Phase 1 の OAuth 設定待ち

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

## Phase 4.6: マクロ経済ページと個別株評価ページの分離（設計確定 2026-08-31）

> grill-meで設計確定。現状は森永乳業のデータを使った統合HTML1枚（`data/reports/森永乳業株式会社_2025-03-31.html`）に
> マクロ環境モニターと個別株評価が同居していたが、目的が異なる（マクロ＝指標に基づく大勢理解、個別株＝投資適格性の判断）ため
> 2ページに分離する。モックアップ2枚をArtifactとして作成済み：
> - マクロ経済モニター（企業非依存・日本/米国）: https://claude.ai/code/artifact/cc33315b-48c5-443e-9025-863dc01968a7
> - 森永乳業 個別株評価（マクロは要約引用＋リンクのみ）: https://claude.ai/code/artifact/4ef8b309-d7b1-44df-9182-4b651ab6faa2

**決定事項**
- 成果物: モックアップで構成確定 → 引き続き `evaluation/report.py`・`macro/` パッケージ側の本実装に接続する
- ページ関係: 個別株ページはマクロを要約引用（ミニチャート＋総合見立てピル）＋マクロページへのリンクのみ。詳細チャート・全指標はマクロページに一本化
- マクロページの結論: 指標の事実提示に留めず、「拡大局面／巡航速度」等の総合見立て（トーン）まで踏み込んで記載する。ただし投資の売買判断（買い時/売り時）ではなく、あくまで景気循環そのものへの見立てに限定し、個別株ページの投資適格性判断とは役割を分離する
- データフロー: マクロスナップショットJSON（`data/macro/*.json`）を独立したタイミング（**週次**）で更新・生成し、個別株評価はそのスナップショットを参照する。企業評価のたびにFRED/e-Statへ再アクセスしない
- 公開順序: マクロページを先に生成・公開してURLを確定 → 個別株ページのマクロ要約カードへ埋め込む
- ビジュアル: 両ページとも同一デザインシステム（帳簿/印鑑風）を継続。ページ種別による意匠の作り分けはしない
- スコープ: 今回は森永乳業1社分のページ構造確定まで。複数企業の一覧/インデックスページはPhase 5（バッチ処理・nanoclaw統合）の将来項目として本フェーズには含めない
- 個別株ページのミニチャートは「日経平均＋CI一致指数」を基本形としつつ、**業種に応じた関連セクター指標（生乳価格・飼料穀物価格等）をLLMが動的に選択**する仕組みを将来実装する（`evaluation/classify.py`の業種タームLLM抽出と同じパターン）。ただし収集するセクター指標そのものはユーザーが手動で決定する方針（未確定・後日確認）
- マクロページの「総合見立て」もLLMが指標データから自動生成する仕組みとし、`evaluation/prompt.py`相当の生成パイプラインを`macro/`側にも新設する

**ロードマップ（実装タスク）**

> 実装完了メモ（2026-08-31）: 下記の主要タスクを実装・実データ/実LLMで動作検証済み。
> `uv run python -m macro.report` → `data/macro/report.html`（マクロページ）生成 →
> `uv run python -m evaluation.cli --pdf ... --fiscal-period 2025-03` で森永乳業を再評価し、
> Markdownレポートに新設「## マクロ前提（詳細は別ページ）」節（総合見立て引用＋関連セクター指標＋
> リンク）が実際に反映されることを確認。テスト21件追加（`test_narrative.py` `test_indicators.py`、
> `test_report.py`にモック追加）、全63件（既存42＋新規21）通過・ruff通過。

- [x] `macro/fetch_jp_market.py`（新規）/ `macro/fetch_us.py`（新規）: CI/DI（e-Stat statsDataId
      0003446461、`estat.py`に`cd_tab`パラメータ追加）・日経平均・JP10年金利・CFNAI/CFNAI-MA3・
      S&P500・US10年金利・FF金利・米CPI/コアCPI・米実質GDP成長率を取得し
      `data/macro/{japan_market,us}.json`へ保存。実APIで動作確認済み。
- [x] `macro/narrative.py`（新規）: 指標データ→LLM（既定`gemini-flash-latest`）で「読み方」パネル
      （2〜3段落）＋「総合見立て」（tone: expand/neutral/contract＋日本語ラベル＋2〜3文）を生成。
      数値の整形・期間内変化率の計算はコード側（LLMには数値を再計算させない）。ゼロ近傍で振動する
      指数（CFNAI等）は変化率%ではなく絶対差（ポイント）で表現するよう補正済み。
      `save_narratives`/`load_narratives`で`data/macro/narrative.json`にキャッシュし、
      企業評価側がLLMを再呼び出しせず引用できるようにした。
- [x] `macro/report.py`（新規）: `data/macro/*.json`＋narrativeからマクロページHTMLを生成
      （日本・米国、CI/DIチャート・DIゲージ・日経平均/S&P500チャート・CFNAIチャート・基礎指標・
      読み方・総合見立て）。grill-meで確定した帳簿/印鑑デザインシステムを踏襲。実データで生成・
      タグバランス検証済み。
- [x] `macro/indicators.py`（新規）: セクター指標カタログ（indicator_catalog.json）から、
      業種タームをLLMで19区分の`industry_taxonomy`へ写像し関連指標を選択
      （`evaluation/classify.py`と同じ「LLMは分類判定のみ、数値/URLは生成させない」設計）。
      業種特化指標を優先、不足分は「全業種共通」で埋め合わせ。LLM失敗時は共通指標へフォールバック。
- [x] `evaluation/report.py`にマクロ前提節を追加（`_macro_premise()`）。日本の総合見立て引用＋
      関連セクター指標リスト＋`data/macro/report.html`へのリンク注記。ミニチャート（SVG）は
      Markdownでは表現できないため見送り、HTML化はPhase 6の「レポートHTML化」でまとめて対応。
- [ ] マクロスナップショットの週次更新パイプライン（cron/Scheduler、Phase 5と合流）— 手動実行は可能、自動化は未着手
- [x] **セクター指標プールの定義・データソース検証 completed（2026-08-31）** — grill-meでユーザーが挙げた日本の公的統計・業界統計44種類を精査。「自殺者数」は投資適格性評価への直接引用がセンシティブなためプールから除外。「官公庁統計情報」「月例経済報告」「金融経済月報」「オルタナティブデータ」の4つは数値系列ではないため type: narrative/meta として別枠に分離。残り38指標について並列調査エージェント5組（WebSearch/WebFetch）でアクセス方法・頻度・難易度・関連業種（日本標準産業分類の大分類相当19区分）を検証し、`data/macro/indicator_catalog.json` として保存。
  - e-Stat APIで機械的取得可能: 17/38（家計調査・商業動態統計・法人企業統計調査・労働力調査・CPI/東京都区部CPI・GDPデフレーター・建築着工統計・貿易統計 等）
  - 日銀独自の時系列統計データ検索サイト（2026-02-18よりAPI機能を新規提供、キー不要）: 短観・企業物価指数・企業向けサービス価格指数・マネーストック・マネタリーベース・消費活動指数・国際収支統計
  - 業界団体サイトでのPDF手動取得のみ: 百貨店協会・チェーンストア協会・JFA（コンビニ）・日本フードサービス協会・JADA/全軽自協（新車販売）・日工会（工作機械受注）・中小機構（中小企業景況調査）・TSR/TDB（倒産統計）
  - 未解決: 消費総合指数（内閣府）は案内されているExcel直リンクが404で安定した機械可読ソース未確定、要追加調査
- [x] **マクロページへのセクター指標カタログ表示 completed（2026-08-31）** — `indicator_catalog.json`の38指標全てに平易な解説（`description`）とテーマ別タブ区分（`tab`: 消費・小売11／企業活動・景況6／貿易・生産7／労働・物価8／不動産・金融6）を追記。`macro/report.py`にタブUI（純CSS+軽量JS、クリックで切替）を追加し、マクロページ（企業非依存）に全指標を解説つきで掲載。末尾に「森永乳業の個別株ページの場合」という実選択例（`macro/indicators.select_indicators()`を実際に呼んで表示）を小さく併記し、企業非依存の原則を保ったまま選択の仕組みを実感できるようにした。実データで生成・タグバランス検証済み。
- [x] **分野別セクター指標の時系列取得・トレンド可視化 completed（2026-08-31）** — 5タブそれぞれの代表指標を実際にAPIで検証・取得し`data/macro/sectors.json`へ保存、マクロページの各タブ冒頭に実時系列トレンドチャートを追加。
  - `macro/boj.py`（新規）: 日銀の新API（2026-02-18公開、`stat-search.boj.or.jp/api/v1/getDataCode`、キー不要）のクライアント。PDFマニュアルをpymupdfで解析しリクエスト形式を確認、getMetadataでマネーストックM2の系列コード（`MAM1NAM2M2MO`）を特定
  - `macro/fetch_sectors.py`（新規）: 消費・小売＝景気ウォッチャー現状判断DI、企業活動・景況＝法人企業統計 売上高経常利益率（四半期、`0003060191`は1954年からの長期系列と判明）、貿易・生産＝コア機械受注（民需・船舶電力除く季調系列）、労働・物価＝完全失業率、不動産・金融＝マネーストックM2、を実データで取得
  - `macro/estat.py`のget_stats_dataに`extra`パラメータ追加（cat02/cat03等、tab/cat01以外の分類軸を絞り込むため。完全失業率取得で必要になった）
  - `macro/report.py`にタブごとのミニトレンドチャート（renderLineChart再利用）を追加。法人企業景気予測調査（短観含む）は四半期ごとに別々の統計表IDが発番される方式でe-Stat検索が非常に遅く、継続時系列としての取得を断念し代替指標（売上高経常利益率）に切替
  - 残り33指標（38指標中5指標のみ実装）は将来の拡張対象。特に業界団体PDF系・J-Quants登録要のものは優先度低
- [ ] 各データソースの実装拡張（e-Stat以外の残り指標。業界団体PDF系は優先度低・後回し）
- [ ] 指標選択ロジックのチューニング: 実行時に「森永乳業（乳製品製造業）」に対し関連の薄い「鉱工業指数（鉄鋼業）」が選ばれるケースを確認。LLMのカテゴリ判定プロンプトの精度改善が必要
- [ ] 複数企業の一覧/インデックスページ（Phase 5の将来項目）

## Phase 6: 精度改善・運用

- [ ] 評価レポートのレビューとプロンプト調整
- [ ] 複数決算期の比較分析機能
- [ ] Google Drive通知連携
- [ ] モニタリング・ログ整備
- [ ] レポートHTML化 + Vercel配信（コードpublic / レポート実体は認証下。金商法の投資助言リスク回避）

---

## 進捗サマリー（2026-08-27 時点）

> ⚠️ 最新版は本ファイル末尾の **「進捗サマリー（2026-08-30 時点）」** を参照。以下は履歴として残置。

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

---

## 進捗サマリー（2026-08-30 時点）

### 08-27 以降にやったこと

**Chroma DB のデバイス間共有インフラを構築（Phase 2 の運用課題）**
- 複数デバイス（Windows/WSL 混在）で同一の本番インデックスを使うため、非公開 GCS バケットを正本にする方式を採用。
- `scripts/chroma_sync.py`（標準ライブラリのみ・`gcloud storage rsync` ラッパー）+ `Makefile` で `make chroma-pull / chroma-push / chroma-status`。
- `scripts/chroma_export.py` / `chroma_import.py` … chromadb バージョン非依存のポータブルダンプ（npz + jsonl.gz + manifest）。Google Drive 退避用に `make chroma-backup`。
- `chromadb==1.5.9` に厳密固定（永続インデックス形式がバージョン依存で、ズレると別デバイスで読めなくなるため）。
- 手順書 `ir-evaluator/docs/chroma-sync.md`。
- **✅ 初回 GCS アップロード完了** — 本番インデックス済み `chroma_db/`（1600項目・chunk 51,331）を正本バケットへ push 済み。他デバイスは `make chroma-pull` で取得できる状態。
- 将来 GCP VM 常時稼働後は Chroma サーバモード（`chroma run` + `HttpClient`）へ移行予定。

**リポジトリ整備**
- 作業ツリー全体が CRLF 化して全ファイル modified 表示になっていた問題を、`.gitattributes`（`* text=auto eol=lf`）を追加して解消。既存ファイルを LF へ renormalize（内容変更なし）。
- `.claude/settings.local.json`（マシンローカル設定）を `.gitignore` に追加。

### 現在地（フェーズ別ステータス）

| Phase | 状態 | 補足 |
|---|---|---|
| Phase 1: インフラ | ⏳ ほぼ未着手 | EDINET登録・Python環境のみ完了。GCP VM / Vertex AI / Drive OAuth / nanoclaw が残 |
| Phase 2: 審査辞典RAG化 | ✅ 完了 | 全1600項目インデックス済み・GCS 正本化済み。残タスクは entries 投入（下記 項目2）のみ |
| Phase 3: EDINET取得 | ✅ ほぼ完了 | 取得〜テキスト抽出〜業種材料抽出まで検証済み。業種分類キー確定は entries 投入待ち |
| Phase 4: 評価エンジン | 🟢 完全ライブ実行OK | `evaluation/` 一式（業種判定/2段階RAG/プロンプト/LLM層/レポート/CLI）実装・42テスト・森永乳業で完全ライブ実行済み（2026-08-30、`gemini-flash-latest`）。散文クエリのチューニングが残 |
| Phase 4.5: マクロ経済 | 🟡 全体系＋Phase4配線済み | 全体系4指標取得済み＋`macro.context` を評価プロンプト冒頭に前置（1a 完了・実出力で反映確認）。業種別DI/IIP が残 |
| Phase 5: nanoclaw統合 | ⬜ 未着手 | Phase 4 完了後 |
| Phase 6: 精度改善・運用 | ⬜ 未着手 | |

### これから行うべきこと（優先順）

0. **git push** — `08cb929`（.gitattributes）＋ Phase 4 実装コミットが未 push。

1. **Phase 4 の完全ライブ化と精度チューニング** — 準ライブ（森永乳業）まで完了
   - **1a〜1d.** ✅ 完了: マクロ前置 / 2段階RAG＋業種判定 / プロンプト・スキーマ / Markdown レポート
   - **1e.** ✅ 完了（2026-08-30）: EDINET・GOOGLE 実キーを `.env` に投入し `uv run python -m
     evaluation.cli --company 森永乳業 --from 2025-06-01 --to 2025-06-30 --model gemini-flash-latest`
     を実行。森永乳業2025年3月期・総合判定「適格」のレポート生成を確認（`data/reports/`）
   - **1f.** ⬜ **次**: チューニング: 散文クエリ（有報リスク/MD&A）は辞典（表主体）に対してノイズ寄り。
     重み下げ／除外、`n_evidence` と truncate 上限、業種タームの絞り方を調整
   - **1g.** ⬜ 別モデル比較（`--model claude-opus-5` / `gpt-*`。要 optional-deps とキー）

2. **rag/ と dictionary/ の整合（1b の精度向上）** — `rag/industry_map.json` の `entries` が空
   （`_status: PENDING`）かつ `_example` が旧「業種別コレクション」前提のまま。単一コレクション +
   メタデータフィルタ方式へスキーマごと作り直し、`dictionary/manifest.py` の巻/分野/章と
   突き合わせて投入。→ `resolve_filter()` が実際に効くようになり RAG の precision が上がる。
   Phase 3 の「業種分類キー確定」もこれで閉じる。

3. **業種別DI/IIP追加（補助A）** — `industry_key` と配線して業種相対の基準線を成立。IIPは製造業・鉱業のみ（非製造業は第3次産業活動指数）。短観業種区分 → 辞典巻/章のマッピングが必要。項目2の entries に依存。

4. **Phase 1: インフラ（GCP VM / Vertex AI / Drive OAuth / nanoclaw）** — Phase 4 が動いてから

5. **運用**：マクロ更新を手動 → Phase 5 で cron/Scheduler 昇格、レポート HTML 化 + Vercel 配信
6. **Phase 4.6: マクロ/個別株ページ分離の本実装** — ✅ 主要タスク実装・実データ/実LLM検証済み（2026-08-31）。`macro/fetch_jp_market.py` `macro/fetch_us.py` `macro/narrative.py` `macro/report.py` `macro/indicators.py` 新設、`evaluation/report.py` にマクロ前提節を追加、テスト21件追加（計63件通過）。残: 週次自動更新パイプライン・指標選択ロジックのチューニング・日銀API連携（詳細はPhase 4.6セクション参照）

### 既知のブロッカー / 依存
- 項目2（entries 投入）が終わると 1b の RAG precision（`resolve_filter` 有効化）と業種別DI/IIP（項目3）の両方が前進
- Drive 保存は Phase 1 の OAuth 設定待ち
- Chroma は `make chroma-pull` でどのデバイスからも取得可（GCS 正本アップロード済み）
- Gemini 3 系は thinking 既定 on。少ない出力上限だと空応答になるため軽量呼び出しは `light=True`（`llm.py`）
- 評価に Anthropic/OpenAI を使うなら `uv sync --extra anthropic` / `--extra openai` と各 API キー
