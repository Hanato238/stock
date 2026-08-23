---
name: notebooklm-pdf-workflow
description: |
  A skill to automate NotebookLM notebook creation, audio content, and slide generation
  from PDFs in a specified Google Drive folder.
  Automatically groups split PDFs (e.g., vol.1/2, part1/2, 上/下) as a single source.
  Also supports generating theme-based audio/slides from an existing notebook,
  and syncing/updating notebook sources with a Google Drive folder.

  Usage:
    /notebooklm [folder-url-or-name] [action]
    /notebooklm [notebook-name-or-id] update-notebook [folder-url-or-name (optional)]
    /notebooklm [notebook-name-or-id] create-theme [theme] [audio|slide|both]

  Actions:
    create-notebook  : Create a notebook and add all PDFs as sources
    update-notebook  : Sync notebook sources with Drive folder (skip existing, delete missing, add new)
    create-audio     : Create Japanese audio content for each PDF (group)
    create-slide     : Create Japanese slides for each PDF (group)
    create-all       : Run all three above (audio & slides generated in parallel)
    create-theme     : Create Japanese audio/slides from all sources using a specified theme
---

# NotebookLM PDF Workflow Skill

## Overview

This skill automates the following workflows using PDFs from a Google Drive folder:

1. **create-notebook**: Create a notebook named after the folder and add all PDFs as sources
2. **update-notebook**: Sync sources in an existing notebook with a Google Drive folder (skip existing, delete missing, add new)
3. **create-audio**: Generate Japanese-language audio content for each PDF (or group)
4. **create-slide**: Generate Japanese-language slides for each PDF (or group)
5. **create-all**: Create audio and slides in parallel for all groups
6. **create-theme**: Generate Japanese-language audio/slides from all sources in a notebook, focused on a user-specified theme

MCP Server used: `gemini-notebook-mcp` (launched via `notebooklm-mcp` command)

> **Language note:** All generated audio content and slides must use `language: "ja"` to ensure Japanese-language output, regardless of the language of the source PDFs.
> **Title naming rule:** All created audio and slide content must use either the exact source PDF title / group name (for `create-audio`, `create-slide`, `create-all`) or the exact user-specified theme string `[theme]` (for `create-theme`). Never prepend notebook titles or AI-generated random titles.

---

## Step 0: Parse Input and Interactive Action Selection

Receive the following from the user:
- `[folder-url-or-name]`: Google Drive folder URL (e.g. `https://drive.google.com/drive/folders/XXXX`) or folder name
- `[action]`: Optional. `create-notebook` / `update-notebook` / `create-audio` / `create-slide` / `create-all` / `create-theme`

### Interactive Action Selection (When `[action]` is omitted or unclear)
If the user executes `/notebooklm [folder-url-or-name]` without specifying an action (or with vague/natural language input), **you MUST call `ask_question` tool** to prompt the user with an interactive multiple-choice dialog:

- **Question:** "「<folder name>」に対して行う処理を選択してください："
- **Options:**
  1. `ノートブック作成 (create-notebook)`: フォルダ内の全PDFを追加して新規作成
  2. `ノートブック更新・同期 (update-notebook)`: 既存ノートブックのソースをDriveと同期（スキップ/削除/追加）
  3. `音声解説を作成 (create-audio)`: 各PDF(グループ)の日本語音声解説を生成
  4. `スライドを作成 (create-slide)`: 各PDF(グループ)の日本語スライドを生成
  5. `全部一括作成 (create-all)`: ノートブック作成・音声・スライドを全自動で一括並列生成
  6. `テーマ指定コンテンツ作成 (create-theme)`: 特定テーマに絞った音声/スライドを生成

Proceed using the action selected by the user in the interactive modal.

**Resolving the folder ID:**
- If a URL is given: extract the ID from the end of the URL (e.g. `https://drive.google.com/drive/folders/FOLDER_ID` → `FOLDER_ID`)
- If a folder name is given: use the `gws` Drive search tool to find the folder ID
- Also record the folder name (= notebook title)

---

## Step 1: Collect and Analyze PDFs

### 1-1. Recursively collect all PDFs in the folder

Use the `gws` Drive API to recursively list all PDFs in the specified folder and all subfolders. Record for each file:
- `file_id`: Google Drive file ID
- `file_name`: Filename without extension
- `drive_url`: `https://drive.google.com/file/d/{file_id}/view`

### 1-2. Group split PDFs

Group the collected PDFs using the following rules:

**Volume/part suffix patterns to strip from the end of filenames:**
- Japanese kanji: `上`, `中`, `下`, `上巻`, `下巻`, `上中`, `中下`
- `_part1`, `_part2`, `_part3`, ... (`_partN`)
- `_1`, `_2`, `_3`, ... (underscore + trailing digits only)
- `_1/2`, `_2/2` style (fractional notation)
- `(1)`, `(2)` etc. (parenthesized numbers)

**Grouping algorithm:**
1. For each filename, strip the above patterns from the end to derive a "base name"
2. Files sharing the same base name form one group
3. PDFs that do not match any pattern form a single-file group
4. The group title = base name (common part after stripping volume suffixes)
5. Files within a group are sorted alphabetically by filename
6. After stripping, trim any trailing spaces, underscores, or hyphens from the base name

**Examples:**
```
第1章上.pdf, 第1章下.pdf   → Group "第1章" (2 files)
第2章_part1.pdf, 第2章_part2.pdf → Group "第2章" (2 files)
総論_1/2.pdf, 総論_2/2.pdf → Group "総論" (2 files)
緒言.pdf                   → Group "緒言" (single file)
```

---

## Step 2: Check and Create Notebook (create-notebook / create-all)

### 2-1. Check for existing notebook

Call `notebook_list` to get the list of notebooks.

```
mcp tool: notebook_list
params: { max_results: 100 }
```

Check whether a notebook with the same name as the folder already exists (exact title match).

### 2-2. Create notebook (only if it does not exist)

If not found, create it:

```
mcp tool: notebook_create
params: { title: "<folder name>" }
```

Record the `notebook_id` from the response.

### 2-3. Check existing sources

```
mcp tool: source_list_drive
params: { notebook_id: "<notebook_id>", skip_freshness: true }
```

Retrieve the list of existing source titles in the notebook.

### 2-4. Add sources per group

**For each group (process files in order):**

If a source with the same name as the group title already exists in the notebook, skip it.

Otherwise, add each PDF in the group sequentially using `source_add` as a Google Drive source:

```
mcp tool: source_add
params:
  notebook_id: "<notebook_id>"
  source_type: "drive"
  document_id: "<file_id>"
  doc_type: "pdf"
  wait: true
  wait_timeout: 180
```

> **Note:** For split PDFs, all files in a group are added individually to the notebook, but content generation (audio/slides) will reference all source IDs in the group as a single unit.

**After adding:** Record each file's `source_id` and store it in the group's source ID list.

---

## Step 3: Create Audio Content (create-audio / create-all)

> **Language requirement:** Audio content must always be generated in Japanese (`language: "ja"`).

### 3-1. Check existing content

```
mcp tool: studio_status
params:
  notebook_id: "<notebook_id>"
  action: "status"
  limit: 100
```

Retrieve the list of existing audio content titles (`artifact_type: audio`).

### 3-2. Create audio content per group

**For each group:**

If an audio item with a matching title (group title) already exists, skip it.

Otherwise, get user approval and create:

```
mcp tool: studio_create
params:
  notebook_id: "<notebook_id>"
  artifact_type: "audio"
  source_ids: ["<source_id_1>", "<source_id_2>", ...]  # all source IDs in the group
  title: "<group title>"
  language: "ja"
  audio_format: "deep_dive"
  audio_length: "default"
  confirm: true
```

> **Important:** The `confirm` parameter requires user approval before being set to `true`. First present the plan to the user and obtain approval, then execute with `confirm: true`.

After creation, poll `studio_status` to verify completion.

---

## Step 4: Create Slides (create-slide / create-all)

> **Language requirement:** Slides must always be generated in Japanese (`language: "ja"`).

### 4-1. Check existing content

Check for existing slides (`artifact_type: slide_deck`) via `studio_status` (same as Step 3-1).

### 4-2. Create slides per group

**For each group:**

If a slide with the same title already exists, skip it.

After user approval:

```
mcp tool: studio_create
params:
  notebook_id: "<notebook_id>"
  artifact_type: "slide_deck"
  source_ids: ["<source_id_1>", "<source_id_2>", ...]  # all source IDs in the group
  title: "<group title>"
  language: "ja"
  slide_format: "detailed_deck"
  slide_length: "default"
  orientation: "landscape"
  confirm: true
```

---

## Step 5: Parallel Execution (create-all)

For `create-all`, generate audio and slides **in parallel**:

1. Once the notebook and all sources are ready (Step 2 complete)
2. For each group, send both the audio creation request (`studio_create` audio) and the slide creation request (`studio_create` slide_deck) **simultaneously**
3. Then poll `studio_status` to confirm completion of all artifacts

---

## Step 6: Error Handling and Completion Report

### Error Handling
- Do not abort on individual content creation failures
- Record the failed group name and error message and continue processing

### Completion Report
After processing, report to the user in Japanese with the following format:

```
## 処理結果サマリー

### ノートブック
- 名前: <notebook name>
- ID: <notebook_id>
- ステータス: 作成済み / 既存を使用

### 処理したグループ（全N件）

| グループ名 | PDF数 | ソース追加 | 音声コンテンツ | スライド |
|-----------|-------|-----------|--------------|--------|
| 第1章     | 2     | ✅ 追加済  | ✅ 作成済    | ✅ 作成済 |
| 緒言      | 1     | ⏭️ スキップ | ✅ 作成済   | ❌ 失敗  |

### 失敗したコンテンツ（あれば）
- 第2章 スライド: <error details>

### 実行時間
約XX分XX秒
```

---

## Notes

1. **Auth errors:** Guide the user to run `nlm login` in a terminal
2. **Rate limiting:** Add a ~5 second delay between `studio_create` calls when processing many groups
3. **source_ids:** Record `source_id` values accurately from each `source_add` response
4. **Title matching:** Normalize for case and full-width/half-width differences when comparing titles
5. **Base name trimming:** After stripping volume suffixes, also trim trailing spaces, underscores, and hyphens

---

## create-theme Action

### Overview

Using **all sources** in a specified notebook, generate Japanese-language audio content and/or slides focused on a user-specified theme.

### Usage

```
/notebooklm [notebook-name-or-id] create-theme [theme] [audio|slide|both]
```

- `[notebook-name-or-id]`: Notebook name or notebook UUID
- `[theme]`: Theme for the audio/slide content (e.g. `血小板減少の治療戦略`)
  - If the argument is missing or too vague, ask clarifying questions interactively
- `[audio|slide|both]`: Type of content to generate (defaults to `both` if omitted)

> **Language requirement:** All generated audio and slides must be in Japanese (`language: "ja"`).

---

### CT-Step 0: Parse Input and Clarify Theme

1. Resolve the notebook ID from `[notebook-name-or-id]`:
   - If a UUID, use it directly
   - If a name, call `notebook_list` and find the matching notebook by title
2. If `[theme]` is missing or too vague/short, ask the user to clarify:
   - "What theme would you like to use for the audio/slides? (e.g. 血小板減少の治療戦略, DICの病態と管理)"
   - Ask follow-up questions as needed to sharpen the theme
3. If `[audio|slide|both]` is omitted, default to `both`

---

### CT-Step 1: Verify Notebook

```
mcp tool: notebook_list
params: { max_results: 100 }
```

Identify the notebook by name or ID, and record `notebook_id` and `notebook_title`.

If no matching notebook is found, report an error to the user and stop.

---

### CT-Step 2: Check for Existing Content

```
mcp tool: studio_status
params:
  notebook_id: "<notebook_id>"
  action: "status"
  limit: 100
```

Check whether the planned content title (`<theme>`) already exists:
- Audio (`audio`): skip if a matching title exists
- Slides (`slide_deck`): skip if a matching title exists

**Content title format:** `<theme>`（指定したテーマ原文そのまま）
(e.g. theme "血小板減少の治療" → title "血小板減少の治療")

---

### CT-Step 3: Create Audio Content (audio / both)

Do not specify `source_ids` (omit to use all sources in the notebook).

Always set the `custom_prompt` below to instruct an expert-commentary style:

```
mcp tool: studio_create
params:
  notebook_id: "<notebook_id>"
  artifact_type: "audio"
  title: "<theme>"
  language: "ja"
  audio_format: "deep_dive"
  audio_length: "default"
  focus_prompt: "<theme>"
  custom_prompt: |
    この音声コンテンツは、当該分野の専門家（医師・研究者・上級実務家など）が
    同僚や後進に向けて行う「専門家解説レクチャー」のスタイルで作成してください。
    具体的には以下の点を守ってください：
    ・一般向けの説明や過度に噛み砕いた表現は避け、専門用語を適切に使用する
    ・エビデンスや文献に基づいた根拠を示しながら解説する
    ・臨床的・実務的な視点から「なぜそうなのか」「実際どう使うか」を深く掘り下げる
    ・二人の会話形式であっても、単なる雑談ではなくカンファレンス・勉強会の議論のようなトーンを保つ
    ・「<theme>」というテーマに関する重要なポイント・落とし穴・最新知見を網羅する
  confirm: true
```

> **Important:** The `confirm` parameter requires user approval before being set to `true`. Present the plan to the user first, then execute with `confirm: true`.

---

### CT-Step 4: Create Slides (slide / both)

```
mcp tool: studio_create
params:
  notebook_id: "<notebook_id>"
  artifact_type: "slide_deck"
  title: "<theme>"
  language: "ja"
  slide_format: "detailed_deck"
  slide_length: "default"
  orientation: "landscape"
  focus_prompt: "<theme>"
  confirm: true
```

---

### CT-Step 5: Parallel Execution (both)

For `both`, send the audio creation request and the slide creation request **simultaneously**.

Then poll `studio_status` to confirm completion of both artifacts.

---

### CT-Step 6: Completion Report

```
## 処理結果サマリー（create-theme）

### 対象ノートブック
- 名前: <notebook_title>
- ID: <notebook_id>

### テーマ
<theme>

### 作成結果

| コンテンツ | タイトル | ステータス |
|-----------|---------|----------|
| 音声コンテンツ | <theme> | ✅ 作成済 / ⏭️ スキップ / ❌ 失敗 |
| スライド      | <theme> | ✅ 作成済 / ⏭️ スキップ / ❌ 失敗 |

### 失敗した場合のエラー詳細（あれば）
- <error details>
```

---

## update-notebook Action

### Overview

Syncs the sources of an existing NotebookLM notebook with the current PDFs in its corresponding Google Drive folder:
- **Existing sources matching Drive PDFs**: Skipped (`⏭️ スキップ`)
- **Sources in notebook no longer in Drive folder**: Deleted (`🗑️ 削除`)
- **New PDFs in Drive folder not in notebook**: Added (`✅ 追加`)

### Usage

```
/notebooklm [notebook-name-or-id] update-notebook [folder-url-or-name (optional)]
```

- `[notebook-name-or-id]`: Notebook name or notebook UUID
- `[folder-url-or-name (optional)]`: Target Google Drive folder URL or name. If omitted, uses the notebook's title as the Drive folder name.

---

### UN-Step 0: Resolve Notebook and Drive Folder

1. **Resolve Notebook:**
   - Call `notebook_list` with `{ max_results: 100 }`
   - Match `[notebook-name-or-id]` by title or UUID to obtain `notebook_id` and `notebook_title`.
2. **Resolve Drive Folder:**
   - If `[folder-url-or-name]` is provided, resolve folder ID (from URL or via `gws` search).
   - If omitted, use `notebook_title` to search for the matching Google Drive folder ID via `gws`.

---

### UN-Step 1: Fetch Current Sources & Drive PDFs

1. **List Existing Sources in Notebook:**
   ```
   mcp tool: source_list_drive
   params: { notebook_id: "<notebook_id>", skip_freshness: true }
   ```
   Record each existing source's `source_id`, `title`, and associated document ID / Drive URL (if available).

2. **Recursively Collect PDFs from Drive Folder:**
   Use `gws` to list all current PDFs in the resolved Drive folder and subfolders. Record for each file:
   - `file_id`: Google Drive file ID
   - `file_name`: Filename without extension (and full filename)

---

### UN-Step 2: Compare and Sync Sources

1. **Identify Actions:**
   - **Keep / Skip**: Sources in the notebook whose document ID / title match a PDF currently in the Drive folder.
   - **Delete**: Sources in the notebook whose title/document ID does NOT match any PDF currently in the Drive folder.
   - **Add**: PDFs currently in the Drive folder whose document ID / title are NOT in the notebook.

2. **Execute Deletions (`source_delete`):**
   For each source to delete:
   ```
   mcp tool: source_delete
   params:
     notebook_id: "<notebook_id>"
     source_id: "<source_id>"
   ```

3. **Execute Additions (`source_add`):**
   For each PDF to add:
   ```
   mcp tool: source_add
   params:
     notebook_id: "<notebook_id>"
     source_type: "drive"
     document_id: "<file_id>"
     doc_type: "pdf"
     wait: true
     wait_timeout: 180
   ```

---

### UN-Step 3: Completion Report

Report summary to the user in Japanese:

```
## 処理結果サマリー（update-notebook）

### 対象ノートブック
- 名前: <notebook_title>
- ID: <notebook_id>
- 連動ドライブフォルダ: <folder_name>

### 同期結果

| ステータス | 件数 | 詳細 |
|-----------|------|------|
| ⏭️ スキップ | X件 | 既存のPDF（変更なし） |
| ✅ 追加 | Y件 | 新規追加されたPDF |
| 🗑️ 削除 | Z件 | ドライブから削除されたPDF |

### 変更ログ
- [追加] <ファイル名>
- [削除] <ソースタイトル>
```
