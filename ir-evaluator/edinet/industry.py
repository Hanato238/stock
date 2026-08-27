"""有報から業種特定用の材料を抽出する。

EDINET 様式の有報は【…】の見出しが標準化されているため、見出し区間で
セクション本文を切り出せる。ここで得た本文を審査辞典（巻/章）と突合して
業種を確定する（マッピングは rag 側で行う）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .extract import ExtractedDocument, extract_document

# 第1【企業の概況】配下の兄弟見出し。区間の終端検出に用いる。
_OVERVIEW_SIBLINGS = [
    "主要な経営指標等の推移",
    "沿革",
    "事業の内容",
    "関係会社の状況",
    "従業員の状況",
    "事業の状況",
]


@dataclass
class CompanyProfile:
    """業種特定に用いる企業プロファイル。"""

    edinet_code: str | None
    sec_code: str | None
    filer_name: str | None
    business_content: str  # 【事業の内容】本文
    related_companies: str  # 【関係会社の状況】本文（業種特定の補助材料）

    @property
    def industry_seed_text(self) -> str:
        """辞典突合に投入するための業種シードテキスト。"""
        parts = [t for t in (self.business_content, self.related_companies) if t]
        return "\n\n".join(parts)


def extract_section(doc: ExtractedDocument, start_marker: str, end_markers: list[str]) -> str:
    """start_marker の見出しから、end_markers のいずれかが現れる直前までを抽出する。

    見出しは【start_marker】の形で全文中から検索する。終端が見つからない場合は
    開始位置から最大 20,000 字を上限に切り出す（暴走防止）。
    """
    full = doc.full_text
    start_pat = re.compile(r"【\s*" + re.escape(start_marker) + r"\s*】")
    m = start_pat.search(full)
    if not m:
        return ""

    body_start = m.end()
    end_pos = len(full)
    for marker in end_markers:
        em = re.search(r"【\s*" + re.escape(marker) + r"\s*】", full[body_start:])
        if em:
            end_pos = min(end_pos, body_start + em.start())

    return full[body_start:end_pos].strip()[:20000]


def build_profile(doc: ExtractedDocument, meta: dict | None = None) -> CompanyProfile:
    """抽出済みドキュメントと EDINET メタデータから企業プロファイルを組み立てる。

    Args:
        doc: extract_document() の結果。
        meta: search_company / fetch が返す EDINET 書類メタデータ（任意）。
              secCode / edinetCode / filerName を利用する。
    """
    meta = meta or {}
    business = extract_section(
        doc,
        "事業の内容",
        [m for m in _OVERVIEW_SIBLINGS if m != "事業の内容"],
    )
    related = extract_section(
        doc,
        "関係会社の状況",
        [m for m in _OVERVIEW_SIBLINGS if m not in ("事業の内容", "関係会社の状況")],
    )

    sec_code = meta.get("secCode")
    if sec_code and len(sec_code) == 5 and sec_code.endswith("0"):
        # EDINET の secCode は5桁（末尾0付き）。4桁の証券コードへ正規化。
        sec_code = sec_code[:4]

    return CompanyProfile(
        edinet_code=meta.get("edinetCode"),
        sec_code=sec_code,
        filer_name=meta.get("filerName"),
        business_content=business,
        related_companies=related,
    )


def profile_from_pdf(pdf_path: str | Path, meta: dict | None = None) -> CompanyProfile:
    """PDF パスから直接プロファイルを作るショートカット。"""
    return build_profile(extract_document(pdf_path), meta=meta)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="有報から業種特定材料を抽出する")
    parser.add_argument("pdf_path", help="対象有報 PDF のパス")
    args = parser.parse_args()

    profile = profile_from_pdf(args.pdf_path)
    print(f"提出者: {profile.filer_name}  証券コード: {profile.sec_code}  EDINET: {profile.edinet_code}")
    print(f"【事業の内容】{len(profile.business_content):,}字 / 【関係会社の状況】{len(profile.related_companies):,}字")
    print("--- 事業の内容（先頭400字）---")
    print(profile.business_content[:400])
