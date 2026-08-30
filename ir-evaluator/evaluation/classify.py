"""企業プロファイル → 審査辞典を引くための業種タームを軽量 LLM で抽出する。

審査辞典のチャンクは大半が業種別の財務指標テーブルで、散文クエリとは相性が悪い。
「乳製品製造業」「アイスクリーム製造業」のような **業種名の名詞句** で引くと精度が出る。
有報【事業の内容】の冒頭はグループ構成の定型文で業種語が埋もれるため、正規表現では
安定せず、LLM に業種名だけを列挙させる。

既定モデルは環境変数 ``INDUSTRY_MODEL``（未設定なら安価な ``gemini-flash-latest``）。
"""

from __future__ import annotations

import os

from edinet.industry import CompanyProfile

from .llm import LLMError, generate_json
from .schema import _loads_lenient

DEFAULT_INDUSTRY_MODEL = os.environ.get("INDUSTRY_MODEL", "gemini-flash-latest")

_MAX_SEED_CHARS = 2000
_MAX_TERMS = 5

_PROMPT = """\
次の企業の「事業の内容」記述から、この会社の主要な事業を表す**業種名の名詞句**を
重要な順に最大{max_terms}個挙げてください。業種別審査辞典を検索するためのキーワードです。

- 「◯◯製造業」「◯◯卸売業」「◯◯小売業」「◯◯サービス業」のような一般的な業種名にする
- 自社ブランド名・製品名・グループ構成の説明は含めない
- 持株会社なら主要子会社の事業を業種名にする

出力は JSON のみ:
{{"terms": ["業種名1", "業種名2", ...], "summary": "この会社の事業の1文要約"}}

--- 事業の内容 ---
{seed}
"""


def classify_industry(
    profile: CompanyProfile,
    *,
    model: str | None = None,
) -> list[str]:
    """業種ターム（審査辞典検索用の名詞句）のリストを返す。失敗時は空リスト。"""
    seed = profile.industry_seed_text.strip()[:_MAX_SEED_CHARS]
    if not seed:
        return []

    prompt = _PROMPT.format(seed=seed, max_terms=_MAX_TERMS)
    try:
        resp = generate_json(
            prompt,
            model=model or DEFAULT_INDUSTRY_MODEL,
            max_output_tokens=1024,
            light=True,
        )
        data = _loads_lenient(resp.text)
    except (LLMError, ValueError, KeyError, TypeError):
        # 業種判定は補助シグナルなので、失敗しても評価本体は続行させる。
        return []

    terms = data.get("terms", [])
    if not isinstance(terms, list):
        return []
    cleaned = []
    for t in terms:
        s = str(t).strip()
        if s and s not in cleaned:
            cleaned.append(s)
    return cleaned[:_MAX_TERMS]
