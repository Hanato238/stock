"""Markdown レポート生成。"""

import pytest

from evaluation.report import _slug, render_markdown, save_report


@pytest.fixture(autouse=True)
def _no_live_macro_calls(monkeypatch):
    """_macro_premise() が実際に LLM/ファイルI/Oへ飛ばないようにする（macro側は別途テスト済み）。"""
    from macro import indicators as macro_indicators
    from macro import narrative as macro_narrative

    monkeypatch.setattr(macro_narrative, "load_narratives", lambda: None)
    monkeypatch.setattr(macro_narrative, "load_sector_narratives", lambda: None)
    monkeypatch.setattr(macro_indicators, "select_indicators", lambda *a, **k: [])
    monkeypatch.setattr(macro_indicators, "load_sector_series", lambda: {})


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


def test_macro_premise_groups_indicators_by_tab_and_quotes_sector_narrative_once(monkeypatch, bundle):
    from macro import indicators as macro_indicators
    from macro import narrative as macro_narrative
    from macro.narrative import SectorNarrative

    monkeypatch.setattr(macro_narrative, "load_narratives", lambda: None)
    monkeypatch.setattr(
        macro_narrative,
        "load_sector_narratives",
        lambda: {
            "消費・小売": SectorNarrative(
                tab="消費・小売", indicator_key="economy_watchers_di", body="食品支出は堅調に推移。"
            )
        },
    )
    monkeypatch.setattr(
        macro_indicators,
        "select_indicators",
        lambda terms, **k: [
            {"name_ja": "家計調査", "publisher": "総務省統計局", "frequency": "月次", "tab": "消費・小売"},
            {"name_ja": "商業動態統計", "publisher": "経済産業省", "frequency": "月次", "tab": "消費・小売"},
            {"name_ja": "完全失業率", "publisher": "総務省統計局", "frequency": "月次", "tab": "労働・物価"},
        ],
    )

    md = render_markdown(bundle)
    assert "【消費・小売】" in md
    assert "【労働・物価】" in md
    assert md.count("食品支出は堅調に推移。") == 1  # 同一タブの指標が複数でも解説は1回のみ
    assert "家計調査（総務省統計局、月次）" in md
    assert "商業動態統計（経済産業省、月次）" in md


def test_macro_premise_appends_latest_value_when_sector_series_available(monkeypatch, bundle):
    from macro import indicators as macro_indicators
    from macro import narrative as macro_narrative
    from macro.fred import Observation
    from macro.store import SeriesData

    monkeypatch.setattr(macro_narrative, "load_narratives", lambda: None)
    monkeypatch.setattr(
        macro_indicators,
        "select_indicators",
        lambda terms, **k: [
            {"name_ja": "家計調査", "publisher": "総務省統計局", "frequency": "月次", "tab": "消費・小売", "key": "household_survey"},
            {"name_ja": "商業動態統計", "publisher": "経済産業省", "frequency": "月次", "tab": "消費・小売", "key": "commerce_dynamics"},
        ],
    )
    monkeypatch.setattr(
        macro_indicators,
        "load_sector_series",
        lambda: {
            "household_survey": SeriesData(
                key="household_survey",
                label="家計調査 消費支出",
                source="e-Stat:0002070001",
                frequency="m",
                unit="円",
                observations=[Observation(date="2026-06-01", value=290886.0)],
            )
        },
    )

    md = render_markdown(bundle)
    assert "家計調査（総務省統計局、月次） — 直近290886円（2026-06-01）" in md
    # 実データがない指標（commerce_dynamics）は名称のみのまま。
    assert "商業動態統計（経済産業省、月次）\n" in md
