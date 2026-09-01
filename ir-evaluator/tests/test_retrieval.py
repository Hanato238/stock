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


def _hit(query: str, code: str, dist: float, chunk_index: int = 0) -> tuple:
    return (
        query,
        [f"text-{code}-{chunk_index}"],
        [{"code": code, "name": code, "section_name": "", "volume": 1, "chunk_index": chunk_index}],
        [dist],
    )


def test_select_evidence_prefers_term_query_at_similar_distance():
    # 散文（MD&A）クエリ由来のほうが生の距離は良いが、ペナルティにより業種タームクエリの
    # ヒットが優先される（辞典は表主体で散文一致はノイズ寄りという設計判断）。
    hits = [
        _hit("乳製品製造業", "A", dist=0.30),
        _hit("売上増加。", "B", dist=0.25),
    ]
    evidence = R._select_evidence(hits, prose_queries={"売上増加。"}, n_evidence=10)
    assert [e.code for e in evidence] == ["A", "B"]


def test_select_evidence_keeps_prose_hit_when_nothing_better():
    # 該当項目が散文クエリでしか見つからなくても、除外はせず証拠として残す。
    hits = [_hit("生乳価格の高騰リスク。", "C", dist=0.35)]
    evidence = R._select_evidence(hits, prose_queries={"生乳価格の高騰リスク。"}, n_evidence=10)
    assert [e.code for e in evidence] == ["C"]


def test_select_evidence_aggregates_true_min_distance_across_queries():
    # 同一チャンクが複数クエリでヒットした場合、由来にかかわらず実際の最小距離を保持する。
    hits = [
        _hit("乳製品製造業", "A", dist=0.40),
        _hit("生乳価格の高騰リスク。", "A", dist=0.20),
    ]
    evidence = R._select_evidence(hits, prose_queries={"生乳価格の高騰リスク。"}, n_evidence=10)
    assert evidence[0].distance == 0.20
    assert evidence[0].matched_query == "生乳価格の高騰リスク。"


def test_select_evidence_respects_max_per_code():
    hits = [
        _hit("q1", "A", dist=0.10, chunk_index=0),
        _hit("q2", "A", dist=0.11, chunk_index=1),
        _hit("q3", "A", dist=0.12, chunk_index=2),
        _hit("q4", "A", dist=0.13, chunk_index=3),
    ]
    evidence = R._select_evidence(hits, prose_queries=set(), n_evidence=10)
    assert len(evidence) == R._MAX_PER_CODE


def test_section_chunks_combines_risk_and_mda_within_limits():
    chunks = R._section_chunks("生乳価格の高騰リスク。" * 5, "売上増加。")
    assert any("生乳価格" in c for c in chunks)
    assert any("売上増加" in c for c in chunks)
