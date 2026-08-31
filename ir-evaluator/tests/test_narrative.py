"""マクロの読み方・総合見立て生成（LLM呼び出しはモック）。"""

import pytest

from macro import narrative
from macro.fred import Observation
from macro.narrative import (
    MacroNarrative,
    NarrativeError,
    NarrativeParagraph,
    _recent,
    _series_fact,
    generate_narrative,
    load_narratives,
    save_narratives,
)
from macro.store import SeriesData


def _series(key, label, unit, values: list[tuple[str, float]]) -> SeriesData:
    return SeriesData(
        key=key,
        label=label,
        source="TEST",
        frequency="m",
        unit=unit,
        observations=[Observation(date=d, value=v) for d, v in values],
    )


def test_series_fact_formats_index_with_change():
    s = _series("ci_coincident", "CI一致指数", "指数(2020=100)", [("2025-01-01", 110.0), ("2025-06-01", 118.5)])
    line = _series_fact(s)
    assert "CI一致指数: 118.5" in line
    assert "期間内変化" in line
    assert "%" in line


def test_series_fact_uses_points_not_percent_for_bare_index_near_zero():
    # CFNAI のようにゼロ近傍で振動する裸の「指数」は変化率(%)にすると値が暴れるため
    # 絶対差（ポイント）で表示する。
    s = _series("cfnai", "CFNAI", "指数", [("2025-01-01", -0.5), ("2025-07-01", -0.08)])
    line = _series_fact(s)
    assert "ポイント" in line
    assert "%" not in line.split("期間内変化")[1].split("ポイント")[0]


def test_series_fact_uses_pt_for_rate_unit():
    s = _series("policy_rate", "政策金利", "%", [("2025-01-01", 0.1), ("2026-01-01", 0.84)])
    line = _series_fact(s)
    assert "%pt" in line


def test_series_fact_handles_no_data():
    s = _series("x", "X指標", "%", [])
    assert _series_fact(s) == "- X指標: データなし"


def test_recent_trims_long_history():
    values = [("2015-01-01", 100.0), ("2026-07-01", 162.3)]
    s = _series("usd_jpy", "USD/JPY", "円/ドル", values)
    trimmed = _recent(s, days=730)
    assert len(trimmed.observations) == 1
    assert trimmed.observations[0].date == "2026-07-01"


def test_generate_narrative_rejects_unknown_region():
    with pytest.raises(ValueError, match="japan"):
        generate_narrative("europe")


def test_generate_narrative_missing_data_raises(monkeypatch):
    monkeypatch.setattr(narrative, "load_bundle", lambda filename: {})
    with pytest.raises(NarrativeError):
        generate_narrative("japan")


def test_generate_narrative_success(monkeypatch):
    market = {
        "nikkei225": _series("nikkei225", "日経平均株価", "円", [("2026-08-01", 66000.0)]),
        "ci_coincident": _series("ci_coincident", "CI一致指数", "指数(2020=100)", [("2026-06-01", 118.5)]),
    }
    overall = {"usd_jpy": _series("usd_jpy", "USD/JPY", "円/ドル", [("2026-07-01", 162.3)])}

    def fake_load_bundle(filename):
        if filename == "japan_market.json":
            return market
        if filename == "overall.json":
            return overall
        raise FileNotFoundError(filename)

    monkeypatch.setattr(narrative, "load_bundle", fake_load_bundle)

    def fake_generate_json(prompt, *, model=None, max_output_tokens=None, system=None, light=False):
        from evaluation.llm import LLMResponse

        assert "日経平均株価" in prompt
        return LLMResponse(
            text=(
                '{"tone": "expand", "tone_label": "拡大局面", '
                '"paragraphs": [{"heading": "株式市場", "body": "日経平均は堅調。"}], '
                '"verdict": "拡大が続く。"}'
            ),
            model="gemini-flash-latest",
            provider="gemini",
        )

    monkeypatch.setattr("evaluation.llm.generate_json", fake_generate_json)

    result = generate_narrative("japan")
    assert result.tone == "expand"
    assert result.tone_label == "拡大局面"
    assert result.paragraphs[0].heading == "株式市場"
    assert result.verdict == "拡大が続く。"


def test_generate_narrative_wraps_llm_error(monkeypatch):
    market = {"nikkei225": _series("nikkei225", "日経平均株価", "円", [("2026-08-01", 66000.0)])}
    monkeypatch.setattr(narrative, "load_bundle", lambda filename: market if filename == "japan_market.json" else {})

    def boom(*a, **k):
        from evaluation.llm import LLMError

        raise LLMError("down")

    monkeypatch.setattr("evaluation.llm.generate_json", boom)
    with pytest.raises(NarrativeError):
        generate_narrative("japan")


def test_generate_narrative_invalid_tone_falls_back_to_neutral(monkeypatch):
    market = {"nikkei225": _series("nikkei225", "日経平均株価", "円", [("2026-08-01", 66000.0)])}
    monkeypatch.setattr(narrative, "load_bundle", lambda filename: market if filename == "japan_market.json" else {})

    def fake_generate_json(*a, **k):
        from evaluation.llm import LLMResponse

        return LLMResponse(text='{"tone": "bogus", "paragraphs": [], "verdict": "v"}', model="m", provider="gemini")

    monkeypatch.setattr("evaluation.llm.generate_json", fake_generate_json)
    result = generate_narrative("japan")
    assert result.tone == "neutral"
    assert result.tone_label  # フォールバックラベルが入る


def test_save_and_load_narratives_roundtrip(tmp_path):
    jp = MacroNarrative(
        region="japan", tone="expand", tone_label="拡大局面",
        paragraphs=[NarrativeParagraph(heading="h", body="b")], verdict="v", model="m",
    )
    us = MacroNarrative(region="us", tone="neutral", tone_label="巡航", paragraphs=[], verdict="v2", model="m")

    path = save_narratives(jp, us, directory=tmp_path)
    assert path.exists()

    loaded = load_narratives(directory=tmp_path)
    assert loaded["japan"].tone_label == "拡大局面"
    assert loaded["japan"].paragraphs[0].body == "b"
    assert loaded["us"].verdict == "v2"


def test_load_narratives_returns_none_when_missing(tmp_path):
    assert load_narratives(directory=tmp_path) is None
