"""2 段階 RAG のうち、外部 API に触れない純ロジック部分。"""

from evaluation import retrieval as R


def test_industry_label_uses_business_head(profile):
    assert R._industry_label(profile) == "乳製品の製造販売を行っている"


def test_industry_label_strips_group_boilerplate():
    from edinet.industry import CompanyProfile

    p = CompanyProfile(
        edinet_code=None,
        sec_code=None,
        filer_name="森永乳業株式会社",
        business_content=(
            "当社グループは、当社、子会社52社及び関連会社11社により構成されており、"
            "乳製品、アイスクリーム、飲料、栄養食品の製造・販売を主な事業としている。"
        ),
        related_companies="",
    )
    label = R._industry_label(p)
    assert label.startswith("乳製品")
    assert "子会社" not in label


def test_industry_label_empty_when_only_boilerplate():
    from edinet.industry import CompanyProfile

    # 業種語が取れない（会社名の羅列だけ）→ 空を返し、呼び出し側で事業の内容をクエリに使う
    p = CompanyProfile(
        edinet_code=None,
        sec_code=None,
        filer_name="株式会社フォールバック",
        business_content="当社は、株式会社フォールバックおよび子会社により構成されている",
        related_companies="",
    )
    assert R._industry_label(p) == ""


def test_retrieve_falls_back_to_business_query_without_terms(profile):
    # terms 無し・ラベルも取れない場合でも _build_queries が事業の内容で引く
    queries = R._build_queries([], profile, "", "")
    assert any("乳製品" in q for q in queries)


def test_build_queries_uses_terms_and_sections(profile):
    queries = R._build_queries(
        ["乳製品製造業", "アイスクリーム製造業"],
        profile,
        risk_section="生乳価格の高騰リスク。" * 5,
        mda_section="売上増加。",
    )
    assert "乳製品製造業" in queries
    assert any("アイスクリーム製造業の経営指標" in q for q in queries)
    assert any("与信上の留意点" in q for q in queries)
    assert any("生乳価格" in q for q in queries)
    assert any("売上増加" in q for q in queries)
    assert len(queries) == len(set(queries))  # 重複なし


def test_build_queries_falls_back_without_terms(profile):
    queries = R._build_queries([], profile, risk_section="", mda_section="")
    assert len(queries) >= 2
    assert all(q.strip() for q in queries)
    # ターム無しなら事業の内容を保険で入れる
    assert any("乳製品" in q for q in queries)


def test_resolve_filter_returns_none_when_mapping_empty(profile):
    # industry_map.json の entries は現状空 → None
    assert R.resolve_filter(profile) is None
