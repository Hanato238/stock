"""評価プロンプトの組み立て。"""

from evaluation.prompt import SYSTEM_INSTRUCTION, build_prompt
from evaluation.schema import OUTPUT_SCHEMA_HINT


def test_build_prompt_contains_all_sections(profile, retrieval):
    macro = "## マクロ経済環境\n\n- USD/JPY: 149.0円（前年比 +5.0%）"
    system, user = build_prompt(profile, macro, retrieval, fiscal_period="2025-03")

    assert system == SYSTEM_INSTRUCTION
    assert "評価対象: テスト乳業株式会社（決算期 2025-03）" in user
    assert "## マクロ経済環境" in user
    assert "USD/JPY: 149.0円" in user
    assert "## 企業プロファイル" in user
    assert "証券コード: 2264" in user
    assert "## 審査辞典の関連知見" in user
    assert "1042 牛乳・乳製品製造業（食料品関連）" in user
    assert "## 有価証券報告書の要点" in user
    assert "生乳価格の高騰" in user
    assert OUTPUT_SCHEMA_HINT in user


def test_build_prompt_marks_missing_filter(profile, retrieval):
    _, user = build_prompt(profile, "macro", retrieval, fiscal_period="2025-03")
    assert "メタデータフィルタ: なし" in user


def test_build_prompt_truncates_long_risk_section(profile, retrieval):
    retrieval.risk_section = "あ" * 20000
    _, user = build_prompt(profile, "m", retrieval, fiscal_period="2025-03")
    assert "（以下略）" in user
    assert user.count("あ") <= 6001
