# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo currently holds one active project:

- **`ir-evaluator/`** — a Python system that ingests Japanese corporate IR filings (有価証券報告書/決算短信 via EDINET) and produces investment-grade evaluation reports, grounded in a proprietary industry-review dictionary (RAG over Chroma) and macroeconomic context (FRED/e-Stat/日銀). All commands below assume `cd ir-evaluator` first.
- `packages/claude-env/` — unrelated tooling package (its own `package.json`).
- `TODO.md` (repo root) — the authoritative, continuously-updated project log for `ir-evaluator`. It records design decisions, phase status, dead ends, and "what to do next" in detail (in Japanese). **Read the tail of this file before starting nontrivial work** — it usually has the most current picture of what's in progress and why.

## Commands (run from `ir-evaluator/`)

```bash
uv sync                      # install deps (uv-managed venv)
uv run pytest                # run all tests, or: make test
uv run pytest tests/test_prompt.py::test_name   # single test
uv run ruff check .          # lint, or: make lint

# Evaluate one company end-to-end
uv run python -m evaluation.cli --pdf data/ir/<company>/<doc>.pdf --fiscal-period 2025-03
uv run python -m evaluation.cli --company 森永乳業 --from 2025-06-01 --to 2025-07-31
uv run python -m evaluation.cli --pdf ... --fiscal-period 2025-03 --model claude-opus-5

# Macro data pipeline (must run before evaluation prompts include macro context)
uv run python -m macro.fetch            # FRED + e-Stat CPI -> data/macro/overall.json
uv run python -m macro.report           # generates data/macro/report.html (macro monitor page)

# Chroma DB sync (production dictionary index lives in a private GCS bucket, not git)
make chroma-pull / chroma-push / chroma-status
make chroma-backup            # version-independent npz+jsonl.gz dump for Drive backup

# Dictionary search CLI (sanity-check retrieval quality against the index)
uv run python -m dictionary.search "クエリ文字列"
```

Tests are pure unit tests with no external API calls (fixtures in `tests/conftest.py` build fake `CompanyProfile`/`RetrievalResult` objects). `conftest.py` at the project root exists solely to put the project root on `sys.path` so `evaluation`, `edinet`, `macro`, etc. resolve as top-level packages.

## Architecture

The pipeline is a straight-line orchestration, wired together in `evaluation/engine.py`:

```
PDF (EDINET) → edinet/extract.py (text) → edinet/industry.py (CompanyProfile)
   → evaluation/classify.py (LLM: industry terms, light/no-thinking call)
   → macro/context.py (macro block, read from cached data/macro/*.json)
   → evaluation/retrieval.py (2-stage RAG against Chroma dictionary index)
   → evaluation/prompt.py + schema.py (build prompt, define output JSON shape)
   → evaluation/llm.py (provider-dispatched call: gemini-*/claude-*/gpt-*)
   → evaluation/schema.py (lenient JSON parse → EvaluationResult)
   → evaluation/report.py (render Markdown report to data/reports/)
```

Each package has one job:

- **`dictionary/`** — one-time offline pipeline that turns the scanned-image PDF dictionary (業種別審査辞典) into a single Chroma collection with metadata filters (`code`/`volume`/`field`/`section`). `manifest.py` scans the dictionary directory structure directly (no static industry-mapping JSON) — item code/volume/field/chapter come from `split_manifest.json` on disk. OCR is Gemini Vision (the PDFs have no text layer); `build_index_batch.py` (Gemini Batch API, cost-optimized, resumable in 200-item chunks) is the production path — `build_index.py` (Standard API, synchronous) is kept only for local verification, not production use.
- **`edinet/`** — EDINET API v2 client: company name → EDINET code, filing list, PDF download, text extraction (pymupdf + NFKC normalization), and industry-material extraction from 【事業の内容】/【関係会社の状況】 sections (`industry.py` → `CompanyProfile`).
- **`rag/`** — the *company profile → dictionary collection/filter* resolution layer (`mapping.py: resolve()`). This is currently a stub: `rag/industry_map.json` has empty `entries` (status `PENDING`), so `resolve_filter()` always returns "no filter" (search the whole single collection). Populating `entries` (aligned to `dictionary/manifest.py`'s volume/field/chapter scheme) is a known pending task that unlocks both RAG precision and industry-relative macro baselines (短観DI/IIP) — see TODO.md items under "rag/ と dictionary/ の整合".
- **`evaluation/`** — the evaluation engine itself:
  - `llm.py` — provider-agnostic LLM call. Provider is inferred from the model name prefix (`gemini-*` → Gemini via `google-genai`, `claude-*` → Anthropic, `gpt-*`/`o1-*`/`o3-*` → OpenAI, or explicit `provider:model`). Anthropic/OpenAI SDKs are optional deps (`uv sync --extra anthropic` / `--extra openai`). All SDK-specific exceptions normalize to `LLMError`. Default model comes from `EVAL_MODEL` env var.
  - `classify.py` — LLM-based industry term extraction (dictionary entries are financial-metric tables, so noun-phrase industry-name queries retrieve much better than prose).
  - `retrieval.py` — 2-stage RAG: resolve industry filter (via `rag.mapping.resolve()`) → build queries (industry terms + credit-review-perspective queries + filing risk/MD&A chunks) → vector search → cap at 3 results per dictionary code, sorted by distance.
  - `prompt.py` / `schema.py` — prompt assembly order is macro context block → company profile → dictionary findings (with source codes) → filing excerpts → output schema; `schema.py` does lenient JSON parsing (strips code fences, balances brackets, normalizes verdict vocabulary) since LLMs don't always return clean JSON.
  - `report.py` — renders the Markdown report (risk score table, flagged items, macro premise section, dictionary citations appendix, financial-instruments-law disclaimer).
- **`macro/`** — macro/sector data, decoupled from company evaluation and updated on its own (intended weekly) cadence; evaluation reads cached `data/macro/*.json`/`narrative.json` rather than hitting FRED/e-Stat per-company. Two page types exist and are deliberately kept separate (see TODO.md "Phase 4.6"): a company-independent **macro monitor page** (`macro/report.py` → `data/macro/report.html`, covering Japan/US indicators, tone narrative via `narrative.py`) and the **per-company evaluation report**, which only embeds a short macro summary + link back to the monitor page. `indicators.py` maps a company's industry to a sector-indicator catalog (`data/macro/indicator_catalog.json`) using the same "LLM classifies, code computes" pattern as `classify.py`.
- **`scripts/chroma_*.py`** — Chroma DB is ~1.2GB, git-ignored, and never committed (source dictionary is licensed content; repo is intended to eventually go public). The private GCS bucket is the source of truth; `chroma_sync.py` (stdlib-only, works without the project venv via `uv run --no-project`) pulls/pushes it. Only one machine should ever push (rsync is last-writer-wins). `chromadb` is pinned exactly (`==1.5.9`) because its on-disk index format is version-dependent — see `docs/chroma-sync.md` before touching sync or upgrading chromadb.

## Key conventions

- **LLM risk score direction**: 1 = low risk, 5 = high risk (aligned with "items needing attention" direction) — don't flip this when touching scoring code.
- **`light=True` on `generate_json`** disables Gemini thinking for simple classification/extraction calls. Gemini 3 models default thinking to on; with a small `max_output_tokens` they can burn the whole budget on thinking and return an empty response. Use `light=True` for classify-style calls, not for the main evaluation call.
- **Macro data is never fetched live during company evaluation.** `evaluation/engine.py`'s `_macro_block()` reads from `data/macro/overall.json` (falls back to a placeholder note if absent, telling the user to run `macro.fetch`). Don't wire live FRED/e-Stat calls into the per-company path.
- Everything Japanese-language and full-width in prompts/reports/dictionary content is intentional — this is a JP-market tool; don't "normalize" Japanese business terminology or convert it to English.
- Windows/WSL/devcontainer users may share a working tree via bind mount — `.venv` is OS-specific and must not be touched cross-OS. Chroma-sync scripts default to `uv run --no-project` for this reason; don't change that default without checking `docs/chroma-sync.md`.
