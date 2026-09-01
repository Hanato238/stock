"""マクロ環境サマリブロックの生成（B主軸の心臓部）。

overall.json の時系列から、有報の「決算期末時点の環境」と「足元（最新）の環境」を
両方切り出し、Gemini 評価プロンプトへ前置する日本語テキストブロックを組み立てる。
生の水準値に加え、前年比（YoY）を算出して局面の向き（円安/円高・利上げ・インフレ）を
読み取りやすくする。

業種別 DI/IIP（補助 A）は未実装のため industry_key は受け取るだけ（将来接続点）。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta

from .fred import Observation
from .store import SeriesData, load_bundle

_OVERALL_FILENAME = "overall.json"
# YoY 比較の基準（約 1 年前）。月次/四半期/年次いずれも「以前で最も近い点」を拾う。
_YEAR_DAYS = 365


@dataclass
class Snapshot:
    """ある時点における 1 指標の切り出し。

    yoy: 前年比の数値。yoy_kind が "pct" なら変化率(%)、"pt" なら差分(%pt)。
    金利のようなゼロ近傍の率は変化率が無意味なため差分(%pt)で表す。
    """

    key: str
    label: str
    unit: str
    value: float | None
    date: str | None
    yoy: float | None
    yoy_kind: str  # "pct" | "pt"


def _parse_period_end(period: str) -> date:
    """決算期指定（'YYYY-MM' または 'YYYY-MM-DD'）を対象日に変換する。

    月のみ指定なら月末日を対象にする（例: '2025-03' → 2025-03-31）。
    """
    parts = period.split("-")
    year, month = int(parts[0]), int(parts[1])
    if len(parts) >= 3:
        return date(year, month, int(parts[2]))
    # 翌月 1 日の前日 ＝ 月末
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return next_month - timedelta(days=1)


def _as_of(series: SeriesData, target: date) -> Observation | None:
    """target 以前で最新の有効観測点を返す（観測列は昇順前提）。"""
    result: Observation | None = None
    for obs in series.observations:
        if obs.value is None:
            continue
        if date.fromisoformat(obs.date) <= target:
            result = obs
        else:
            break
    return result


def _snapshot_at(series: SeriesData, ref: date) -> Snapshot:
    """ref 時点の値と、その約 1 年前比（YoY）を切り出す。

    金利（unit=="%"）は差分(%pt)、それ以外は変化率(%)で YoY を算出する。
    """
    # 率そのもの（金利）は前年差(%pt)、水準・指数・価格は変化率(%)。
    kind = "pt" if series.unit == "%" else "pct"
    cur = _as_of(series, ref)
    if cur is None:
        return Snapshot(series.key, series.label, series.unit, None, None, None, kind)
    prev = _as_of(series, date.fromisoformat(cur.date) - timedelta(days=_YEAR_DAYS))
    yoy: float | None = None
    if prev is not None and prev.value is not None:
        if kind == "pt":
            yoy = cur.value - prev.value
        elif prev.value != 0:
            yoy = (cur.value - prev.value) / abs(prev.value) * 100.0
    return Snapshot(series.key, series.label, series.unit, cur.value, cur.date, yoy, kind)


def _fmt_value(value: float, unit: str) -> str:
    if unit.startswith("指数"):
        return f"{value:.1f}"
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "円/ドル":
        return f"{value:.1f}円"
    if unit == "10億円":
        return f"{value / 1000:.1f}兆円"
    return f"{value:g}{unit}"


def _fmt_line(snap: Snapshot) -> str:
    if snap.value is None:
        return f"- {snap.label}: データなし"
    line = f"- {snap.label}: {_fmt_value(snap.value, snap.unit)}"
    if snap.yoy is not None:
        if snap.yoy_kind == "pt":
            line += f"（前年差 {snap.yoy:+.2f}%pt）"
        else:
            line += f"（前年比 {snap.yoy:+.1f}%）"
    return line


def build_macro_context(
    fiscal_period: str,
    industry_key: str | None = None,
    bundle_file: str = _OVERALL_FILENAME,
) -> str:
    """マクロ環境サマリブロックを生成する。

    Args:
        fiscal_period: 有報の決算期。'YYYY-MM'（例: '2025-03'）または 'YYYY-MM-DD'。
        industry_key: 業種キー（業種別 DI/IIP 接続用。現状未使用）。
        bundle_file: 読み込む overall.json 名。

    Returns:
        Gemini 評価プロンプトへ前置する日本語テキストブロック。
    """
    bundle = load_bundle(bundle_file)
    ref = _parse_period_end(fiscal_period)
    # 足元は各系列の最終点（十分未来の日付で as_of すると末尾を拾う）。
    latest_ref = date.max

    fiscal_lines = [_fmt_line(_snapshot_at(s, ref)) for s in bundle.values()]
    latest_snaps = [_snapshot_at(s, latest_ref) for s in bundle.values()]
    latest_lines = [_fmt_line(s) for s in latest_snaps]

    latest_dates = [s.date for s in latest_snaps if s.date]
    latest_label = max(latest_dates) if latest_dates else "不明"

    parts = [
        "## マクロ経済環境",
        "",
        f"### 決算期環境（{fiscal_period} 期末時点）",
        *fiscal_lines,
        "",
        f"### 足元環境（最新: {latest_label}）",
        *latest_lines,
    ]
    if industry_key:
        parts += ["", f"（業種別指標 [{industry_key}] は未接続）"]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="マクロ環境サマリブロックを生成")
    parser.add_argument("fiscal_period", help="決算期 YYYY-MM（例: 2025-03）")
    parser.add_argument("--industry", default=None, help="業種キー（任意）")
    args = parser.parse_args()
    print(build_macro_context(args.fiscal_period, industry_key=args.industry))


if __name__ == "__main__":
    main()
