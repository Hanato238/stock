"""スキャンPDF（テキスト層なし）→ Gemini Vision によるOCR/Markdown化"""

import os
import threading
import time
from pathlib import Path

import pypdfium2 as pdfium
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# pypdfium2はスレッドセーフではなく、複数スレッドから同時にレンダリングするとクラッシュする。
# レンダリング自体は高速なCPU処理なのでロックで直列化し、並列性はネットワーク待ちのAPI呼び出し側で確保する。
_pdfium_lock = threading.Lock()

OCR_MODEL = os.environ.get("OCR_MODEL", "gemini-2.5-flash")
RENDER_SCALE = 2.0  # 約144dpi相当
MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 24576  # 表崩れ等でのOCR暴走時のコスト上限（thinking無効化済みなので実質は書き起こし本文の上限）
MAX_LINE_LEN = 500  # 通常のOCR行としてはあり得ない長さ（暴走時の1行無限出力対策）
MAX_LINE_REPEAT = 5  # 同一行がこの回数以上連続したら暴走とみなし打ち切る
MIN_ACCEPTABLE_LEN = 1000  # 打ち切り後にこれ未満しか残らなければ生成をやり直す

OCR_PROMPT = (
    "これは業種別審査辞典の1項目分のスキャンページ画像です。"
    "全ページの内容を、見出し・表組みの構造を保ったMarkdown形式で書き起こしてください。"
    "ページ番号やヘッダー/フッターの繰り返し表記は省略し、本文のみを出力してください。"
    "「以下は書き起こしです」のような前置きや説明文、コードフェンス（```）は一切付けず、"
    "書き起こし結果のMarkdown本文だけを出力してください。"
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _client


def render_pages(pdf_path: Path, scale: float = RENDER_SCALE) -> list:
    """PDFの各ページをPIL画像としてレンダリングする（pdfium呼び出しはロックで直列化）。"""
    with _pdfium_lock:
        pdf = pdfium.PdfDocument(str(pdf_path))
        images = [page.render(scale=scale).to_pil() for page in pdf]
        pdf.close()
    return images


def _clean_ocr_text(text: str) -> tuple[str, bool]:
    """表崩れ等でOCRが暴走した場合の痕跡（極端に長い行・同一行の連続繰り返し）を取り除く。
    戻り値は (クリーンなテキスト, 打ち切りが発生したか)。
    """
    lines = [line[:MAX_LINE_LEN] for line in text.split("\n")]

    cleaned = []
    prev_line = object()
    repeat_count = 0
    truncated = False
    for line in lines:
        if line == prev_line:
            repeat_count += 1
            if repeat_count >= MAX_LINE_REPEAT:
                del cleaned[-(repeat_count - 1):]
                truncated = True
                break
        else:
            prev_line = line
            repeat_count = 1
        cleaned.append(line)

    return "\n".join(cleaned).rstrip(), truncated


def ocr_pdf(pdf_path: Path) -> str:
    """PDFの全ページをGemini Visionで一括してMarkdownに書き起こす。
    暴走検知で内容がほとんど残らなかった場合は取り直す。
    """
    images = render_pages(pdf_path)
    client = _get_client()
    config = types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    best = ""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=OCR_MODEL, contents=[OCR_PROMPT, *images], config=config
            )
            cleaned, truncated = _clean_ocr_text(response.text or "")
            if len(cleaned) > len(best):
                best = cleaned
            if truncated and len(cleaned) < MIN_ACCEPTABLE_LEN and attempt < MAX_RETRIES - 1:
                continue
            return cleaned
        except Exception:
            if attempt == MAX_RETRIES - 1:
                if best:
                    return best
                raise
            time.sleep(2**attempt)
    return best
