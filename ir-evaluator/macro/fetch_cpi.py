"""e-Stat から月次コア CPI（生鮮食品を除く総合・全国）を取得する。

統計表 ID・分類コードは e-Stat 検索／メタ情報で特定済み（2026-08-27）:
  統計表 0003427113 「消費者物価指数（2020年基準）」
  cat01=0161「生鮮食品を除く総合」＝日本の「コア CPI」
  area=00000「全国」 / tab=1「指数」
基準改定で ID・コードは変わり得るため、変わったら search_stats_list／get_meta_info で
再特定する。
"""

from __future__ import annotations

from .estat import EstatClient, estat_month_to_iso
from .fred import Observation
from .store import SeriesData

CPI_STATS_DATA_ID = "0003427113"
CPI_CAT01_CORE = "0161"  # 生鮮食品を除く総合（コア CPI）
CPI_AREA_ALL = "00000"  # 全国
CPI_TAB_INDEX = "1"  # 指数（前年比等ではなく水準）


def fetch_cpi(client: EstatClient | None = None, start_year: str = "2015") -> SeriesData:
    """月次コア CPI 指数（2020=100）を SeriesData で返す。"""
    client = client or EstatClient()
    stat = client.get_stats_data(
        CPI_STATS_DATA_ID,
        cd_cat01=CPI_CAT01_CORE,
        cd_area=CPI_AREA_ALL,
        cd_time_from=f"{start_year}000000",
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations: list[Observation] = []
    for v in values:
        if v.get("@tab") != CPI_TAB_INDEX:
            continue
        iso = estat_month_to_iso(v.get("@time"))
        if iso is None:
            continue
        raw = v.get("$")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None  # 欠損記号（"-", "***" 等）
        observations.append(Observation(date=iso, value=value))

    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="cpi_core",
        label="コアCPI（生鮮食品を除く総合、2020年基準）",
        source=f"e-Stat:{CPI_STATS_DATA_ID}/cat01={CPI_CAT01_CORE}",
        frequency="m",
        unit="指数(2020=100)",
        observations=observations,
    )
