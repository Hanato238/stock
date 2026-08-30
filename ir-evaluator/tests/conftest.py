"""評価エンジンのユニットテスト用フィクスチャ（外部 API を叩かない範囲）。"""

import pytest

from edinet.industry import CompanyProfile
from evaluation.engine import EvaluationBundle
from evaluation.retrieval import Evidence, RetrievalResult
from evaluation.schema import EvaluationResult


@pytest.fixture
def profile() -> CompanyProfile:
    return CompanyProfile(
        edinet_code="E00001",
        sec_code="2264",
        filer_name="テスト乳業株式会社",
        business_content="乳製品の製造販売を行っている。\nアイスクリーム及び飲料も扱う。",
        related_companies="子会社3社が製造を担う。",
    )


@pytest.fixture
def retrieval() -> RetrievalResult:
    return RetrievalResult(
        evidence=[
            Evidence(
                code="1042",
                name="牛乳・乳製品製造業",
                section_name="食料品関連",
                volume=1,
                text="自己資本比率の業種平均は約35%。借入金対月商倍率は3か月前後が目安。",
                distance=0.41,
                matched_query="乳製品の財務指標",
            ),
            Evidence(
                code="1042",
                name="牛乳・乳製品製造業",
                section_name="食料品関連",
                volume=1,
                text="原材料（生乳）価格の変動が収益を左右する。",
                distance=0.52,
                matched_query="乳製品のリスク要因",
            ),
        ],
        queries=["乳製品の財務指標", "乳製品のリスク要因"],
        where=None,
        industry_label="乳製品の製造販売を行っている。",
        risk_section="生乳価格の高騰により原価率が上昇するリスクがある。",
        mda_section="当期は売上高が前年比5%増加した。",
    )


@pytest.fixture
def result() -> EvaluationResult:
    return EvaluationResult.from_llm_json(
        {
            "overall_grade": "条件付き適格",
            "summary": "財務は安定するが原材料コストに注意。",
            "risk_scores": [
                {"axis": "財務", "score": 2, "rationale": "自己資本比率は業種平均超。"},
                {"axis": "事業", "score": 4, "rationale": "生乳価格変動に脆弱。"},
                {"axis": "経営", "score": 3, "rationale": "情報不足。"},
            ],
            "watch_items": [
                {"title": "原材料価格", "detail": "生乳の調達コスト上昇。", "severity": "高"},
                {"title": "為替", "detail": "輸入飼料経由の間接影響。", "severity": "低"},
            ],
            "investment_comment": "中期的には価格転嫁力の確認が必要。",
        },
        company="テスト乳業株式会社",
        fiscal_period="2025-03",
        model="gemini:gemini-2.5-pro",
    )


@pytest.fixture
def bundle(result, profile, retrieval) -> EvaluationBundle:
    return EvaluationBundle(
        result=result,
        profile=profile,
        retrieval=retrieval,
        macro_block="## マクロ経済環境\n\n### 決算期環境（2025-03 期末時点）\n- USD/JPY: 149.0円",
        industry_terms=["牛乳・乳製品製造業", "アイスクリーム製造業"],
        queries=retrieval.queries,
    )
