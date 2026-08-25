"""項目テキストをチャンク分割する（段落単位でパッキング、長文はオーバーラップ付きで分割）"""

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """テキストを空行区切りの段落にまとめ、chunk_size文字を目安にパッキングする。
    単独の段落がchunk_sizeを超える場合はoverlap付きでハード分割する。
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    current = ""
    for para in paragraphs:
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(para, chunk_size, overlap))
            continue

        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            current = para
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    step = chunk_size - overlap
    return [text[i : i + chunk_size] for i in range(0, len(text), step)]
