"""業種タームからのセクター指標選択（LLM呼び出しはモック）。"""

import pytest

from macro.indicators import (
    IndicatorCatalog,
    IndicatorSelectionError,
    select_indicators,
)


@pytest.fixture
def catalog() -> IndicatorCatalog:
    return IndicatorCatalog(
        industry_taxonomy=["製造業（食品・飲料）", "卸売業", "建設業", "全業種共通"],
        indicators=[
            {
                "key": "commerce_dynamics",
                "name_ja": "商業動態統計",
                "publisher": "経済産業省",
                "frequency": "月次",
                "difficulty": "easy",
                "relevant_industries": ["卸売業", "小売業"],
            },
            {
                "key": "household_survey",
                "name_ja": "家計調査",
                "publisher": "総務省統計局",
                "frequency": "月次",
                "difficulty": "easy",
                "relevant_industries": ["製造業（食品・飲料）", "全業種共通"],
            },
            {
                "key": "tankan",
                "name_ja": "日銀短観",
                "publisher": "日本銀行",
                "frequency": "四半期",
                "difficulty": "easy",
                "relevant_industries": ["全業種共通"],
            },
            {
                "key": "construction_starts",
                "name_ja": "建築着工統計調査",
                "publisher": "国土交通省",
                "frequency": "月次",
                "difficulty": "easy",
                "relevant_industries": ["建設業"],
            },
        ],
    )


def _mock_categories(monkeypatch, categories: list[str]):
    def fake_generate_json(prompt, *, model=None, max_output_tokens=None, system=None, light=False):
        import json

        from evaluation.llm import LLMResponse

        return LLMResponse(text=json.dumps({"categories": categories}), model="m", provider="gemini")

    monkeypatch.setattr("evaluation.llm.generate_json", fake_generate_json)


def test_select_indicators_prioritizes_specific_over_common(monkeypatch, catalog):
    _mock_categories(monkeypatch, ["製造業（食品・飲料）"])
    selected = select_indicators(["乳製品製造業"], catalog=catalog, max_indicators=2)
    keys = [i["key"] for i in selected]
    assert keys[0] == "household_survey"  # 業種特化（製造業(食品・飲料)）が優先
    assert len(selected) == 2


def test_select_indicators_fills_with_common_when_specific_insufficient(monkeypatch, catalog):
    _mock_categories(monkeypatch, ["建設業"])
    selected = select_indicators(["建築工事業"], catalog=catalog, max_indicators=3)
    keys = [i["key"] for i in selected]
    assert "construction_starts" in keys  # 特化1件
    assert "tankan" in keys  # 全業種共通で埋め合わせ
    assert len(selected) <= 3


def test_select_indicators_ignores_categories_not_in_taxonomy(monkeypatch, catalog):
    _mock_categories(monkeypatch, ["でたらめな業種", "卸売業"])
    selected = select_indicators(["何らかの卸売業"], catalog=catalog)
    keys = [i["key"] for i in selected]
    assert "commerce_dynamics" in keys


def test_select_indicators_empty_terms_returns_common_only(monkeypatch, catalog):
    # LLM は呼ばれないはず（空リストならガード節で早期return）
    monkeypatch.setattr(
        "evaluation.llm.generate_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれるべきでない"))
    )
    selected = select_indicators([], catalog=catalog, max_indicators=2)
    keys = [i["key"] for i in selected]
    assert keys == ["household_survey", "tankan"]  # 全業種共通2件（easy優先の登録順）


def test_select_indicators_llm_failure_falls_back_to_common(monkeypatch, catalog):
    def boom(*a, **k):
        from evaluation.llm import LLMError

        raise LLMError("down")

    monkeypatch.setattr("evaluation.llm.generate_json", boom)
    selected = select_indicators(["乳製品製造業"], catalog=catalog, max_indicators=2)
    keys = [i["key"] for i in selected]
    assert keys == ["household_survey", "tankan"]


def test_catalog_load_missing_file_raises(tmp_path):
    with pytest.raises(IndicatorSelectionError):
        IndicatorCatalog.load(tmp_path / "nope.json")


def test_catalog_load_real_file():
    catalog = IndicatorCatalog.load()
    assert len(catalog.industry_taxonomy) == 19
    assert len(catalog.indicators) == 38
    assert all("relevant_industries" in i for i in catalog.indicators)
