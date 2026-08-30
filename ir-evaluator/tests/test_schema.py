"""EvaluationResult の LLM 出力パース・正規化。"""

import pytest

from evaluation.schema import (
    EvaluationResult,
    RiskScore,
    _loads_lenient,
    _normalize_grade,
)

_GOOD = {
    "overall_grade": "条件付き適格",
    "summary": "総括テキスト。",
    "risk_scores": [
        {"axis": "財務", "score": 2, "rationale": "自己資本比率が業種平均以上。"},
        {"axis": "事業", "score": 4, "rationale": "原材料価格の変動に脆弱。"},
        {"axis": "経営", "score": 3, "rationale": "後継者計画が不透明。"},
    ],
    "watch_items": [
        {"title": "為替感応度", "detail": "円安局面で調達コスト増。", "severity": "高"},
    ],
    "investment_comment": "コメント本文。",
}


def test_from_llm_json_dict_roundtrip():
    r = EvaluationResult.from_llm_json(
        _GOOD, company="テスト社", fiscal_period="2025-03", model="gemini:x"
    )
    assert r.overall_grade == "条件付き適格"
    assert [s.axis for s in r.risk_scores] == ["財務", "事業", "経営"]
    assert r.risk_scores[1].score == 4
    assert r.watch_items[0].severity == "高"
    assert r.model == "gemini:x"


def test_from_llm_json_strips_code_fence_and_prose():
    raw = 'これは結果です:\n```json\n' + '{"overall_grade": "適格", "summary": "s"}' + "\n```\nおわり"
    r = EvaluationResult.from_llm_json(
        raw, company="C", fiscal_period="2025-03", model="m"
    )
    assert r.overall_grade == "適格"
    # 欠損軸は score=3 で補完される。
    assert all(s.score == 3 for s in r.risk_scores)
    assert len(r.risk_scores) == 3


def test_score_out_of_range_is_clamped():
    assert RiskScore("財務", 9, "x").clamped().score == 5
    assert RiskScore("財務", 0, "x").clamped().score == 1


def test_missing_axis_defaults_and_bad_score_type():
    data = {"risk_scores": [{"axis": "財務", "score": "high"}]}
    r = EvaluationResult.from_llm_json(data, company="C", fiscal_period="p", model="m")
    fin = next(s for s in r.risk_scores if s.axis == "財務")
    assert fin.score == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("投資適格と判断", "適格"),
        ("条件付", "条件付き適格"),
        ("要精査", "要精査"),
        ("投資不適格", "非適格"),
        ("よくわからない表現", "よくわからない表現"),
        ("", "要精査"),
    ],
)
def test_normalize_grade(text, expected):
    assert _normalize_grade(text) == expected


def test_loads_lenient_nested_braces_in_strings():
    raw = 'prefix {"summary": "a } b { c", "overall_grade": "適格"} suffix'
    assert _loads_lenient(raw)["overall_grade"] == "適格"


def test_loads_lenient_raises_without_json():
    with pytest.raises(ValueError):
        _loads_lenient("no json here")
