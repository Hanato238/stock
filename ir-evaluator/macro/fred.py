"""FRED API クライアント（米セントルイス連銀・経済データ）。

日本の主要マクロ指標を series ID で引く。クリーンな JSON API 一本で長期時系列が
取れるため、B主軸（経済全体の文脈）の 4 指標をここに寄せている。
無料の API キーが必要（環境変数 FRED_API_KEY）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

load_dotenv()

FRED_BASE_URL = "https://api.stlouisfed.org/fred"


@dataclass
class Observation:
    """時系列の 1 観測点。欠損（FRED では "."）は value=None。"""

    date: str  # ISO 日付 (YYYY-MM-DD)
    value: float | None


class FredClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ["FRED_API_KEY"]
        self.session = requests.Session()

    def get_series(
        self,
        series_id: str,
        observation_start: str | None = None,
        observation_end: str | None = None,
        frequency: str | None = None,
    ) -> list[Observation]:
        """指定 series の観測値を取得する。

        Args:
            series_id: FRED の系列 ID（例: DEXJPUS）。
            observation_start: 取得開始日 YYYY-MM-DD（省略で全期間）。
            observation_end: 取得終了日 YYYY-MM-DD。
            frequency: 集計頻度。より高頻度の系列を落とし込む
                （d=日, w=週, m=月, q=四半期, a=年）。FRED 側で平均集計される。
        """
        params: dict[str, str] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if observation_start:
            params["observation_start"] = observation_start
        if observation_end:
            params["observation_end"] = observation_end
        if frequency:
            params["frequency"] = frequency

        resp = self.session.get(f"{FRED_BASE_URL}/series/observations", params=params)
        resp.raise_for_status()
        data = resp.json()

        observations: list[Observation] = []
        for obs in data.get("observations", []):
            raw = obs.get("value")
            value = None if raw in (None, ".", "") else float(raw)
            observations.append(Observation(date=obs["date"], value=value))
        return observations
