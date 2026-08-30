"""業種ターム抽出（LLM 呼び出しはモック）。"""

from evaluation import classify


def test_classify_industry_parses_terms(monkeypatch, profile):
    def fake_generate_json(prompt, *, model=None, max_output_tokens=None, system=None, light=False):
        assert "事業の内容" in prompt
        from evaluation.llm import LLMResponse

        return LLMResponse(
            text='```json\n{"terms": ["牛乳・乳製品製造業", "アイスクリーム製造業", "牛乳・乳製品製造業"], '
            '"summary": "乳製品と氷菓の製造販売"}\n```',
            model="gemini-flash-latest",
            provider="gemini",
        )

    monkeypatch.setattr(classify, "generate_json", fake_generate_json)
    terms = classify.classify_industry(profile)
    assert terms == ["牛乳・乳製品製造業", "アイスクリーム製造業"]  # 重複除去


def test_classify_industry_returns_empty_on_error(monkeypatch, profile):
    from evaluation.llm import LLMError

    def boom(*a, **k):
        raise LLMError("API down")

    monkeypatch.setattr(classify, "generate_json", boom)
    assert classify.classify_industry(profile) == []


def test_classify_industry_empty_seed(monkeypatch):
    from edinet.industry import CompanyProfile

    p = CompanyProfile(
        edinet_code=None, sec_code=None, filer_name="X",
        business_content="", related_companies="",
    )
    # generate_json は呼ばれないはず
    monkeypatch.setattr(classify, "generate_json", lambda *a, **k: 1 / 0)
    assert classify.classify_industry(p) == []
