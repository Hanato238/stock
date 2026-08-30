"""評価プロンプトの組み立て。

構成（前置き順）:
    1. マクロ経済環境ブロック（macro.context.build_macro_context の出力）← B 主軸
    2. 企業プロファイル（会社名・証券コード・事業の内容の要約）
    3. 審査辞典の関連知見（2 段階 RAG で引いたチャンク。出典コード付き）
    4. 有報本文の要点（事業等のリスク・MD&A の抜粋）
    5. 出力指示（JSON スキーマ）
"""

from __future__ import annotations

from edinet.industry import CompanyProfile

from .retrieval import RetrievalResult
from .schema import OUTPUT_SCHEMA_HINT

SYSTEM_INSTRUCTION = (
    "あなたは日本企業の与信・投資適格性を評価するアナリストです。"
    "提示されたマクロ経済環境・審査辞典の業種知見・有価証券報告書の記述のみを根拠に、"
    "財務・事業・経営の 3 軸でリスクを評点し、要注意項目と投資適格性コメントをまとめます。"
    "推測は最小限にとどめ、根拠が乏しい点は「情報不足」と明記します。"
    "審査辞典の数値基準に触れる場合は該当項目コードを示します。"
)

# 各セクションの本文をプロンプトに載せる際の上限。
_MAX_BUSINESS_CHARS = 2500
_MAX_RISK_CHARS = 6000
_MAX_MDA_CHARS = 6000
_MAX_EVIDENCE_CHARS = 1100


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…（以下略）"


def _profile_block(profile: CompanyProfile) -> str:
    lines = [
        "## 企業プロファイル",
        "",
        f"- 会社名: {profile.filer_name or '不明'}",
        f"- 証券コード: {profile.sec_code or '不明'}",
        f"- EDINETコード: {profile.edinet_code or '不明'}",
        "",
        "### 事業の内容（有報【事業の内容】より）",
        _truncate(profile.business_content, _MAX_BUSINESS_CHARS) or "（記載なし）",
    ]
    if profile.related_companies:
        lines += [
            "",
            "### 関係会社の状況（抜粋）",
            _truncate(profile.related_companies, 1200),
        ]
    return "\n".join(lines)


def _evidence_block(retrieval: RetrievalResult) -> str:
    if not retrieval.evidence:
        return "## 審査辞典の関連知見\n\n（該当チャンクを取得できませんでした）"

    lines = [
        "## 審査辞典の関連知見",
        "",
        f"業種の当たり: {retrieval.industry_label}",
        (
            f"メタデータフィルタ: {retrieval.where}"
            if retrieval.where
            else "メタデータフィルタ: なし（コレクション全体を検索）"
        ),
        "",
    ]
    for i, ev in enumerate(retrieval.evidence, 1):
        lines += [
            f"### [{i}] {ev.citation}  (距離 {ev.distance:.3f})",
            _truncate(ev.text, _MAX_EVIDENCE_CHARS),
            "",
        ]
    return "\n".join(lines).rstrip()


def _ir_excerpt_block(retrieval: RetrievalResult) -> str:
    lines = ["## 有価証券報告書の要点"]
    lines += [
        "",
        "### 事業等のリスク",
        _truncate(retrieval.risk_section, _MAX_RISK_CHARS) or "（本文を抽出できませんでした）",
        "",
        "### 経営者による分析（MD&A）",
        _truncate(retrieval.mda_section, _MAX_MDA_CHARS) or "（本文を抽出できませんでした）",
    ]
    return "\n".join(lines)


def build_prompt(
    profile: CompanyProfile,
    macro_block: str,
    retrieval: RetrievalResult,
    *,
    fiscal_period: str,
) -> tuple[str, str]:
    """(system_instruction, user_prompt) を返す。"""
    user = "\n\n".join(
        [
            f"# 評価対象: {profile.filer_name or '対象企業'}（決算期 {fiscal_period}）",
            macro_block.strip(),
            _profile_block(profile),
            _evidence_block(retrieval),
            _ir_excerpt_block(retrieval),
            "## 出力指示",
            OUTPUT_SCHEMA_HINT,
        ]
    )
    return SYSTEM_INSTRUCTION, user
