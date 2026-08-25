"""業種別審査辞典（項目単位PDF）のディレクトリをスキャンし、項目一覧を構築する"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

# {repo_root}/業種別審査辞典_第5版_業種別/
DEFAULT_DICT_DIR = Path(__file__).resolve().parents[2] / "業種別審査辞典_第5版_業種別"

_VOLUME_DIR_RE = re.compile(r"^第(\d+)巻_(.+)$")
_SECTION_DIR_RE = re.compile(r"^(\d+-\d+)_(.+)$")
_ITEM_FILE_RE = re.compile(r"^(\d+)_(.+)\.pdf$")


@dataclass(frozen=True)
class DictItem:
    code: str  # 項目コード（4桁、辞典内で一意）
    name: str  # 項目名（例: 経営コンサルタント）
    volume: int  # 巻番号
    field: str  # 分野名（巻のサブタイトル）
    section_code: str  # 章コード（例: 7-1）
    section_name: str  # 章名
    pdf_path: Path


def scan_dictionary(dict_dir: Path | None = None) -> list[DictItem]:
    """業種別フォルダを再帰的にスキャンし、DictItemのリストを返す（コード昇順）。"""
    dict_dir = dict_dir or Path(os.environ.get("DICTIONARY_DIR", DEFAULT_DICT_DIR))
    if not dict_dir.is_dir():
        raise FileNotFoundError(f"辞典ディレクトリが見つかりません: {dict_dir}")

    items = []
    for volume_dir in sorted(dict_dir.iterdir()):
        volume_match = _VOLUME_DIR_RE.match(volume_dir.name)
        if not volume_dir.is_dir() or not volume_match:
            continue
        volume, field = int(volume_match.group(1)), volume_match.group(2)

        for section_dir in sorted(volume_dir.iterdir()):
            section_match = _SECTION_DIR_RE.match(section_dir.name)
            if not section_dir.is_dir() or not section_match:
                continue
            section_code, section_name = section_match.group(1), section_match.group(2)

            for pdf_path in sorted(section_dir.iterdir()):
                item_match = _ITEM_FILE_RE.match(pdf_path.name)
                if not item_match:
                    continue
                code, name = item_match.group(1), item_match.group(2)
                items.append(
                    DictItem(
                        code=code,
                        name=name,
                        volume=volume,
                        field=field,
                        section_code=section_code,
                        section_name=section_name,
                        pdf_path=pdf_path,
                    )
                )

    return sorted(items, key=lambda i: i.code)
