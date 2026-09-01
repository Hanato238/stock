"""e-Stat から景気動向指数（CI/DI）、FRED から日経平均・JP10年金利を取得する。

マクロ経済モニターページ（Phase 4.6）の日本セクション用。overall.json の
4指標（B主軸）とは別バンドル（japan_market.json）に保存する。

統計表 ID・分類コードは e-Stat 検索で特定済み（2026-08-30）:
  統計表 0003446461「景気動向指数」長期系列（内閣府）
  tab: 100=CI指数 / 120=DI指数
  cat01: 100=先行指数 / 110=一致指数 / 120=遅行指数
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .estat import EstatClient, estat_month_to_iso
from .fred import FredClient, Observation
from .store import SeriesData

CI_DI_STATS_DATA_ID = "0003446461"
_TAB_CI = "100"
_TAB_DI = "120"
_CAT01_LEADING = "100"
_CAT01_COINCIDENT = "110"
_CAT01_LAGGING = "120"

_CI_DI_SPECS: tuple[tuple[str, str, str, str], ...] = (
    # (key, label, tab, cat01)
    ("ci_leading", "CI先行指数", _TAB_CI, _CAT01_LEADING),
    ("ci_coincident", "CI一致指数", _TAB_CI, _CAT01_COINCIDENT),
    ("ci_lagging", "CI遅行指数", _TAB_CI, _CAT01_LAGGING),
    ("di_leading", "DI先行指数", _TAB_DI, _CAT01_LEADING),
    ("di_coincident", "DI一致指数", _TAB_DI, _CAT01_COINCIDENT),
    ("di_lagging", "DI遅行指数", _TAB_DI, _CAT01_LAGGING),
)


def _fetch_ci_di_series(
    client: EstatClient, key: str, label: str, tab: str, cat01: str, start_year: str
) -> SeriesData:
    stat = client.get_stats_data(
        CI_DI_STATS_DATA_ID,
        cd_tab=tab,
        cd_cat01=cat01,
        cd_time_from=f"{start_year}000000",
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations: list[Observation] = []
    for v in values:
        if v.get("@tab") != tab or v.get("@cat01") != cat01:
            continue
        iso = estat_month_to_iso(v.get("@time"))
        if iso is None:
            continue
        raw = v.get("$")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        observations.append(Observation(date=iso, value=value))

    observations.sort(key=lambda o: o.date)
    unit = "指数(2020=100)" if tab == _TAB_CI else "指数(0-100)"
    return SeriesData(
        key=key,
        label=label,
        source=f"e-Stat:{CI_DI_STATS_DATA_ID}/tab={tab},cat01={cat01}",
        frequency="m",
        unit=unit,
        observations=observations,
    )


def fetch_ci_di(client: EstatClient | None = None, start_year: str = "2024") -> list[SeriesData]:
    """景気動向指数（CI・DI、先行/一致/遅行の6系列）を取得する。"""
    client = client or EstatClient()
    return [
        _fetch_ci_di_series(client, key, label, tab, cat01, start_year)
        for key, label, tab, cat01 in _CI_DI_SPECS
    ]


@dataclass(frozen=True)
class FredMarketSpec:
    key: str
    label: str
    series_id: str
    frequency: str
    unit: str


_FRED_MARKET_SPECS: tuple[FredMarketSpec, ...] = (
    FredMarketSpec("nikkei225", "日経平均株価", "NIKKEI225", "w", "円"),
    FredMarketSpec("jp_10y", "日本 10年国債利回り", "IRLTLT01JPM156N", "m", "%"),
)


def fetch_jp_market(
    client: FredClient | None = None,
    start: str = "2025-08-01",
) -> list[SeriesData]:
    """FRED から日経平均・日本10年国債利回りを取得する。"""
    client = client or FredClient()
    out: list[SeriesData] = []
    for spec in _FRED_MARKET_SPECS:
        observations = client.get_series(
            spec.series_id, observation_start=start, frequency=spec.frequency
        )
        out.append(
            SeriesData(
                key=spec.key,
                label=spec.label,
                source=f"FRED:{spec.series_id}",
                frequency=spec.frequency,
                unit=spec.unit,
                observations=observations,
            )
        )
    return out


def main() -> None:
    from .store import save_bundle

    parser = argparse.ArgumentParser(description="日本の市場・景気動向指数を取得（japan_market.json）")
    parser.add_argument("--ci-di-start-year", default="2024")
    parser.add_argument("--market-start", default="2025-08-01")
    args = parser.parse_args()

    series = fetch_ci_di(start_year=args.ci_di_start_year) + fetch_jp_market(start=args.market_start)
    for s in series:
        latest = s.latest()
        latest_str = f"{latest.date} = {latest.value}" if latest else "（有効値なし）"
        print(f"[OK]   {s.key:16s} {s.label} … {len(s.observations)}点 / 最新 {latest_str}")

    out_path = save_bundle(series, "japan_market.json")
    print(f"\n保存: {out_path}（{len(series)}指標）")


if __name__ == "__main__":
    main()
