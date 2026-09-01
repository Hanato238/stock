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
        "",
        _macro_premise(bundle),
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


def _macro_premise(bundle: EvaluationBundle) -> str:
    """マクロ経済モニター（Phase 4.6）への要約引用＋リンク。

    LLM は呼ばない: 総合見立ては macro.report が生成時に data/macro/narrative.json
    へ保存済みのものを読むだけ。関連セクター指標は業種タームから軽量 LLM で1回だけ
    選ぶ（evaluation.classify と同じ、失敗しても評価本体は止めない設計）。
    """
    lines = ["## マクロ前提（詳細は別ページ）", ""]

    try:
        from macro.narrative import load_narratives, load_sector_narratives

        narratives = load_narratives()
        sector_narratives = load_sector_narratives()
    except ImportError:
        narratives = None
        sector_narratives = None

    if narratives and "japan" in narratives:
        n = narratives["japan"]
        lines.append(f"**日本の景気サイクル総合見立て:** {n.tone_label}")
        lines.append("")
        lines.append(n.verdict or "（見立てなし）")
        lines.append("")
    else:
        lines.append("（マクロ経済モニター未生成。`uv run python -m macro.report` で生成すると要約が表示されます）")
        lines.append("")

    try:
        from macro.indicators import IndicatorSelectionError, select_indicators

        indicators = select_indicators(bundle.industry_terms) if bundle.industry_terms else []
    except (ImportError, IndicatorSelectionError):
        indicators = []

    try:
        from macro.context import _fmt_value
        from macro.indicators import load_sector_series

        sector_series = load_sector_series()
    except ImportError:
        sector_series = {}
        _fmt_value = None

    if indicators:
        lines.append("**関連セクター指標（業種に応じて自動選択）:**")
        lines.append("")
        by_tab: dict[str, list[dict]] = {}
        tab_order: list[str] = []
        for ind in indicators:
            tab = ind.get("tab", "")
            if tab not in by_tab:
                by_tab[tab] = []
                tab_order.append(tab)
            by_tab[tab].append(ind)

        for tab in tab_order:
            lines.append(f"【{tab}】" if tab else "（分野不明）")
            for ind in by_tab[tab]:
                line = f"- {ind['name_ja']}（{ind.get('publisher', '不明')}、{ind.get('frequency', '')}）"
                series = sector_series.get(ind.get("key", ""))
                latest = series.latest() if series else None
                if latest is not None and _fmt_value is not None:
                    line += f" — 直近{_fmt_value(latest.value, series.unit)}（{latest.date}）"
                lines.append(line)
            sn = (sector_narratives or {}).get(tab)
            if sn:
                lines.append("")
                lines.append(sn.body)
            lines.append("")

    lines.append("詳細な景気動向指数（CI/DI）・CFNAI・株式市場チャート等は `data/macro/report.html` を参照。")
    return "\n".join(lines)


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
