"""マクロ指標の JSON キャッシュ読み書き（data/macro/*.json）。

時系列で保持し、決算期環境と足元環境の両方を後段（context.py）が引けるようにする。
確報改定は割り切り、取得時点のスナップショットをそのままキャッシュする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .fred import Observation

# リポジトリ直下の data/macro/ を既定の保存先にする。
_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "macro"


@dataclass
class SeriesData:
    """1 指標の時系列＋メタ情報。"""

    key: str  # 内部指標キー（例: usd_jpy）
    label: str  # 日本語ラベル（例: USD/JPY）
    source: str  # 取得元（例: FRED:DEXJPUS）
    frequency: str  # d/m/q など
    unit: str  # 単位（例: 円/ドル）
    observations: list[Observation]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "frequency": self.frequency,
            "unit": self.unit,
            "observations": [{"date": o.date, "value": o.value} for o in self.observations],
        }

    @classmethod
    def from_dict(cls, d: dict) -> SeriesData:
        return cls(
            key=d["key"],
            label=d["label"],
            source=d["source"],
            frequency=d["frequency"],
            unit=d["unit"],
            observations=[Observation(date=o["date"], value=o["value"]) for o in d["observations"]],
        )

    def latest(self) -> Observation | None:
        """欠損でない最新観測点（足元環境用）。"""
        for obs in reversed(self.observations):
            if obs.value is not None:
                return obs
        return None


def save_bundle(
    series: list[SeriesData],
    filename: str,
    directory: Path = _DEFAULT_DIR,
) -> Path:
    """複数指標を 1 ファイルにまとめて保存する（例: overall.json）。

    取得日時をファイル内 `_meta.fetched_at` に記録する。
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "_meta": {
            "fetched_at": datetime.now(UTC).isoformat(),
            "series_keys": [s.key for s in series],
        },
        "series": {s.key: s.to_dict() for s in series},
    }
    out_path = directory / filename
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def load_bundle(filename: str, directory: Path = _DEFAULT_DIR) -> dict[str, SeriesData]:
    """保存済みバンドルを指標キー→SeriesData の辞書で読み込む。"""
    path = directory / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: SeriesData.from_dict(sd) for key, sd in data.get("series", {}).items()}
