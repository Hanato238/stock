"""マクロ全体系 4 指標を取得し JSON キャッシュ（overall.json）へ保存する。

B主軸（経済全体の文脈）に効く指標群。業種非依存なので e-Stat 業種別 DI/IIP を
待たずに先行取得できる。生の水準値を時系列で保存し、成長率などの加工は後段の
context.py に委ねる（前年同期比の計算等）。

ソースはハイブリッド:
  - FRED: USD/JPY・政策金利・名目GDP（3 指標）
  - e-Stat: コア CPI（FRED の日本 CPI 月次指数が現行維持されていないため一次ソースへ）

使い方:
    uv run python -m macro.fetch                # 全指標を取得して overall.json 更新
    uv run python -m macro.fetch --start 2010-01-01
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import requests

from .fetch_cpi import fetch_cpi
from .fred import FredClient
from .store import SeriesData, save_bundle

_DEFAULT_START = "2015-01-01"
_OVERALL_FILENAME = "overall.json"


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    label: str
    series_id: str  # FRED series ID
    frequency: str  # d/m/q（FRED 側で集計）
    unit: str


# FRED 全体系 3 指標。series_id は現行維持されている系列を実地検証済み（2026-08-27）。
# CPI は FRED の日本「月次指数」系列が OECD MEI 打ち切りで現行維持されていないため、
# 総務省 e-Stat（月次・現行の一次ソース）へ移管した（macro/estat.py, macro/fetch_cpi.py）。
OVERALL_INDICATORS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec(
        key="usd_jpy",
        label="USD/JPY 為替レート",
        series_id="DEXJPUS",
        frequency="m",
        unit="円/ドル",
    ),
    IndicatorSpec(
        key="policy_rate",
        label="政策金利（無担保コール翌日物近似）",
        series_id="IRSTCI01JPM156N",
        frequency="m",
        unit="%",
    ),
    IndicatorSpec(
        key="nominal_gdp",
        label="名目GDP（水準）",
        series_id="JPNNGDP",
        frequency="q",
        unit="10億円",
    ),
)


def fetch_overall(
    client: FredClient | None = None,
    start: str = _DEFAULT_START,
    indicators: tuple[IndicatorSpec, ...] = OVERALL_INDICATORS,
) -> tuple[list[SeriesData], list[tuple[str, str]]]:
    """全体系指標を取得する。

    Returns:
        (取得成功した SeriesData のリスト, (指標キー, エラー文) の失敗リスト)
    """
    client = client or FredClient()
    fetched: list[SeriesData] = []
    failures: list[tuple[str, str]] = []

    for spec in indicators:
        try:
            observations = client.get_series(
                spec.series_id,
                observation_start=start,
                frequency=spec.frequency,
            )
            if not observations:
                failures.append((spec.key, f"観測点ゼロ（series_id={spec.series_id}）"))
                continue
            fetched.append(
                SeriesData(
                    key=spec.key,
                    label=spec.label,
                    source=f"FRED:{spec.series_id}",
                    frequency=spec.frequency,
                    unit=spec.unit,
                    observations=observations,
                )
            )
        except requests.HTTPError as e:
            failures.append((spec.key, f"HTTP {e.response.status_code}（series_id={spec.series_id}）"))
        except requests.RequestException as e:
            failures.append((spec.key, f"{type(e).__name__}: {e}"))

    return fetched, failures


def _print_ok(series: SeriesData) -> None:
    latest = series.latest()
    latest_str = f"{latest.date} = {latest.value}" if latest else "（有効値なし）"
    print(f"[OK]   {series.key:14s} {series.label} … {len(series.observations)}点 / 最新 {latest_str}")


def main() -> None:
    parser = argparse.ArgumentParser(description="マクロ全体系指標（FRED + e-Stat）を取得")
    parser.add_argument("--start", default=_DEFAULT_START, help="取得開始日 YYYY-MM-DD")
    args = parser.parse_args()

    # FRED（USD/JPY・政策金利・名目GDP）
    fetched, failures = fetch_overall(start=args.start)
    for series in fetched:
        _print_ok(series)
    for key, err in failures:
        print(f"[FAIL] {key:14s} {err}")

    # e-Stat（コア CPI）— キー未設定や API 不通でも他指標の保存は止めない
    start_year = args.start[:4]
    try:
        cpi = fetch_cpi(start_year=start_year)
        _print_ok(cpi)
        fetched.append(cpi)
    except KeyError:
        print("[SKIP] cpi_core        ESTAT_APP_ID 未設定のため CPI をスキップ")
    except requests.RequestException as e:
        print(f"[FAIL] cpi_core        {type(e).__name__}: {e}")

    if fetched:
        out_path = save_bundle(fetched, _OVERALL_FILENAME)
        print(f"\n保存: {out_path}（{len(fetched)}指標）")
    else:
        print("\n取得成功した指標がありません。FRED_API_KEY / ESTAT_APP_ID を確認してください。")


if __name__ == "__main__":
    main()
