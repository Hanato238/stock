"""FRED から米国側マクロ指標を取得する（マクロ経済モニターの米国セクション用）。

日本の CI/DI に相当する公式指標が米国にはないため、シカゴ連銀の CFNAI（National
Activity Index）を採用する。ISM PMI・Conference Board LEI（USSLIND）・OECD CLI
（USALOLITONOSTSAM）はいずれも FRED では現行維持されていない/取得不可のため見送った
（2026-08-30 に実地確認済み）。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .fred import FredClient
from .store import SeriesData


@dataclass(frozen=True)
class FredUsSpec:
    key: str
    label: str
    series_id: str
    frequency: str
    unit: str


# CPI は水準（指数）で保存し、前年比は macro.context / macro.narrative 側で算出する
# （overall.json の cpi_core と同じ設計）。GDP成長率のみ FRED 側が既に前期比年率(%)。
US_INDICATORS: tuple[FredUsSpec, ...] = (
    FredUsSpec("cfnai", "米国 景気活動指数 CFNAI", "CFNAI", "m", "指数"),
    FredUsSpec("cfnai_ma3", "米国 景気活動指数 CFNAI-MA3（3か月平均）", "CFNAIMA3", "m", "指数"),
    FredUsSpec("sp500", "S&P500", "SP500", "w", "pt"),
    FredUsSpec("us_10y", "米国 10年国債利回り", "DGS10", "m", "%"),
    FredUsSpec("fed_funds", "FF金利誘導目標（実効ベース平均）", "FEDFUNDS", "m", "%"),
    FredUsSpec("us_cpi", "米国 CPI（総合、水準）", "CPIAUCSL", "m", "指数"),
    FredUsSpec("us_cpi_core", "米国 コアCPI（食品・エネルギー除く、水準）", "CPILFESL", "m", "指数"),
    FredUsSpec("us_gdp_growth", "米国 実質GDP成長率（前期比年率）", "A191RL1Q225SBEA", "q", "%"),
)


def fetch_us(
    client: FredClient | None = None,
    start: str = "2025-01-01",
    indicators: tuple[FredUsSpec, ...] = US_INDICATORS,
) -> list[SeriesData]:
    """米国側マクロ指標を取得する。"""
    client = client or FredClient()
    out: list[SeriesData] = []
    for spec in indicators:
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

    parser = argparse.ArgumentParser(description="米国側マクロ指標を取得（us.json）")
    parser.add_argument("--start", default="2025-01-01")
    args = parser.parse_args()

    series = fetch_us(start=args.start)
    for s in series:
        latest = s.latest()
        latest_str = f"{latest.date} = {latest.value}" if latest else "（有効値なし）"
        print(f"[OK]   {s.key:14s} {s.label} … {len(s.observations)}点 / 最新 {latest_str}")

    out_path = save_bundle(series, "us.json")
    print(f"\n保存: {out_path}（{len(series)}指標）")


if __name__ == "__main__":
    main()
