"""企業の業種タームから、関連するセクター指標を data/macro/indicator_catalog.json
（grill-meで確定・調査済みの日本の公的統計・業界統計38本のカタログ）の中から選ぶ。

evaluation.classify.classify_industry() と同じパターン: LLM に業種名（自由記述）を
渡し、固定の分類体系（indicator_catalog.json の industry_taxonomy、19区分）へ
写像させる。カタログ側の絞り込みそのものはコード側で行い、LLM には分類の判定
だけを担わせる（数値やURLを LLM に生成させない）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "macro" / "indicator_catalog.json"

DEFAULT_INDICATOR_MODEL = os.environ.get("INDICATOR_MODEL", "gemini-flash-latest")

_MAX_CATEGORIES = 3
_DEFAULT_MAX_INDICATORS = 6

# 全業種共通の指標は毎回混じると個別株ページのミニ指標として冗長になるため、
# 業種特化の指標を優先し、共通指標は不足時の埋め合わせ用に後ろへ回す。
_COMMON_TAG = "全業種共通"


class IndicatorSelectionError(RuntimeError):
    """指標選択に関する失敗（カタログ未検証・LLM 失敗など）。"""


@dataclass
class IndicatorCatalog:
    industry_taxonomy: list[str]
    indicators: list[dict]

    @classmethod
    def load(cls, path: Path = _CATALOG_PATH) -> IndicatorCatalog:
        if not path.exists():
            raise IndicatorSelectionError(
                f"{path} が見つかりません。data/macro/indicator_catalog.json を用意してください。"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            industry_taxonomy=list(data.get("industry_taxonomy", [])),
            indicators=list(data.get("indicators", [])),
        )


_PROMPT = """\
次の企業の業種名（審査辞典検索用に抽出済みの名詞句）を読み、
この企業のマクロ経済上の関連が最も深い業種分類を、以下の固定リストから
重要な順に最大{max_categories}個選んでください。

固定リスト:
{taxonomy}

企業の業種名:
{terms}

- 固定リストに無い分類を作らないこと（リストの文字列をそのまま使う）
- 判断に迷う場合は「全業種共通」ではなく、最も近い具体的な業種を優先すること
- 出力は JSON のみ: {{"categories": ["業種分類1", "業種分類2", ...]}}
"""


def _select_categories(industry_terms: list[str], taxonomy: list[str], *, model: str) -> list[str]:
    if not industry_terms:
        return []

    # 遅延 import: macro パッケージを evaluation パッケージへ静的依存させないため
    # （macro/narrative.py と同じ方針）。
    from evaluation.llm import LLMError, generate_json
    from evaluation.schema import _loads_lenient

    prompt = _PROMPT.format(
        max_categories=_MAX_CATEGORIES,
        taxonomy="\n".join(f"- {t}" for t in taxonomy),
        terms="、".join(industry_terms),
    )
    try:
        resp = generate_json(prompt, model=model, max_output_tokens=512, light=True)
        data = _loads_lenient(resp.text)
    except (LLMError, ValueError, KeyError, TypeError):
        # 業種→指標の対応づけは補助シグナルなので、失敗しても全業種共通指標へフォールバックする。
        return []

    categories = data.get("categories", [])
    if not isinstance(categories, list):
        return []
    valid = set(taxonomy)
    return [c for c in categories if isinstance(c, str) and c in valid][:_MAX_CATEGORIES]


def select_indicators(
    industry_terms: list[str],
    *,
    model: str | None = None,
    max_indicators: int = _DEFAULT_MAX_INDICATORS,
    catalog: IndicatorCatalog | None = None,
) -> list[dict]:
    """業種タームに関連するセクター指標のカタログエントリを返す。

    Args:
        industry_terms: evaluation.classify.classify_industry() が返す業種名リスト。
        model: 業種分類に使う軽量モデル（既定は INDICATOR_MODEL 環境変数）。
        max_indicators: 返す件数の上限。
        catalog: テスト用の差し替え。省略時は data/macro/indicator_catalog.json を読む。

    Returns:
        indicator_catalog.json の indicators[] エントリ（dict）のリスト。
        業種特化の指標を優先し、不足分は「全業種共通」タグの指標で埋める。
        LLM 判定に失敗した場合や industry_terms が空の場合は「全業種共通」のみ返す。
    """
    catalog = catalog or IndicatorCatalog.load()
    use_model = model or DEFAULT_INDICATOR_MODEL

    categories = _select_categories(industry_terms, catalog.industry_taxonomy, model=use_model)
    category_set = set(categories)

    specific: list[dict] = []
    common: list[dict] = []
    for ind in catalog.indicators:
        tags = set(ind.get("relevant_industries", []))
        if tags & category_set:
            specific.append(ind)
        elif _COMMON_TAG in tags:
            common.append(ind)

    # 業種特化の指標は easy（機械取得しやすい）を優先。
    specific.sort(key=lambda i: 0 if i.get("difficulty") == "easy" else 1)
    common.sort(key=lambda i: 0 if i.get("difficulty") == "easy" else 1)

    selected = specific[:max_indicators]
    if len(selected) < max_indicators:
        selected += common[: max_indicators - len(selected)]
    return selected


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="業種タームから関連セクター指標を選択")
    parser.add_argument("terms", nargs="+", help="業種名（複数可、空白区切り）")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max", type=int, default=_DEFAULT_MAX_INDICATORS)
    args = parser.parse_args()

    selected = select_indicators(args.terms, model=args.model, max_indicators=args.max)
    for ind in selected:
        print(f"- {ind['name_ja']}（{ind['key']}, {ind.get('difficulty', '?')}, {ind.get('publisher', '?')}）")


if __name__ == "__main__":
    main()
