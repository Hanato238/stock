"""マクロ経済モニターページ（日本・米国）の HTML 生成。

data/macro/*.json（japan_market.json, us.json, overall.json）と
macro.narrative の LLM 生成結果を組み合わせて、企業非依存の静的 HTML ページを
組み立てる。デザインは grill-me で確定した「帳簿/印鑑」デザインシステムを踏襲し、
個別株評価レポート（Phase 6 で HTML 化予定）と同じトークン・コンポーネントを
将来共有できるようにしている。

使い方:
    uv run python -m macro.report                       # data/macro/report.html へ出力
    uv run python -m macro.report --model gemini-flash-latest --out /tmp/x.html
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from .context import _fmt_value, _snapshot_at
from .narrative import (
    SECTOR_TAB_KEYS as _TAB_SECTOR_KEY,
)
from .narrative import (
    MacroNarrative,
    NarrativeError,
    SectorNarrative,
    generate_narrative,
    generate_sector_narratives,
    save_narratives,
    save_sector_narratives,
)
from .store import SeriesData, load_bundle

_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "macro"
_INDICATOR_CATALOG_PATH = _DEFAULT_OUTPUT_DIR / "indicator_catalog.json"

_TONE_CLASS = {"expand": "expand", "contract": "contract", "neutral": "neutral"}

# 個別株ページの「関連セクター指標」がどう選ばれるかを示す実例（森永乳業、乳製品製造業）。
# マクロページは企業非依存だが、選択の仕組みを実感できるよう小さく1社分だけ例示する。
_EXAMPLE_INDUSTRY_TERMS = ("乳製品製造業", "市乳処理業", "アイスクリーム製造業", "食料品卸売業", "飼料卸売業")
_EXAMPLE_COMPANY_LABEL = "森永乳業（乳製品製造業）"

# タブ（5分野）ごとの代表指標（macro/fetch_sectors.py が実際に取得・検証した5系列）は
# macro.narrative.SECTOR_TAB_KEYS と共有（分野解説の生成側と表示側でマッピングを揃えるため）。
# 各分野の景況推移を一目で示すための代表選定であり、38指標のうち他の33指標は
# カタログ情報（カード）のみで時系列は今回のスコープ外（TODO.md参照）。


class ReportError(RuntimeError):
    """マクロレポート生成の失敗（データ未取得など）。"""


@dataclass
class MacroBundle:
    japan_market: dict[str, SeriesData]
    overall: dict[str, SeriesData]
    us: dict[str, SeriesData]
    jp_narrative: MacroNarrative
    us_narrative: MacroNarrative
    sector_narratives: dict[str, SectorNarrative] = field(default_factory=dict)


def load_macro_bundle(*, model: str | None = None) -> MacroBundle:
    """3 つの JSON バンドルを読み込み、日米それぞれの LLM 見立てを生成する。"""
    try:
        japan_market = load_bundle("japan_market.json")
        overall = load_bundle("overall.json")
        us = load_bundle("us.json")
    except FileNotFoundError as e:
        raise ReportError(
            f"{e.filename} が見つかりません。"
            "`uv run python -m macro.fetch` / `macro.fetch_jp_market` / `macro.fetch_us` を先に実行してください。"
        ) from e

    try:
        jp_narrative = generate_narrative("japan", model=model)
        us_narrative = generate_narrative("us", model=model)
    except NarrativeError as e:
        raise ReportError(str(e)) from e

    # 個別株評価レポートが LLM を再呼び出しせず引用できるようキャッシュする。
    save_narratives(jp_narrative, us_narrative)

    # 分野解説は sectors.json 未取得でもページ生成全体は止めない（ベストエフォート）。
    try:
        sector_narratives = generate_sector_narratives(model=model)
    except NarrativeError:
        sector_narratives = {}
    if sector_narratives:
        save_sector_narratives(sector_narratives)

    return MacroBundle(
        japan_market=japan_market,
        overall=overall,
        us=us,
        jp_narrative=jp_narrative,
        us_narrative=us_narrative,
        sector_narratives=sector_narratives,
    )


# --------------------------------------------------------------------------
# 数値フォーマット（タイル・基礎指標カード）
# --------------------------------------------------------------------------

def _snap(series: SeriesData) -> tuple[str, str, str]:
    """(値の文字列, 前年比/前年差の文字列, 変化の向き "up"|"down"|"") を返す。"""
    s = _snapshot_at(series, date.max)
    if s.value is None:
        return "データなし", "", ""
    value_str = _fmt_value(s.value, s.unit)
    if s.yoy is None:
        return value_str, "", ""
    if s.yoy_kind == "pt":
        delta_str = f"前年差 {s.yoy:+.2f}%pt"
    else:
        delta_str = f"前年比 {s.yoy:+.1f}%"
    direction = "up" if s.yoy >= 0 else "down"
    return value_str, delta_str, direction


def _range_stats(series: SeriesData) -> dict:
    """期間内の高値・安値・変化率を返す（52週固定ではなく実際の取得期間ベース）。"""
    valid = [o for o in series.observations if o.value is not None]
    if len(valid) < 2:
        return {}
    first, last = valid[0], valid[-1]
    vmax = max(valid, key=lambda o: o.value)
    vmin = min(valid, key=lambda o: o.value)
    change_pct = (last.value - first.value) / abs(first.value) * 100.0 if first.value else None
    return {
        "start_date": first.date,
        "change_pct": change_pct,
        "high": vmax.value,
        "high_date": vmax.date,
        "low": vmin.value,
        "low_date": vmin.date,
        "latest": last.value,
    }


def _di_status(value: float) -> str:
    return "expand" if value >= 50 else "contract"


# --------------------------------------------------------------------------
# チャート用 JSON（既存モックアップの汎用 renderLineChart() が期待する形）
# --------------------------------------------------------------------------

def _month_label(iso_date: str) -> str:
    y, m, _ = iso_date.split("-")
    return f"{y}/{int(m)}"


def _ci_series_json(market: dict[str, SeriesData]) -> str:
    order = [("ci_leading", "先行指数"), ("ci_coincident", "一致指数"), ("ci_lagging", "遅行指数")]
    data = {
        label: [
            {"m": _month_label(o.date), "v": o.value}
            for o in market[key].observations
            if o.value is not None
        ]
        for key, label in order
        if key in market
    }
    return json.dumps(data, ensure_ascii=False)


def _weekly_series_json(series: SeriesData) -> str:
    return json.dumps(
        [{"date": o.date, "value": o.value} for o in series.observations if o.value is not None],
        ensure_ascii=False,
    )


def _cfnai_series_json(us: dict[str, SeriesData]) -> tuple[str, str]:
    raw = json.dumps(
        [{"date": o.date, "value": o.value} for o in us["cfnai"].observations if o.value is not None],
        ensure_ascii=False,
    )
    ma3 = json.dumps(
        [{"date": o.date, "value": o.value} for o in us["cfnai_ma3"].observations if o.value is not None],
        ensure_ascii=False,
    )
    return raw, ma3


# --------------------------------------------------------------------------
# HTML 断片の組み立て
# --------------------------------------------------------------------------

def _paragraph_html(narrative: MacroNarrative) -> str:
    parts = []
    for p in narrative.paragraphs:
        parts.append(f'      <p><b>{p.heading}:</b> {p.body}</p>')
    return "\n".join(parts)


def _verdict_html(narrative: MacroNarrative, *, tone_class_map: dict[str, str]) -> str:
    css_var = {"expand": "var(--risk-low)", "contract": "var(--risk-high)", "neutral": "var(--ink-faint)"}
    pill_class = tone_class_map.get(narrative.tone, "neutral")
    border = css_var.get(narrative.tone, "var(--ink-faint)")
    region_label = "日本" if narrative.region == "japan" else "米国"
    return f"""    <div class="verdict-panel" style="border-left-color:{border}">
      <div class="verdict-head">
        <span class="gauge-status {pill_class}">{narrative.tone_label}</span>
        <span class="v-title">{region_label} 総合見立て</span>
      </div>
      <p>{narrative.verdict}</p>
    </div>"""


# --------------------------------------------------------------------------
# セクター指標カタログ（タブ表示）
# --------------------------------------------------------------------------

def _load_indicator_catalog(path: Path = _INDICATOR_CATALOG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


_DIFFICULTY_BADGE = {
    "easy": ("機械取得可", "easy"),
    "manual": ("手動DL", "manual"),
    "hard": ("取得困難", "hard"),
}


def _indicator_card_html(ind: dict) -> str:
    label, cls = _DIFFICULTY_BADGE.get(ind.get("difficulty", ""), (ind.get("difficulty", "?"), "manual"))
    return f"""        <div class="ind-card">
          <div class="ind-card-head">
            <span class="ind-name">{ind['name_ja']}</span>
            <span class="ind-badge {cls}">{label}</span>
          </div>
          <p class="ind-desc">{ind.get('description', '')}</p>
          <p class="ind-meta">{ind.get('publisher', '')} ／ {ind.get('frequency', '')}</p>
        </div>"""


def _trend_caption(series: SeriesData) -> str:
    """系列の直近値・期間内変化を1行の事実文で表す（LLMは使わない、単純な集計）。"""
    valid = [o for o in series.observations if o.value is not None]
    if not valid:
        return "データなし"
    last = valid[-1]
    line = f"直近（{last.date}）: {_fmt_value(last.value, series.unit)}"
    if len(valid) >= 2:
        first = valid[0]
        delta = last.value - first.value
        direction = "上昇" if delta >= 0 else "低下"
        if series.unit == "%":
            line += f" ／ {first.date}以降 {delta:+.2f}%pt {direction}"
        elif first.value:
            pct = delta / abs(first.value) * 100.0
            line += f" ／ {first.date}以降 {pct:+.1f}% {direction}"
    return line


def _sector_chart_json(series: SeriesData) -> str:
    return json.dumps(
        [{"date": o.date, "value": o.value} for o in series.observations if o.value is not None],
        ensure_ascii=False,
    )


def _line_chart_js(
    svg_id: str,
    label: str,
    series: SeriesData,
    *,
    width: int,
    height: int,
    pad_r: int,
    pad_t: int,
    pad_b: int,
    label_every_divisor: int = 6,
) -> str:
    data_json = _sector_chart_json(series)
    unit = series.unit
    return f"""
  (function () {{
    const pts = {data_json};
    if (!pts.length) return;
    const series = [{{ name: {label!r}, color: cssVar('--accent'), points: pts.map(d => ({{ y: d.value, label: d.date.slice(2, 7) }})) }}];
    renderLineChart(document.getElementById({svg_id!r}), series, {{
      width: {width}, height: {height}, padR: {pad_r}, padT: {pad_t}, padB: {pad_b},
      labelEvery: Math.max(1, Math.floor(pts.length / {label_every_divisor})),
      yfmt: v => v.toFixed({1 if unit == "%" or unit.startswith("指数") else 0}),
      tipfmt: v => v.toLocaleString(),
      endLabel: (s, last) => last.y.toLocaleString(),
    }});
  }})();"""


def _sector_trend_card_html(
    tab_index: int, tab_name: str, series: SeriesData | None, narrative_body: str = ""
) -> str:
    if series is None:
        return ""
    svg_id = f"sector-chart-{tab_index}"
    narrative_html = f'\n          <p class="chart-narrative">{narrative_body}</p>' if narrative_body else ""
    return f"""        <div class="chart-card sector-trend-card">
          <div class="chart-head">
            <span class="chart-title">{tab_name}の景況推移: {series.label}</span>
          </div>
          <div class="chart-wrap"><svg id="{svg_id}" viewBox="0 0 720 160" preserveAspectRatio="xMidYMid meet"></svg></div>
          <p class="chart-foot-note">{_trend_caption(series)} ／ 出典: {series.source}</p>{narrative_html}
        </div>"""


def _sector_mini_chart_html(tab_index: int, mini_index: int, ind: dict, series: SeriesData) -> str:
    svg_id = f"sector-mini-{tab_index}-{mini_index}"
    return f"""          <div class="chart-card mini-chart-card">
            <div class="chart-head"><span class="chart-title">{ind.get('name_ja', series.label)}</span></div>
            <div class="chart-wrap"><svg id="{svg_id}" viewBox="0 0 380 130" preserveAspectRatio="xMidYMid meet"></svg></div>
            <p class="chart-foot-note">{_trend_caption(series)} ／ 出典: {series.source}</p>
          </div>"""


def _catalog_tabs_html(
    catalog: dict,
    sectors: dict[str, SeriesData] | None,
    sector_narratives: dict[str, SectorNarrative] | None = None,
    sectors_extra: dict[str, SeriesData] | None = None,
) -> tuple[str, str, str]:
    """(タブボタンHTML, タブパネルHTML, セクター推移チャートJS) を返す。

    タブ内は3階層で表示する: ①代表チャート（5分野固定・LLM解説文付き）
    ②実データが確認できたその他の指標のミニチャート群（sectors_extra.json）
    ③まだ実データがない指標のメタデータのみカード（従来通り）。
    重複表示を避けるため、①②で表示した指標は③のカード一覧から除外する。
    """
    tabs: list[str] = catalog.get("tabs", [])
    by_tab: dict[str, list[dict]] = {t: [] for t in tabs}
    for ind in catalog.get("indicators", []):
        by_tab.setdefault(ind.get("tab", "その他"), []).append(ind)
    sectors_extra = sectors_extra or {}

    buttons = []
    panels = []
    chart_js_parts = []
    for i, tab in enumerate(tabs):
        active = " active" if i == 0 else ""
        items = by_tab.get(tab, [])
        buttons.append(
            f'<button class="tab-btn{active}" data-tab="cat-tab-{i}">{tab}<span class="tab-count">{len(items)}</span></button>'
        )
        primary_key = _TAB_SECTOR_KEY.get(tab, "")
        series = (sectors or {}).get(primary_key)
        narrative = (sector_narratives or {}).get(tab)
        trend_html = _sector_trend_card_html(i, tab, series, narrative.body if narrative else "")
        if series is not None:
            chart_js_parts.append(
                _line_chart_js(f"sector-chart-{i}", series.label, series, width=720, height=160, pad_r=60, pad_t=10, pad_b=22)
            )

        mini_items = [ind for ind in items if ind.get("key") != primary_key and ind.get("key") in sectors_extra]
        mini_cards = []
        for j, ind in enumerate(mini_items):
            mseries = sectors_extra[ind["key"]]
            mini_cards.append(_sector_mini_chart_html(i, j, ind, mseries))
            chart_js_parts.append(
                _line_chart_js(
                    f"sector-mini-{i}-{j}", mseries.label, mseries,
                    width=380, height=130, pad_r=40, pad_t=8, pad_b=20, label_every_divisor=4,
                )
            )
        mini_grid_html = ""
        if mini_cards:
            mini_cards_html = "\n".join(mini_cards)
            mini_grid_html = f'\n        <div class="mini-chart-grid">\n{mini_cards_html}\n        </div>'

        charted_keys = {ind["key"] for ind in mini_items} | ({primary_key} if series is not None else set())
        remaining_items = [ind for ind in items if ind.get("key") not in charted_keys]
        cards = "\n".join(_indicator_card_html(ind) for ind in remaining_items)
        panels.append(
            f'      <div class="tab-panel{active}" id="cat-tab-{i}">\n'
            f"{trend_html}"
            f"{mini_grid_html}\n"
            f'        <div class="ind-grid">\n{cards}\n        </div>\n      </div>'
        )

    return "\n      ".join(buttons), "\n".join(panels), "".join(chart_js_parts)


def _example_selection_html() -> str:
    """個別株ページの指標選択がどう動くかを示す小さな実例（1社分のみ）。失敗時は空文字。"""
    try:
        from .indicators import IndicatorSelectionError, select_indicators

        selected = select_indicators(list(_EXAMPLE_INDUSTRY_TERMS))
    except (ImportError, IndicatorSelectionError):
        selected = []
    if not selected:
        return ""
    items = "".join(f"<li>{ind['name_ja']}</li>" for ind in selected)
    return f"""    <div class="example-box">
      <p class="example-title">例: {_EXAMPLE_COMPANY_LABEL}の個別株ページの場合</p>
      <p class="example-lede">業種タームから、このカタログの中から以下がLLMにより自動選択されます（<code>macro/indicators.py</code>）:</p>
      <ul class="example-list">{items}</ul>
    </div>"""


def render_html(bundle: MacroBundle) -> str:
    market = bundle.japan_market
    overall = bundle.overall
    us = bundle.us

    nikkei_val, nikkei_delta, nikkei_dir = _snap(market["nikkei225"])
    ci_val, ci_delta, _ = _snap(market["ci_coincident"])
    di_coincident_latest = market["di_coincident"].latest()
    di_val = f"{di_coincident_latest.value:.1f}" if di_coincident_latest else "—"
    di_status = _di_status(di_coincident_latest.value) if di_coincident_latest else "neutral"
    usd_jpy_val, usd_jpy_delta, usd_jpy_dir = _snap(overall["usd_jpy"])
    sp500_val, sp500_delta, sp500_dir = _snap(us["sp500"])
    cfnai_latest = us["cfnai"].latest()
    cfnai_ma3_latest = us["cfnai_ma3"].latest()
    cfnai_val = f"{cfnai_latest.value:.2f}" if cfnai_latest else "—"
    cfnai_ma3_str = f"{cfnai_ma3_latest.value:+.2f}" if cfnai_ma3_latest else "?"
    fed_val, fed_delta, fed_dir = _snap(us["fed_funds"])
    cpi_core_val, cpi_core_delta, _ = _snap(us["us_cpi_core"])

    nikkei_stats = _range_stats(market["nikkei225"])
    sp500_stats = _range_stats(us["sp500"])
    us_10y_latest = us["us_10y"].latest()
    jp_10y_latest = market["jp_10y"].latest()

    fetched_at = max(
        (s.observations[-1].date for s in {**market, **overall, **us}.values() if s.observations),
        default="不明",
    )

    ci_series_json = _ci_series_json(market)
    nikkei_json = _weekly_series_json(market["nikkei225"])
    cfnai_raw_json, cfnai_ma3_json = _cfnai_series_json(us)
    sp500_json = _weekly_series_json(us["sp500"])

    di_leading_latest = market["di_leading"].latest()
    di_lagging_latest = market["di_lagging"].latest()
    di_gauge_data = json.dumps(
        {
            "先行指数": {"m": _month_label(di_leading_latest.date), "v": di_leading_latest.value}
            if di_leading_latest
            else None,
            "一致指数": {"m": _month_label(di_coincident_latest.date), "v": di_coincident_latest.value}
            if di_coincident_latest
            else None,
            "遅行指数": {"m": _month_label(di_lagging_latest.date), "v": di_lagging_latest.value}
            if di_lagging_latest
            else None,
        },
        ensure_ascii=False,
    )

    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    catalog = _load_indicator_catalog()
    try:
        sectors = load_bundle("sectors.json")
    except FileNotFoundError:
        sectors = {}
    try:
        sectors_extra = load_bundle("sectors_extra.json")
    except FileNotFoundError:
        sectors_extra = {}
    catalog_tab_buttons, catalog_tab_panels, sector_chart_js = _catalog_tabs_html(
        catalog, sectors, bundle.sector_narratives, sectors_extra
    )
    example_html = _example_selection_html()
    catalog_keys = {ind.get("key") for ind in catalog.get("indicators", [])}
    sector_charted_count = len((set(sectors) | set(sectors_extra)) & catalog_keys)
    sector_total_count = len(catalog.get("indicators", []))

    return _TEMPLATE.format(
        generated_at=generated_at,
        fetched_at=fetched_at,
        nikkei_val=nikkei_val,
        nikkei_delta=nikkei_delta,
        nikkei_dir=nikkei_dir,
        ci_val=ci_val,
        ci_delta=ci_delta,
        di_val=di_val,
        di_status=di_status,
        di_status_label="拡大局面の目安（&gt;50）" if di_status == "expand" else "後退局面の目安（&lt;50）",
        usd_jpy_val=usd_jpy_val,
        usd_jpy_delta=usd_jpy_delta,
        usd_jpy_dir=usd_jpy_dir,
        sp500_val=sp500_val,
        sp500_delta=sp500_delta,
        sp500_dir=sp500_dir,
        cfnai_val=cfnai_val,
        cfnai_ma3_str=cfnai_ma3_str,
        fed_val=fed_val,
        fed_delta=fed_delta,
        fed_dir=fed_dir,
        cpi_core_val=cpi_core_val,
        cpi_core_delta=cpi_core_delta,
        nikkei_change_pct=f"{nikkei_stats.get('change_pct', 0):+.1f}%" if nikkei_stats else "—",
        nikkei_high=f"{nikkei_stats.get('high', 0):,.0f}円" if nikkei_stats else "—",
        nikkei_high_date=nikkei_stats.get("high_date", ""),
        nikkei_low=f"{nikkei_stats.get('low', 0):,.0f}円" if nikkei_stats else "—",
        nikkei_low_date=nikkei_stats.get("low_date", ""),
        jp_10y_val=f"{jp_10y_latest.value:.2f}%" if jp_10y_latest else "—",
        jp_10y_date=jp_10y_latest.date if jp_10y_latest else "",
        sp500_change_pct=f"{sp500_stats.get('change_pct', 0):+.1f}%" if sp500_stats else "—",
        sp500_high=f"{sp500_stats.get('high', 0):,.0f}" if sp500_stats else "—",
        sp500_high_date=sp500_stats.get("high_date", ""),
        sp500_low=f"{sp500_stats.get('low', 0):,.0f}" if sp500_stats else "—",
        sp500_low_date=sp500_stats.get("low_date", ""),
        us_10y_val=f"{us_10y_latest.value:.2f}%" if us_10y_latest else "—",
        us_10y_date=us_10y_latest.date if us_10y_latest else "",
        usd_jpy_asof=overall["usd_jpy"].latest().date if overall.get("usd_jpy") and overall["usd_jpy"].latest() else "",
        policy_rate_val=_snap(overall["policy_rate"])[0] if "policy_rate" in overall else "—",
        policy_rate_delta=_snap(overall["policy_rate"])[1] if "policy_rate" in overall else "",
        policy_rate_asof=overall["policy_rate"].latest().date if overall.get("policy_rate") and overall["policy_rate"].latest() else "",
        nominal_gdp_val=_snap(overall["nominal_gdp"])[0] if "nominal_gdp" in overall else "—",
        nominal_gdp_delta=_snap(overall["nominal_gdp"])[1] if "nominal_gdp" in overall else "",
        nominal_gdp_asof=overall["nominal_gdp"].latest().date if overall.get("nominal_gdp") and overall["nominal_gdp"].latest() else "",
        cpi_core_asof=overall["cpi_core"].latest().date if overall.get("cpi_core") and overall["cpi_core"].latest() else "",
        cpi_core_full_val=_snap(overall["cpi_core"])[0] if "cpi_core" in overall else "—",
        cpi_core_full_delta=_snap(overall["cpi_core"])[1] if "cpi_core" in overall else "",
        fed_asof=us["fed_funds"].latest().date if us.get("fed_funds") and us["fed_funds"].latest() else "",
        us_cpi_val=_snap(us["us_cpi"])[0] if "us_cpi" in us else "—",
        us_cpi_delta=_snap(us["us_cpi"])[1] if "us_cpi" in us else "",
        us_cpi_core_asof=us["us_cpi_core"].latest().date if us.get("us_cpi_core") and us["us_cpi_core"].latest() else "",
        us_gdp_growth_val=_snap(us["us_gdp_growth"])[0] if "us_gdp_growth" in us else "—",
        us_gdp_growth_asof=us["us_gdp_growth"].latest().date if us.get("us_gdp_growth") and us["us_gdp_growth"].latest() else "",
        jp_reading_html=_paragraph_html(bundle.jp_narrative),
        us_reading_html=_paragraph_html(bundle.us_narrative),
        jp_verdict_html=_verdict_html(bundle.jp_narrative, tone_class_map=_TONE_CLASS),
        us_verdict_html=_verdict_html(bundle.us_narrative, tone_class_map=_TONE_CLASS),
        ci_series_json=ci_series_json,
        di_gauge_data=di_gauge_data,
        nikkei_json=nikkei_json,
        cfnai_raw_json=cfnai_raw_json,
        cfnai_ma3_json=cfnai_ma3_json,
        sp500_json=sp500_json,
        catalog_tab_buttons=catalog_tab_buttons,
        catalog_tab_panels=catalog_tab_panels,
        example_html=example_html,
        sector_chart_js=sector_chart_js,
        sector_charted_count=sector_charted_count,
        sector_total_count=sector_total_count,
    )


def save_report(bundle: MacroBundle, *, directory: Path = _DEFAULT_OUTPUT_DIR, filename: str = "report.html") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(render_html(bundle), encoding="utf-8")
    return path


_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<title>日米マクロ経済モニター</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root {{
    --bg: #EBEFEC; --grid-line: rgba(22, 35, 42, 0.05); --surface: #FFFFFF; --surface-alt: #F3F6F4;
    --ink: #16232A; --ink-muted: #55666C; --ink-faint: #8B9A9C; --border: #D7DFDA; --border-strong: #B9C6C0;
    --accent: #1F4A5C; --accent-soft: #E4EBEA; --on-accent: #FFFFFF;
    --risk-low: #3C7A57; --risk-low-soft: #E4EFE7; --risk-mid: #A8792B; --risk-mid-soft: #F3EADA;
    --risk-high: #A63B33; --risk-high-soft: #F3E1DD;
    --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --chart-grid: #D7DFDA;
    --shadow: 0 1px 2px rgba(22, 35, 42, 0.06), 0 6px 20px -12px rgba(22, 35, 42, 0.18);
    --serif: "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", "Georgia", serif;
    --sans: "Hiragino Sans", "Yu Gothic Medium", "Noto Sans JP", "Helvetica Neue", Arial, sans-serif;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #10161A; --grid-line: rgba(231, 233, 229, 0.045); --surface: #182028; --surface-alt: #1D2731;
      --ink: #E9EDE9; --ink-muted: #A6B3B2; --ink-faint: #6E7C7C; --border: #2B3640; --border-strong: #3B4A54;
      --accent: #7FA9BE; --accent-soft: #223440; --on-accent: #0F1A20;
      --risk-low: #74B893; --risk-low-soft: #1E2E24; --risk-mid: #D2AC5F; --risk-mid-soft: #332A19;
      --risk-high: #DE8579; --risk-high-soft: #35211D;
      --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --chart-grid: #2B3640;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.3), 0 12px 28px -16px rgba(0, 0, 0, 0.5);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #10161A; --grid-line: rgba(231, 233, 229, 0.045); --surface: #182028; --surface-alt: #1D2731;
    --ink: #E9EDE9; --ink-muted: #A6B3B2; --ink-faint: #6E7C7C; --border: #2B3640; --border-strong: #3B4A54;
    --accent: #7FA9BE; --accent-soft: #223440; --on-accent: #0F1A20;
    --risk-low: #74B893; --risk-low-soft: #1E2E24; --risk-mid: #D2AC5F; --risk-mid-soft: #332A19;
    --risk-high: #DE8579; --risk-high-soft: #35211D;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --chart-grid: #2B3640;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.3), 0 12px 28px -16px rgba(0, 0, 0, 0.5);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: repeating-linear-gradient(0deg, var(--grid-line) 0, var(--grid-line) 1px, transparent 1px, transparent 32px), var(--bg);
    color: var(--ink); font-family: var(--sans); line-height: 1.8; -webkit-font-smoothing: antialiased;
  }}
  .page {{ max-width: 920px; margin: 0 auto; padding: 56px 24px 96px; }}
  a {{ color: var(--accent); }}
  .doc-header {{ padding-bottom: 28px; border-bottom: 2px solid var(--border-strong); margin-bottom: 20px; }}
  .eyebrow {{ font-family: var(--serif); font-size: 0.78rem; letter-spacing: 0.28em; color: var(--ink-muted); margin: 0 0 14px; }}
  .company-name {{ font-family: var(--serif); font-weight: 600; font-size: clamp(1.7rem, 4vw, 2.5rem); line-height: 1.35; margin: 0 0 12px; text-wrap: balance; }}
  .meta-row {{ display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 0.86rem; color: var(--ink-muted); font-variant-numeric: tabular-nums; }}
  .meta-row dt {{ display: inline; font-weight: 600; color: var(--ink-faint); }}
  .meta-row .meta-item {{ display: inline-flex; gap: 5px; }}
  .meta-row dd {{ margin: 0; }}
  .toc-nav {{ display: flex; flex-wrap: wrap; gap: 7px 4px; margin: 0 0 40px; padding-bottom: 18px; border-bottom: 1px solid var(--border); font-size: 0.78rem; }}
  .toc-nav a {{ color: var(--ink-muted); text-decoration: none; padding: 3px 10px; border-radius: 999px; border: 1px solid transparent; }}
  .toc-nav a:hover {{ color: var(--accent); border-color: var(--border); background: var(--accent-soft); }}
  section {{ margin-bottom: 44px; }}
  h2 {{ font-family: var(--serif); font-size: 1.15rem; font-weight: 600; margin: 0 0 18px; padding-bottom: 10px; border-bottom: 1px solid var(--border); display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; scroll-margin-top: 20px; }}
  h2 .h2-sub {{ font-family: var(--sans); font-size: 0.76rem; font-weight: 400; color: var(--ink-faint); letter-spacing: 0.04em; }}
  h3.subsection-title {{ font-family: var(--serif); font-size: 1rem; font-weight: 600; margin: 34px 0 6px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; scroll-margin-top: 20px; }}
  .subsection-lede {{ font-size: 0.84rem; color: var(--ink-muted); max-width: 66ch; margin: 0 0 16px; }}
  .section-lede {{ font-size: 0.86rem; color: var(--ink-muted); max-width: 68ch; margin: 0 0 20px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }}
  .tile-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; box-shadow: var(--shadow); margin-bottom: 8px; }}
  .tile {{ background: var(--surface); padding: 16px 18px; }}
  .tile .t-label {{ font-size: 0.72rem; color: var(--ink-faint); margin: 0 0 6px; }}
  .tile .t-value {{ font-variant-numeric: tabular-nums; font-size: 1.35rem; font-weight: 700; line-height: 1.2; }}
  .tile .t-delta {{ display: block; font-size: 0.76rem; font-weight: 600; margin-top: 5px; font-variant-numeric: tabular-nums; }}
  .tile .t-delta.up {{ color: var(--risk-low); }}
  .tile .t-delta.down {{ color: var(--risk-high); }}
  .tile .t-status {{ display: inline-block; margin-top: 6px; font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 999px; }}
  .tile .t-status.expand {{ background: var(--risk-low-soft); color: var(--risk-low); }}
  .tile .t-status.contract {{ background: var(--risk-mid-soft); color: var(--risk-mid); }}
  .tile .t-status.neutral {{ background: var(--surface-alt); color: var(--ink-muted); border: 1px solid var(--border); }}
  @media (max-width: 720px) {{ .tile-row {{ grid-template-columns: repeat(2, 1fr); }} }}
  .region-head {{ display: flex; align-items: baseline; gap: 12px; margin: 40px 0 4px; padding-top: 22px; border-top: 1px dashed var(--border); }}
  .region-head.first {{ margin-top: 32px; padding-top: 0; border-top: none; }}
  .region-label {{ font-family: var(--serif); font-size: 0.72rem; letter-spacing: 0.22em; color: var(--accent); }}
  .region-flag {{ font-size: 0.72rem; color: var(--ink-faint); }}
  .chart-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px 22px 16px; box-shadow: var(--shadow); position: relative; margin-bottom: 14px; }}
  .chart-head {{ display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px; margin-bottom: 6px; }}
  .chart-title {{ font-family: var(--serif); font-weight: 600; font-size: 0.98rem; }}
  .chart-legend {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.76rem; color: var(--ink-muted); }}
  .chart-legend .lg-item {{ display: inline-flex; align-items: center; gap: 6px; }}
  .chart-legend .lg-key {{ width: 14px; height: 2px; border-radius: 1px; display: inline-block; }}
  .chart-wrap {{ position: relative; width: 100%; overflow-x: auto; }}
  .chart-wrap svg {{ display: block; width: 100%; height: auto; }}
  .axis-label {{ font-size: 10px; fill: var(--ink-faint); font-family: var(--sans); }}
  .gridline {{ stroke: var(--chart-grid); stroke-width: 1; }}
  .series-line {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
  .end-dot {{ r: 4; stroke: var(--surface); stroke-width: 2; }}
  .crosshair-line {{ stroke: var(--ink-faint); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0; pointer-events: none; }}
  .hover-dot {{ r: 5; stroke: var(--surface); stroke-width: 2; opacity: 0; pointer-events: none; }}
  .direct-label {{ font-size: 11px; font-weight: 700; font-family: var(--sans); }}
  .hit-rect {{ fill: transparent; cursor: crosshair; }}
  .tooltip {{ position: absolute; pointer-events: none; background: var(--ink); color: var(--bg); border-radius: 6px; padding: 8px 11px; font-size: 0.76rem; line-height: 1.6; opacity: 0; transform: translate(-50%, -110%); transition: opacity 0.08s; white-space: nowrap; z-index: 5; box-shadow: var(--shadow); }}
  .tooltip .tt-date {{ font-weight: 700; margin-bottom: 3px; font-variant-numeric: tabular-nums; opacity: 0.85; }}
  .tooltip .tt-row {{ display: flex; justify-content: space-between; gap: 12px; }}
  .tooltip .tt-row .tt-k {{ display: flex; align-items: center; gap: 5px; opacity: 0.85; }}
  .tooltip .tt-row .tt-key-dot {{ width: 8px; height: 8px; border-radius: 2px; display: inline-block; }}
  .tooltip .tt-v {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
  .chart-foot-note {{ font-size: 0.74rem; color: var(--ink-faint); margin-top: 10px; }}
  .chart-narrative {{ font-size: 0.82rem; color: var(--ink); line-height: 1.75; margin: 8px 0 0; }}
  .gauge-row {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 16px 0 26px; }}
  .gauge-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; box-shadow: var(--shadow); }}
  .gauge-top {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }}
  .gauge-name {{ font-family: var(--serif); font-weight: 600; font-size: 0.9rem; }}
  .gauge-value {{ font-variant-numeric: tabular-nums; font-weight: 700; font-size: 1.1rem; }}
  .gauge-track {{ position: relative; height: 10px; background: var(--surface-alt); border: 1px solid var(--border); border-radius: 5px; margin-bottom: 6px; }}
  .gauge-fill {{ position: absolute; top: 0; bottom: 0; left: 0; border-radius: 5px 0 0 5px; }}
  .gauge-fill.expand {{ background: var(--risk-low); }}
  .gauge-fill.contract {{ background: var(--risk-mid); }}
  .gauge-mid-tick {{ position: absolute; top: -3px; bottom: -3px; left: 50%; width: 1px; background: var(--border-strong); }}
  .gauge-note {{ font-size: 0.72rem; color: var(--ink-faint); }}
  .gauge-status {{ font-size: 0.7rem; font-weight: 700; padding: 1px 7px; border-radius: 999px; }}
  .gauge-status.expand {{ background: var(--risk-low-soft); color: var(--risk-low); }}
  .gauge-status.contract {{ background: var(--risk-mid-soft); color: var(--risk-mid); }}
  .gauge-status.neutral {{ background: var(--surface-alt); color: var(--ink-muted); border: 1px solid var(--border); }}
  @media (max-width: 640px) {{ .gauge-row {{ grid-template-columns: 1fr; }} }}
  .market-stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 26px; }}
  .market-stats .ms-cell {{ background: var(--surface); padding: 12px 14px; }}
  .market-stats .ms-label {{ font-size: 0.7rem; color: var(--ink-faint); margin: 0 0 4px; }}
  .market-stats .ms-value {{ font-variant-numeric: tabular-nums; font-weight: 700; font-size: 0.95rem; }}
  @media (max-width: 640px) {{ .market-stats {{ grid-template-columns: repeat(2, 1fr); }} }}
  .base-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
  .base-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 16px 18px; box-shadow: var(--shadow); }}
  .base-card .b-label2 {{ font-size: 0.76rem; color: var(--ink-faint); margin: 0 0 8px; }}
  .base-card .b-value2 {{ font-variant-numeric: tabular-nums; font-size: 1.3rem; font-weight: 700; }}
  .base-card .b-delta {{ display: block; font-size: 0.78rem; margin-top: 6px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .base-card .b-asof {{ display: block; font-size: 0.7rem; color: var(--ink-faint); margin-top: 8px; }}
  @media (max-width: 720px) {{ .base-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
  .pipeline-note {{ background: var(--accent-soft); border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 4px; padding: 14px 18px; font-size: 0.84rem; color: var(--ink); line-height: 1.75; margin-top: 18px; }}
  .reading-panel {{ background: var(--surface-alt); border: 1px solid var(--border); border-radius: 6px; padding: 18px 20px; font-size: 0.86rem; color: var(--ink); line-height: 1.85; margin-top: 4px; }}
  .reading-panel p {{ margin: 0 0 10px; }}
  .reading-panel p:last-child {{ margin-bottom: 0; }}
  .verdict-panel {{ background: var(--surface-alt); border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 4px; padding: 20px 22px; font-size: 0.92rem; color: var(--ink); line-height: 1.85; margin-top: 16px; }}
  .verdict-head {{ display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }}
  .verdict-head .v-title {{ font-family: var(--serif); font-weight: 600; font-size: 1rem; }}
  .tab-bar {{ display: flex; flex-wrap: wrap; gap: 2px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }}
  .tab-btn {{ font-family: var(--sans); font-size: 0.82rem; color: var(--ink-muted); background: transparent; border: none; border-bottom: 2px solid transparent; padding: 8px 14px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }}
  .tab-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }}
  .tab-btn:hover {{ color: var(--accent); }}
  .tab-count {{ font-size: 0.68rem; color: var(--ink-faint); background: var(--surface-alt); border: 1px solid var(--border); border-radius: 999px; padding: 1px 7px; font-variant-numeric: tabular-nums; }}
  .tab-btn.active .tab-count {{ color: var(--accent); }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
  .ind-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }}
  @media (max-width: 720px) {{ .ind-grid {{ grid-template-columns: 1fr; }} }}
  .ind-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; box-shadow: var(--shadow); }}
  .ind-card-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 6px; }}
  .ind-name {{ font-family: var(--serif); font-weight: 600; font-size: 0.9rem; }}
  .ind-badge {{ font-size: 0.68rem; font-weight: 700; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }}
  .ind-badge.easy {{ background: var(--risk-low-soft); color: var(--risk-low); }}
  .ind-badge.manual {{ background: var(--risk-mid-soft); color: var(--risk-mid); }}
  .ind-badge.hard {{ background: var(--risk-high-soft); color: var(--risk-high); }}
  .ind-desc {{ font-size: 0.82rem; color: var(--ink-muted); line-height: 1.7; margin: 0 0 8px; }}
  .ind-meta {{ font-size: 0.74rem; color: var(--ink-faint); margin: 0; }}
  .example-box {{ background: var(--accent-soft); border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 4px; padding: 16px 18px; margin-top: 22px; }}
  .example-title {{ font-family: var(--serif); font-weight: 600; font-size: 0.9rem; margin: 0 0 6px; }}
  .example-lede {{ font-size: 0.8rem; color: var(--ink-muted); margin: 0 0 8px; }}
  .example-list {{ margin: 0; padding-left: 1.2em; font-size: 0.84rem; color: var(--ink); line-height: 1.7; }}
  .sector-trend-card {{ margin-bottom: 16px; }}
  .mini-chart-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 0 0 14px; }}
  @media (max-width: 720px) {{ .mini-chart-grid {{ grid-template-columns: 1fr; }} }}
  .mini-chart-card {{ padding: 14px 16px 12px; margin-bottom: 0; }}
  .mini-chart-card .chart-title {{ font-size: 0.86rem; }}
  .mini-chart-card .chart-foot-note {{ font-size: 0.7rem; }}
  .appendix-note {{ font-size: 0.82rem; color: var(--ink-faint); }}
  footer {{ margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }}
  .sources {{ font-size: 0.76rem; color: var(--ink-faint); line-height: 1.8; }}
  .disclaimer {{ font-size: 0.78rem; color: var(--ink-faint); line-height: 1.7; max-width: 74ch; }}
  .gen-meta {{ font-size: 0.76rem; color: var(--ink-faint); font-variant-numeric: tabular-nums; }}
  :focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
</head>
<body>
<div class="page" lang="ja">

  <header class="doc-header">
    <p class="eyebrow">マクロ経済モニター</p>
    <h1 class="company-name">日本・米国 景気サイクル定点観測</h1>
    <dl class="meta-row">
      <span class="meta-item"><dt>データ最終更新</dt><dd>{fetched_at}</dd></span>
      <span class="meta-item"><dt>ページ生成</dt><dd>{generated_at}</dd></span>
      <span class="meta-item"><dt>対象地域</dt><dd>日本 / 米国</dd></span>
    </dl>
  </header>

  <nav class="toc-nav" aria-label="目次">
    <a href="#snapshot">スナップショット</a>
    <a href="#japan">日本</a>
    <a href="#us">米国</a>
    <a href="#sectors">セクター指標カタログ</a>
    <a href="#appendix">付録</a>
  </nav>

  <section id="snapshot">
    <h2>スナップショット <span class="h2-sub">主要8指標</span></h2>
    <p class="section-lede">個別企業を評価する前に、まず日米の景気循環・市場・金融政策の全体像を単独で確認するためのページです。ここで示す指標そのものへの投資判断（買い時/売り時）は行わず、循環局面の事実確認にとどめています。</p>

    <div class="tile-row">
      <div class="tile"><p class="t-label">日経平均株価</p><p class="t-value">{nikkei_val}</p><span class="t-delta {nikkei_dir}">{nikkei_delta}</span></div>
      <div class="tile"><p class="t-label">景気動向指数 CI一致指数</p><p class="t-value">{ci_val}</p><span class="t-delta">{ci_delta}</span></div>
      <div class="tile"><p class="t-label">景気動向指数 DI一致指数</p><p class="t-value">{di_val}</p><span class="t-status {di_status}">{di_status_label}</span></div>
      <div class="tile"><p class="t-label">USD/JPY</p><p class="t-value">{usd_jpy_val}</p><span class="t-delta {usd_jpy_dir}">{usd_jpy_delta}</span></div>
      <div class="tile"><p class="t-label">S&amp;P500</p><p class="t-value">{sp500_val}</p><span class="t-delta {sp500_dir}">{sp500_delta}</span></div>
      <div class="tile"><p class="t-label">米国 景気活動指数 CFNAI</p><p class="t-value">{cfnai_val}</p><span class="t-status neutral">3か月平均 {cfnai_ma3_str}</span></div>
      <div class="tile"><p class="t-label">FF金利誘導目標</p><p class="t-value">{fed_val}</p><span class="t-delta {fed_dir}">{fed_delta}</span></div>
      <div class="tile"><p class="t-label">米国コアCPI（水準）</p><p class="t-value">{cpi_core_val}</p><span class="t-delta">{cpi_core_delta}</span></div>
    </div>
  </section>

  <section id="japan">
    <div class="region-head first"><span class="region-label">日本</span><span class="region-flag">景気動向指数・株式市場・金融政策</span></div>

    <h3 class="subsection-title">景気動向指数（CI・DI） <span class="h2-sub">内閣府</span></h3>
    <p class="subsection-lede">CI（コンポジット・インデックス）は景気変動の「大きさ・テンポ」を表す量的指標、DI（ディフュージョン・インデックス）は構成系列のうち何%が改善しているかを示す「波及度合い」の指標です。DIは50が拡大・後退の分岐点とされます。</p>
    <div class="chart-card">
      <div class="chart-head"><span class="chart-title">CI指数の推移（2020年=100）</span><div class="chart-legend" id="ci-legend"></div></div>
      <div class="chart-wrap"><svg id="ci-chart" viewBox="0 0 760 260" preserveAspectRatio="xMidYMid meet"></svg></div>
      <p class="chart-foot-note">出典: 内閣府「景気動向指数」長期系列（e-Stat API, statsDataId 0003446461）。</p>
    </div>
    <div class="gauge-row" id="di-gauges"></div>

    <h3 class="subsection-title">株式市場 <span class="h2-sub">日経平均株価</span></h3>
    <div class="chart-card">
      <div class="chart-head"><span class="chart-title">日経平均株価 週次推移</span></div>
      <div class="chart-wrap"><svg id="nikkei-chart" viewBox="0 0 760 240" preserveAspectRatio="xMidYMid meet"></svg></div>
      <p class="chart-foot-note">出典: FRED（series: NIKKEI225）。</p>
    </div>
    <div class="market-stats">
      <div class="ms-cell"><p class="ms-label">取得期間内変化（起点 {nikkei_high_date}〜）</p><p class="ms-value">{nikkei_change_pct}</p></div>
      <div class="ms-cell"><p class="ms-label">期間内高値（{nikkei_high_date}）</p><p class="ms-value">{nikkei_high}</p></div>
      <div class="ms-cell"><p class="ms-label">期間内安値（{nikkei_low_date}）</p><p class="ms-value">{nikkei_low}</p></div>
      <div class="ms-cell"><p class="ms-label">10年国債利回り（{jp_10y_date}）</p><p class="ms-value">{jp_10y_val}</p></div>
    </div>

    <h3 class="subsection-title">読み方 <span class="h2-sub">LLM生成・データからの機械的な読み取り・公式見解ではありません</span></h3>
    <div class="reading-panel">
{jp_reading_html}
    </div>
{jp_verdict_html}

    <h3 class="subsection-title">基礎指標 <span class="h2-sub">各社評価レポートのマクロ前提として使用</span></h3>
    <div class="base-grid">
      <div class="base-card"><p class="b-label2">USD/JPY 為替レート</p><p class="b-value2">{usd_jpy_val}</p><span class="b-delta {usd_jpy_dir}">{usd_jpy_delta}</span><span class="b-asof">{usd_jpy_asof} ／ 出典: FRED</span></div>
      <div class="base-card"><p class="b-label2">政策金利（無担保コール翌日物近似）</p><p class="b-value2">{policy_rate_val}</p><span class="b-delta">{policy_rate_delta}</span><span class="b-asof">{policy_rate_asof} ／ 出典: FRED</span></div>
      <div class="base-card"><p class="b-label2">名目GDP（水準）</p><p class="b-value2">{nominal_gdp_val}</p><span class="b-delta">{nominal_gdp_delta}</span><span class="b-asof">{nominal_gdp_asof} ／ 出典: FRED</span></div>
      <div class="base-card"><p class="b-label2">コアCPI（生鮮食品を除く総合）</p><p class="b-value2">{cpi_core_full_val}</p><span class="b-delta">{cpi_core_full_delta}</span><span class="b-asof">{cpi_core_asof} ／ 出典: e-Stat（総務省）</span></div>
    </div>
    <p class="pipeline-note">この4系列は <code>data/macro/overall.json</code> に時系列で保存され、個別企業の評価レポートは決算期時点の値と本ページの最新値を併記して引用します。</p>
  </section>

  <section id="us">
    <div class="region-head"><span class="region-label">米国</span><span class="region-flag">景気活動指数・株式市場・金融政策</span></div>

    <h3 class="subsection-title">景気活動指数（CFNAI） <span class="h2-sub">シカゴ連銀</span></h3>
    <p class="subsection-lede">日本のCI/DIに相当する公式の月次指標は米国には存在しないため、シカゴ連銀が毎月公表する National Activity Index（CFNAI）を採用しています。0＝トレンド並みの成長、CFNAI-MA3が-0.70を下回ると景気後退リスク、+0.70を上回るとインフレ加速リスクの高まりが目安とされます。</p>
    <div class="chart-card">
      <div class="chart-head"><span class="chart-title">CFNAIの推移</span><div class="chart-legend" id="cfnai-legend"></div></div>
      <div class="chart-wrap"><svg id="cfnai-chart" viewBox="0 0 760 240" preserveAspectRatio="xMidYMid meet"></svg></div>
      <p class="chart-foot-note">出典: FRED（series: CFNAI, CFNAIMA3、原資料はシカゴ連銀）。</p>
    </div>

    <h3 class="subsection-title">株式市場 <span class="h2-sub">S&amp;P500</span></h3>
    <div class="chart-card">
      <div class="chart-head"><span class="chart-title">S&amp;P500 週次推移</span></div>
      <div class="chart-wrap"><svg id="sp500-chart" viewBox="0 0 760 220" preserveAspectRatio="xMidYMid meet"></svg></div>
      <p class="chart-foot-note">出典: FRED（series: SP500）。</p>
    </div>
    <div class="market-stats">
      <div class="ms-cell"><p class="ms-label">取得期間内変化</p><p class="ms-value">{sp500_change_pct}</p></div>
      <div class="ms-cell"><p class="ms-label">期間内高値（{sp500_high_date}）</p><p class="ms-value">{sp500_high}</p></div>
      <div class="ms-cell"><p class="ms-label">期間内安値（{sp500_low_date}）</p><p class="ms-value">{sp500_low}</p></div>
      <div class="ms-cell"><p class="ms-label">10年国債利回り（{us_10y_date}）</p><p class="ms-value">{us_10y_val}</p></div>
    </div>

    <h3 class="subsection-title">金利・物価・成長 <span class="h2-sub">FRED</span></h3>
    <div class="base-grid">
      <div class="base-card"><p class="b-label2">FF金利誘導目標</p><p class="b-value2">{fed_val}</p><span class="b-delta {fed_dir}">{fed_delta}</span><span class="b-asof">{fed_asof} ／ 出典: FRED（FEDFUNDS）</span></div>
      <div class="base-card"><p class="b-label2">CPI（総合、水準）</p><p class="b-value2">{us_cpi_val}</p><span class="b-delta">{us_cpi_delta}</span><span class="b-asof">出典: FRED（CPIAUCSL）</span></div>
      <div class="base-card"><p class="b-label2">コアCPI（食品・エネルギー除く、水準）</p><p class="b-value2">{cpi_core_val}</p><span class="b-delta">{cpi_core_delta}</span><span class="b-asof">{us_cpi_core_asof} ／ 出典: FRED（CPILFESL）</span></div>
      <div class="base-card"><p class="b-label2">実質GDP成長率（前期比年率）</p><p class="b-value2">{us_gdp_growth_val}</p><span class="b-asof">{us_gdp_growth_asof} ／ 出典: FRED（BEA）</span></div>
    </div>

    <h3 class="subsection-title">読み方 <span class="h2-sub">LLM生成・データからの機械的な読み取り・公式見解ではありません</span></h3>
    <div class="reading-panel">
{us_reading_html}
    </div>
{us_verdict_html}
  </section>

  <section id="sectors">
    <h2>セクター指標カタログ <span class="h2-sub">38指標・grill-meで調査・カタログ化</span></h2>
    <p class="section-lede">CI/DI・CFNAI等の全体マクロ指標に加えて、個別株の評価が業種に応じて参照するセクター指標のカタログです。各タブの冒頭には、その分野を代表する指標の実際の時系列推移と、その動きが何を意味するかの短い解説（LLM生成）を掲載し、続けて実データが確認できた指標をミニチャートで並べています（全{sector_total_count}指標のうち{sector_charted_count}指標が時系列グラフ化済み、残りはカード情報のみ）。個別株ページ側では、企業の業種タームからこの中の数指標がLLMにより自動選択されます（下記に実例）。</p>

    <div class="tabs-block">
      <div class="tab-bar">
      {catalog_tab_buttons}
      </div>
      <div class="tab-panels">
{catalog_tab_panels}
      </div>
    </div>
{example_html}
  </section>

  <section id="appendix" style="margin-bottom:0;">
    <h2>付録</h2>
    <p class="appendix-note">本ページの数値は個別企業の投資適格性評価レポートが参照する共通のマクロ前提です。「読み方」「総合見立て」は macro/narrative.py が LLM（Gemini）で自動生成しています。</p>
  </section>

  <footer>
    <p class="gen-meta">ページ生成日時: {generated_at} ／ データ最終更新: {fetched_at}</p>
    <p class="sources">
      【日本】景気動向指数: 内閣府「景気動向指数」長期系列（e-Stat API, statsDataId 0003446461） ／
      日経平均株価・10年国債利回り: FRED（series NIKKEI225, IRLTLT01JPM156N） ／
      USD/JPY・政策金利・名目GDP: FRED ／ コアCPI: e-Stat（総務省統計局）。
      【米国】景気活動指数: FRED（series CFNAI, CFNAIMA3、原資料はシカゴ連銀） ／
      S&amp;P500・10年国債利回り: FRED（series SP500, DGS10） ／
      FF金利: FRED（FEDFUNDS） ／ CPI・コアCPI: FRED（CPIAUCSL, CPILFESL） ／
      実質GDP成長率: FRED（series A191RL1Q225SBEA、原資料は米商務省BEA）。
    </p>
    <p class="disclaimer">本ページは公開統計・市場データの集計とその機械的な読み取りであり、投資助言ではありません。「読み方」「総合見立て」の記述は内閣府・FRB等による公式の基調判断を示すものではなく、特定銘柄の売買判断を示すものでもありません。金融商品取引法上の投資判断は利用者自身の責任で行ってください。</p>
  </footer>

</div>

<script>
(function () {{
  function cssVar(name) {{ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }}
  const svgNS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs) {{
    const e = document.createElementNS(svgNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }}
  function makeTooltip(container) {{
    const tip = document.createElement('div');
    tip.className = 'tooltip';
    container.appendChild(tip);
    return tip;
  }}
  function renderLineChart(svgEl, series, opts) {{
    const W = opts.width, H = opts.height;
    const padL = opts.padL || 44, padR = opts.padR || 64, padT = opts.padT || 16, padB = opts.padB || 28;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const n = series[0].points.length;
    let allVals = [];
    series.forEach(s => s.points.forEach(p => allVals.push(p.y)));
    let vMin = Math.min(...allVals), vMax = Math.max(...allVals);
    const pad = (vMax - vMin) * 0.12 || 1;
    vMin -= pad; vMax += pad;
    const xAt = i => padL + (n === 1 ? 0 : (plotW * i) / (n - 1));
    const yAt = v => padT + plotH - ((v - vMin) / (vMax - vMin)) * plotH;
    svgEl.innerHTML = '';
    svgEl.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
    const gridSteps = 4;
    for (let i = 0; i <= gridSteps; i++) {{
      const v = vMin + ((vMax - vMin) * i) / gridSteps;
      const y = yAt(v);
      svgEl.appendChild(el('line', {{ x1: padL, x2: W - padR, y1: y, y2: y, class: 'gridline' }}));
      const t = el('text', {{ x: padL - 8, y: y + 3, class: 'axis-label', 'text-anchor': 'end' }});
      t.textContent = opts.yfmt ? opts.yfmt(v) : Math.round(v);
      svgEl.appendChild(t);
    }}
    const labelEvery = opts.labelEvery || 1;
    for (let i = 0; i < n; i += labelEvery) {{
      const t = el('text', {{ x: xAt(i), y: H - 6, class: 'axis-label', 'text-anchor': 'middle' }});
      t.textContent = series[0].points[i].label;
      svgEl.appendChild(t);
    }}
    series.forEach(s => {{
      const d = s.points.map((p, i) => `${{i === 0 ? 'M' : 'L'}} ${{xAt(i)}} ${{yAt(p.y)}}`).join(' ');
      svgEl.appendChild(el('path', {{ d, class: 'series-line', stroke: s.color }}));
      const last = s.points[n - 1];
      svgEl.appendChild(el('circle', {{ cx: xAt(n - 1), cy: yAt(last.y), class: 'end-dot', fill: s.color }}));
      const lbl = el('text', {{ x: xAt(n - 1) + 8, y: yAt(last.y) + 4, class: 'direct-label', fill: s.color }});
      lbl.textContent = opts.endLabel ? opts.endLabel(s, last) : last.y;
      svgEl.appendChild(lbl);
    }});
    const crosshair = el('line', {{ x1: 0, x2: 0, y1: padT, y2: padT + plotH, class: 'crosshair-line' }});
    svgEl.appendChild(crosshair);
    const hoverDots = series.map(s => {{
      const c = el('circle', {{ class: 'hover-dot', fill: s.color }});
      svgEl.appendChild(c);
      return c;
    }});
    const container = svgEl.closest('.chart-wrap');
    const tip = makeTooltip(container);
    const hit = el('rect', {{ x: padL, y: padT, width: plotW, height: plotH, class: 'hit-rect' }});
    svgEl.appendChild(hit);
    function showAt(i) {{
      const x = xAt(i);
      crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x);
      crosshair.setAttribute('opacity', 1);
      let rows = '';
      series.forEach((s, si) => {{
        const p = s.points[i];
        hoverDots[si].setAttribute('cx', x);
        hoverDots[si].setAttribute('cy', yAt(p.y));
        hoverDots[si].setAttribute('opacity', 1);
        rows += `<div class="tt-row"><span class="tt-k"><span class="tt-key-dot" style="background:${{s.color}}"></span>${{s.name}}</span><span class="tt-v">${{opts.tipfmt ? opts.tipfmt(p.y) : p.y}}</span></div>`;
      }});
      tip.innerHTML = `<div class="tt-date">${{series[0].points[i].label}}</div>${{rows}}`;
      tip.style.opacity = 1;
      const rect = container.getBoundingClientRect();
      const svgRect = svgEl.getBoundingClientRect();
      const scale = svgRect.width / W;
      tip.style.left = (svgRect.left - rect.left + x * scale) + 'px';
      tip.style.top = (svgRect.top - rect.top + yAt(Math.min(...series.map(s => s.points[i].y))) * scale) + 'px';
    }}
    function hide() {{
      crosshair.setAttribute('opacity', 0);
      hoverDots.forEach(d => d.setAttribute('opacity', 0));
      tip.style.opacity = 0;
    }}
    hit.addEventListener('pointermove', ev => {{
      const svgRect = svgEl.getBoundingClientRect();
      const scale = W / svgRect.width;
      const localX = (ev.clientX - svgRect.left) * scale;
      let i = Math.round(((localX - padL) / plotW) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      showAt(i);
    }});
    hit.addEventListener('pointerleave', hide);
  }}

  const ciData = {ci_series_json};
  const ciColors = {{ "一致指数": cssVar('--series-1'), "先行指数": cssVar('--series-2'), "遅行指数": cssVar('--series-3') }};
  const ciSeries = Object.keys(ciData).map(name => ({{
    name, color: ciColors[name],
    points: ciData[name].map(d => ({{ y: d.v, label: d.m }}))
  }}));
  renderLineChart(document.getElementById('ci-chart'), ciSeries, {{
    width: 760, height: 260, padR: 60, labelEvery: 3,
    yfmt: v => v.toFixed(0), tipfmt: v => v.toFixed(1),
    endLabel: (s, last) => `${{s.name}} ${{last.y.toFixed(1)}}`,
  }});
  const ciLegend = document.getElementById('ci-legend');
  ciSeries.forEach(s => {{
    const item = document.createElement('span'); item.className = 'lg-item';
    const key = document.createElement('span'); key.className = 'lg-key'; key.style.background = s.color;
    const label = document.createElement('span'); label.textContent = s.name;
    item.appendChild(key); item.appendChild(label); ciLegend.appendChild(item);
  }});

  const diData = {di_gauge_data};
  const diOrder = ["先行指数", "一致指数", "遅行指数"];
  const gaugeRow = document.getElementById('di-gauges');
  diOrder.forEach(name => {{
    const d = diData[name];
    if (!d) return;
    const v = d.v;
    const expand = v >= 50;
    const card = document.createElement('div');
    card.className = 'gauge-card';
    card.innerHTML = `
      <div class="gauge-top"><span class="gauge-name">DI ${{name}}</span><span class="gauge-value">${{v.toFixed(1)}}</span></div>
      <div class="gauge-track"><div class="gauge-fill ${{expand ? 'expand' : 'contract'}}" style="width:${{v}}%"></div><div class="gauge-mid-tick"></div></div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span class="gauge-note">${{d.m}}分</span>
        <span class="gauge-status ${{expand ? 'expand' : 'contract'}}">${{expand ? '拡大目安（>50）' : '後退目安（<50）'}}</span>
      </div>`;
    gaugeRow.appendChild(card);
  }});

  const nikkeiSample = {nikkei_json};
  const nikkeiSeries = [{{ name: '日経平均株価', color: cssVar('--accent'), points: nikkeiSample.map(d => ({{ y: d.value, label: d.date.slice(2).replace('-', '/') }})) }}];
  renderLineChart(document.getElementById('nikkei-chart'), nikkeiSeries, {{
    width: 760, height: 240, padR: 70, labelEvery: 8,
    yfmt: v => Math.round(v).toLocaleString(), tipfmt: v => Math.round(v).toLocaleString() + '円',
    endLabel: (s, last) => Math.round(last.y).toLocaleString() + '円',
  }});

  const cfnaiRaw = {cfnai_raw_json};
  const cfnaiMa3 = {cfnai_ma3_json};
  const cfnaiSeries = [
    {{ name: 'CFNAI（月次）', color: cssVar('--series-2'), points: cfnaiRaw.map(d => ({{ y: d.value, label: d.date }})) }},
    {{ name: 'CFNAI-MA3（3か月平均）', color: cssVar('--series-1'), points: cfnaiMa3.map(d => ({{ y: d.value, label: d.date }})) }},
  ];
  renderLineChart(document.getElementById('cfnai-chart'), cfnaiSeries, {{
    width: 760, height: 240, padR: 60, labelEvery: 3,
    yfmt: v => v.toFixed(1), tipfmt: v => v.toFixed(2), endLabel: (s, last) => last.y.toFixed(2),
  }});
  const cfnaiLegend = document.getElementById('cfnai-legend');
  cfnaiSeries.forEach(s => {{
    const item = document.createElement('span'); item.className = 'lg-item';
    const key = document.createElement('span'); key.className = 'lg-key'; key.style.background = s.color;
    const label = document.createElement('span'); label.textContent = s.name;
    item.appendChild(key); item.appendChild(label); cfnaiLegend.appendChild(item);
  }});

  const sp500Sample = {sp500_json};
  const sp500Series = [{{ name: 'S&P500', color: cssVar('--accent'), points: sp500Sample.map(d => ({{ y: d.value, label: d.date.slice(2).replace('-', '/') }})) }}];
  renderLineChart(document.getElementById('sp500-chart'), sp500Series, {{
    width: 760, height: 220, padR: 60, labelEvery: 8,
    yfmt: v => Math.round(v).toLocaleString(), tipfmt: v => Math.round(v).toLocaleString(),
    endLabel: (s, last) => Math.round(last.y).toLocaleString(),
  }});

  {sector_chart_js}

  document.querySelectorAll('.tabs-block').forEach(block => {{
    block.addEventListener('click', ev => {{
      const btn = ev.target.closest('.tab-btn');
      if (!btn) return;
      const id = btn.dataset.tab;
      block.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
      block.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === id));
    }});
  }});
}})();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="マクロ経済モニターページ（HTML）を生成")
    parser.add_argument("--model", default=None, help="読み方・総合見立て生成に使うモデル")
    parser.add_argument("--out", default=None, help="出力先パス（既定: data/macro/report.html）")
    args = parser.parse_args()

    bundle = load_macro_bundle(model=args.model)
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(render_html(bundle), encoding="utf-8")
    else:
        out_path = save_report(bundle)
    print(f"生成: {out_path}")


if __name__ == "__main__":
    main()
