"""IR PDF テキスト抽出・前処理

EDINET の有報 PDF はテキストレイヤーを持つデジタルネイティブ PDF のため、
OCR は不要で pymupdf の get_text() だけで本文を取得できる。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass
class Page:
    """1ページ分の抽出結果。number は 1 始まり。"""

    number: int
    text: str


@dataclass
class ExtractedDocument:
    source_path: Path
    page_count: int
    pages: list[Page]

    @property
    def full_text(self) -> str:
        """全ページを連結した本文。ページ境界は空行で区切る。"""
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


# ページ番号のみ・目次リーダー等のノイズ行を除去するパターン
_PAGE_NUMBER_ONLY = re.compile(r"^\s*[-‐―ー－]?\s*\d{1,4}\s*[-‐―ー－]?\s*$")
_MULTISPACE = re.compile(r"[ \t　]{2,}")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def _clean_text(raw: str) -> str:
    """1ページ分の生テキストを正規化・整形する。

    - NFKC 正規化（全角英数→半角、半角カナ→全角など日本語処理の定番前処理）
    - 各行の連続空白を1つに圧縮
    - ページ番号のみの行を除去
    - 3連以上の空行を2つに圧縮
    """
    text = unicodedata.normalize("NFKC", raw)

    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if _PAGE_NUMBER_ONLY.match(stripped):
            continue
        cleaned_lines.append(_MULTISPACE.sub(" ", stripped))

    result = "\n".join(cleaned_lines)
    result = _MANY_BLANK_LINES.sub("\n\n", result)
    return result.strip()


def extract_document(pdf_path: str | Path, clean: bool = True) -> ExtractedDocument:
    """PDF からページ単位でテキストを抽出する。

    Args:
        pdf_path: 対象 PDF のパス。
        clean: True なら各ページに前処理（正規化・ノイズ除去）を適用する。

    Returns:
        ExtractedDocument（ページ単位の抽出結果と全文アクセサを持つ）。
    """
    path = Path(pdf_path)
    doc = pymupdf.open(path)
    try:
        pages: list[Page] = []
        for i, page in enumerate(doc):
            raw = page.get_text("text")
            text = _clean_text(raw) if clean else raw
            pages.append(Page(number=i + 1, text=text))
        return ExtractedDocument(source_path=path, page_count=doc.page_count, pages=pages)
    finally:
        doc.close()


def extract_text(pdf_path: str | Path, clean: bool = True) -> str:
    """PDF から前処理済みの全文テキストだけを取り出すショートカット。"""
    return extract_document(pdf_path, clean=clean).full_text


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IR PDF からテキストを抽出する")
    parser.add_argument("pdf_path", help="対象 PDF のパス")
    parser.add_argument("-o", "--output", help="抽出テキストの保存先（省略時は標準出力へ要約表示）")
    parser.add_argument("--raw", action="store_true", help="前処理を行わず生テキストを出力する")
    args = parser.parse_args()

    document = extract_document(args.pdf_path, clean=not args.raw)

    if args.output:
        Path(args.output).write_text(document.full_text, encoding="utf-8")
        print(f"保存: {args.output}（{document.page_count}ページ / {document.char_count:,}文字）")
    else:
        print(f"ページ数: {document.page_count}")
        print(f"総文字数: {document.char_count:,}")
        print("--- 先頭500文字 ---")
        print(document.full_text[:500])
