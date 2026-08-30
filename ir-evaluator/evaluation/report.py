"""評価結果 → Markdown レポート。

保存は Phase 1（Google Drive OAuth）完了までローカルファイルのみ。
`save_report()` は data/reports/{会社名}_{決算期}.md に書き出す。
"""

from __future__ import annotations

import re
from pathlib import Path

from .engine import EvaluationBundle
from .schema import EvaluationResult

_SCORE_BAR = {1: "▁ 低", 2: "▃ やや低", 3: "▅ 中", 4: "▆ やや高", 5: "█ 高"}
_DEFAULT_REPORT_DIR = Path("data/reports")

_DISCLAIMER = (
    "> 本レポートは審査辞典 RAG と LLM による自動生成物であり、投資助言では"
    "ありません。金融商品取引法上の投資判断は利用者自身の責任で行ってください。"
)


def render_markdown(bundle: EvaluationBundle, *, include_evidence: bool = True) -> str:
    r = bundle.result
    parts = [
        f"# 投資適格性評価レポート: {r.company}",
        "",
        f"- 決算期: {r.fiscal_period}",
        f"- 総合判定: **{r.overall_grade}**",
        f"- 業種判定: {' / '.join(bundle.industry_terms) if bundle.industry_terms else '（LLM判定なし）'}",
        f"- 使用モデル: {r.model}",
        f"- 生成日時: {r.generated_at}",
        "",
        _DISCLAIMER,
        "",
        "## 総括",
        "",
        r.summary or "（なし）",
        "",
        _risk_table(r),
        "",
        _watch_items(r),
        "",
        "## 投資適格性コメント",
        "",
        r.investment_comment or "（なし）",
        "",
        "## マクロ経済環境（評価前提）",
        "",
        bundle.macro_block.strip(),
    ]
    if include_evidence:
        parts += ["", _evidence_appendix(bundle)]
    return "\n".join(parts).rstrip() + "\n"


def _risk_table(r: EvaluationResult) -> str:
    lines = [
        "## リスク評点（1=低リスク / 5=高リスク）",
        "",
        "| 軸 | 評点 | 目安 | 根拠 |",
        "|----|------|------|------|",
    ]
    for s in r.risk_scores:
        bar = _SCORE_BAR.get(s.score, str(s.score))
        rationale = s.rationale.replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {s.axis} | {s.score} | {bar} | {rationale} |")
    return "\n".join(lines)


def _watch_items(r: EvaluationResult) -> str:
    if not r.watch_items:
        return "## 要注意項目\n\n（特記事項なし）"
    order = {"高": 0, "中": 1, "低": 2}
    items = sorted(r.watch_items, key=lambda w: order.get(w.severity, 1))
    lines = ["## 要注意項目", ""]
    for w in items:
        lines.append(f"### [{w.severity}] {w.title}")
        lines.append("")
        lines.append(w.detail or "（詳細なし）")
        lines.append("")
    return "\n".join(lines).rstrip()


def _evidence_appendix(bundle: EvaluationBundle) -> str:
    ev = bundle.retrieval.evidence
    lines = ["## 付録: 参照した審査辞典チャンク", ""]
    if bundle.retrieval.where:
        lines.append(f"メタデータフィルタ: `{bundle.retrieval.where}`")
    else:
        lines.append("メタデータフィルタ: なし（単一コレクション全体を検索）")
    lines.append("")
    for i, e in enumerate(ev, 1):
        snippet = e.text.strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        lines.append(f"{i}. **{e.citation}** — 距離 {e.distance:.3f}")
        lines.append(f"   > {snippet}")
    return "\n".join(lines)


def _slug(text: str) -> str:
    text = re.sub(r"\s+", "", text.strip())
    text = re.sub(r'[/\\:*?"<>|]', "", text)
    return text or "company"


def save_report(
    bundle: EvaluationBundle,
    *,
    directory: Path = _DEFAULT_REPORT_DIR,
    include_evidence: bool = True,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    r = bundle.result
    path = directory / f"{_slug(r.company)}_{_slug(r.fiscal_period)}.md"
    path.write_text(render_markdown(bundle, include_evidence=include_evidence), encoding="utf-8")
    return path
