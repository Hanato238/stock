"""企業プロファイル → 審査辞典の巻/章（Chromaコレクション）への解決層。

industry_map.json は辞典の巻/章立てをキーに持つマッピング表。中身は辞典JSONの
復帰後に投入する。resolve() は投入済みエントリに対しキーワード一致でスコアリングし、
対応する審査辞典コレクションを返す。エントリが空の間は None を返す。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_MAP_PATH = Path(__file__).with_name("industry_map.json")


@dataclass
class IndustryEntry:
    industry_key: str
    dictionary_volume: int
    dictionary_chapter: str
    chroma_collection: str
    tse33: str | None = None
    match_keywords: tuple[str, ...] = ()


def load_mapping(path: str | Path = _DEFAULT_MAP_PATH) -> list[IndustryEntry]:
    """industry_map.json から有効な entries を読み込む。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries: list[IndustryEntry] = []
    for e in data.get("entries", []):
        entries.append(
            IndustryEntry(
                industry_key=e["industry_key"],
                dictionary_volume=e["dictionary_volume"],
                dictionary_chapter=e["dictionary_chapter"],
                chroma_collection=e["chroma_collection"],
                tse33=e.get("tse33"),
                match_keywords=tuple(e.get("match_keywords", [])),
            )
        )
    return entries


@dataclass
class ResolveResult:
    entry: IndustryEntry
    score: int


def resolve(
    seed_text: str,
    entries: list[IndustryEntry] | None = None,
    tse33: str | None = None,
) -> ResolveResult | None:
    """業種シードテキストに最も適合する辞典エントリを返す。

    スコア = シードテキスト中のキーワード出現回数の合計（+ tse33 一致でボーナス）。
    スコア 0（＝どのエントリにも一致しない）または entries が空なら None。

    Args:
        seed_text: CompanyProfile.industry_seed_text 等の業種特定テキスト。
        entries: マッピングエントリ。省略時は load_mapping() を使う。
        tse33: 東証33業種（任意）。一致でスコアを加点する補助シグナル。
    """
    if entries is None:
        entries = load_mapping()
    if not entries:
        return None

    best: ResolveResult | None = None
    for entry in entries:
        score = sum(seed_text.count(kw) for kw in entry.match_keywords)
        if tse33 and entry.tse33 and entry.tse33 == tse33:
            score += 5
        if score > 0 and (best is None or score > best.score):
            best = ResolveResult(entry=entry, score=score)
    return best
