"""業種タームからのセクター指標選択（LLM呼び出しはモック）。"""

import pytest

from macro.indicators import (
    IndicatorCatalog,
    IndicatorSelectionError,
    load_sector_series,
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


def test_catalog_real_file_food_beverage_has_coverage():
    # 回帰テスト: 「製造業（食品・飲料）」は以前どの指標にも紐付いておらず、
    # 森永乳業（乳製品製造業）評価時に無関係な「鉱工業指数（鉄鋼業）」が選ばれる
    # 原因になっていた（TODO.md Phase4.6）。household_survey に追加して解消。
    catalog = IndicatorCatalog.load()
    covered = {ind for i in catalog.indicators for ind in i.get("relevant_industries", [])}
    assert "製造業（食品・飲料）" in covered


def test_catalog_real_file_steel_index_not_tagged_general_manufacturing():
    # 回帰テスト: steel_industry_index（鉱工業指数・鉄鋼業）は鉄鋼業specificな指標であり、
    # 汎用の「製造業（一般）」タグが付くと食品等の無関係な業種にも波及してしまっていた。
    catalog = IndicatorCatalog.load()
    steel = next(i for i in catalog.indicators if i["key"] == "steel_industry_index")
    assert "製造業（一般）" not in steel["relevant_industries"]


def test_load_sector_series_merges_both_bundles(monkeypatch):
    from macro import indicators as macro_indicators

    def fake_load_bundle(filename):
        if filename == "sectors.json":
            return {"economy_watchers_di": "series-a"}
        if filename == "sectors_extra.json":
            return {"tokyo_cpi": "series-b"}
        raise AssertionError(f"unexpected filename: {filename}")

    monkeypatch.setattr(macro_indicators, "load_bundle", fake_load_bundle)
    merged = load_sector_series()
    assert merged == {"economy_watchers_di": "series-a", "tokyo_cpi": "series-b"}


def test_load_sector_series_tolerates_missing_files(monkeypatch):
    from macro import indicators as macro_indicators

    def fake_load_bundle(filename):
        raise FileNotFoundError(filename)

    monkeypatch.setattr(macro_indicators, "load_bundle", fake_load_bundle)
    assert load_sector_series() == {}
