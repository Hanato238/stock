"""Markdown レポート生成。"""

import pytest

from evaluation.report import _slug, render_markdown, save_report


@pytest.fixture(autouse=True)
def _no_live_macro_calls(monkeypatch):
    """_macro_premise() が実際に LLM/ファイルI/Oへ飛ばないようにする（macro側は別途テスト済み）。"""
    from macro import indicators as macro_indicators
    from macro import narrative as macro_narrative

    monkeypatch.setattr(macro_narrative, "load_narratives", lambda: None)
    monkeypatch.setattr(macro_indicators, "select_indicators", lambda *a, **k: [])


def test_render_markdown_structure(bundle):
    md = render_markdown(bundle)
    assert md.startswith("# 投資適格性評価レポート: テスト乳業株式会社")
    assert "総合判定: **条件付き適格**" in md
    assert "投資助言では" in md  # ディスクレーマ
    assert "## リスク評点（1=低リスク / 5=高リスク）" in md
    assert "| 財務 | 2 |" in md
    assert "## マクロ経済環境（評価前提）" in md
    assert "## 付録: 参照した審査辞典チャンク" in md


def test_watch_items_sorted_by_severity(bundle):
    md = render_markdown(bundle)
    assert md.index("[高] 原材料価格") < md.index("[低] 為替")


def test_pipe_in_rationale_is_escaped(bundle):
    bundle.result.risk_scores[0].rationale = "A | B"
    md = render_markdown(bundle)
    assert "A \\| B" in md


def test_no_evidence_flag_drops_appendix(bundle):
    md = render_markdown(bundle, include_evidence=False)
    assert "## 付録" not in md


def test_save_report_writes_file(bundle, tmp_path):
    path = save_report(bundle, directory=tmp_path)
    assert path.exists()
    assert path.name == "テスト乳業株式会社_2025-03.md"
    assert "投資適格性評価レポート" in path.read_text(encoding="utf-8")


def test_slug_removes_path_chars():
    assert _slug("A/B:C 社") == "ABC社"
    assert _slug("  ") == "company"


def test_macro_premise_shows_fallback_when_narrative_missing(bundle):
    md = render_markdown(bundle)
    assert "## マクロ前提（詳細は別ページ）" in md
    assert "マクロ経済モニター未生成" in md


def test_macro_premise_quotes_narrative_and_indicators(monkeypatch, bundle):
    from macro import indicators as macro_indicators
    from macro import narrative as macro_narrative
    from macro.narrative import MacroNarrative, NarrativeParagraph

    narrative = MacroNarrative(
        region="japan",
        tone="expand",
        tone_label="緩やかな拡大局面",
        paragraphs=[NarrativeParagraph(heading="景気動向指数", body="CI一致指数は118.5。")],
        verdict="拡大局面が続いている。",
        model="gemini-flash-latest",
    )
    monkeypatch.setattr(macro_narrative, "load_narratives", lambda: {"japan": narrative, "us": narrative})
    monkeypatch.setattr(
        macro_indicators,
        "select_indicators",
        lambda terms, **k: [{"name_ja": "商業動態統計", "publisher": "経済産業省", "frequency": "月次"}],
    )

    md = render_markdown(bundle)
    assert "緩やかな拡大局面" in md
    assert "拡大局面が続いている。" in md
    assert "商業動態統計（経済産業省、月次）" in md
