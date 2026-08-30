"""評価エンジンのオーケストレーション。

有報 PDF → テキスト抽出 → 企業プロファイル → マクロ環境ブロック
→ 2 段階 RAG → 評価プロンプト → LLM → 構造化結果、までを一気通貫でつなぐ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from edinet.client import DOC_TYPE_ANNUAL_REPORT
from edinet.extract import extract_document
from edinet.industry import CompanyProfile, build_profile

from .classify import classify_industry
from .llm import generate_json
from .prompt import build_prompt
from .retrieval import RetrievalResult, retrieve_evidence
from .schema import EvaluationResult


@dataclass
class EvaluationInput:
    pdf_path: Path
    fiscal_period: str
    meta: dict | None = None
    model: str | None = None
    n_evidence: int = 14
    industry_model: str | None = None


@dataclass
class EvaluationBundle:
    """レポート生成に必要な一式（結果＋根拠）。"""

    result: EvaluationResult
    profile: CompanyProfile
    retrieval: RetrievalResult
    macro_block: str
    industry_terms: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def _macro_block(fiscal_period: str) -> str:
    """マクロ環境ブロックを生成する。overall.json が無ければ注記を返す。"""
    try:
        from macro.context import build_macro_context

        return build_macro_context(fiscal_period, industry_key=None)
    except FileNotFoundError:
        return (
            "## マクロ経済環境\n\n"
            "（data/macro/overall.json が未取得のため環境ブロックを省略。"
            "`uv run python -m macro.fetch` で取得してください）"
        )


def evaluate_document(inp: EvaluationInput) -> EvaluationBundle:
    """1 本の有報 PDF を評価する。"""
    doc = extract_document(inp.pdf_path)
    profile = build_profile(doc, meta=inp.meta)

    industry_terms = classify_industry(profile, model=inp.industry_model)
    macro_block = _macro_block(inp.fiscal_period)
    retrieval = retrieve_evidence(
        profile, doc, industry_terms=industry_terms, n_evidence=inp.n_evidence
    )

    system, user = build_prompt(
        profile, macro_block, retrieval, fiscal_period=inp.fiscal_period
    )
    resp = generate_json(user, system=system, model=inp.model)

    result = EvaluationResult.from_llm_json(
        resp.text,
        company=profile.filer_name or inp.pdf_path.stem,
        fiscal_period=inp.fiscal_period,
        model=f"{resp.provider}:{resp.model}",
    )
    return EvaluationBundle(
        result=result,
        profile=profile,
        retrieval=retrieval,
        macro_block=macro_block,
        industry_terms=industry_terms,
        queries=retrieval.queries,
    )


def _period_from_meta(meta: dict) -> str | None:
    for key in ("periodEnd", "period_end", "periodStart"):
        value = meta.get(key)
        if value:
            return str(value)[:10]
    return None


def evaluate_company(
    company_name: str,
    date_from: str,
    date_to: str,
    *,
    fiscal_period: str | None = None,
    model: str | None = None,
    industry_model: str | None = None,
    n_evidence: int = 14,
    output_dir: Path = Path("data/ir"),
) -> EvaluationBundle:
    """企業名で EDINET から有報を取得し、最新の 1 本を評価する。"""
    from edinet.fetch import fetch_ir_for_company

    docs = fetch_ir_for_company(
        company_name, date_from, date_to, output_dir=output_dir
    )
    annual = [d for d in docs if d.get("docTypeCode") == DOC_TYPE_ANNUAL_REPORT and d.get("pdf_path")]
    if not annual:
        raise ValueError(
            f"'{company_name}' の有価証券報告書が {date_from}〜{date_to} の範囲で見つかりませんでした"
        )
    # 期末日が新しいものを採用。
    annual.sort(key=lambda d: d.get("periodEnd") or d.get("submitDateTime") or "", reverse=True)
    target = annual[0]

    period = fiscal_period or _period_from_meta(target)
    if not period:
        raise ValueError(
            "決算期を特定できませんでした。--fiscal-period で明示してください（例: 2025-03）"
        )

    return evaluate_document(
        EvaluationInput(
            pdf_path=Path(target["pdf_path"]),
            fiscal_period=period,
            meta=target,
            model=model,
            industry_model=industry_model,
            n_evidence=n_evidence,
        )
    )
