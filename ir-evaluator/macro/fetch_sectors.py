"""セクター指標カタログ（indicator_catalog.json）5タブそれぞれの代表指標を取得し、
data/macro/sectors.json へ保存する。

全38指標を網羅的に取得するのではなく、各タブ（消費・小売／企業活動・景況／
貿易・生産／労働・物価／不動産・金融）を代表する1系列を選び、実際にAPIで
取得・検証済みのものだけをコード化している（未検証のパラメータで捏造しない）。

- 消費・小売: 景気ウォッチャー調査 現状判断DI（内閣府, e-Stat）
- 企業活動・景況: 法人企業統計調査 売上高経常利益率（財務省, e-Stat, 四半期）
- 貿易・生産: 機械受注統計 民需（船舶・電力除く、季調系列）（内閣府, e-Stat）
- 労働・物価: 労働力調査 完全失業率（総務省, e-Stat） ※コアCPIは overall.json に既存
- 不動産・金融: マネーストックM2（日本銀行, 新API）

使い方:
    uv run python -m macro.fetch_sectors
"""

from __future__ import annotations

from .boj import BojClient, boj_month_to_iso
from .estat import EstatClient, estat_month_to_iso
from .fred import Observation
from .store import SeriesData

# --------------------------------------------------------------------------
# 消費・小売: 景気ウォッチャー調査 現状判断DI（全国・全分野）
# --------------------------------------------------------------------------
_WATCHERS_STATS_DATA_ID = "0003348427"


def fetch_economy_watchers_di(client: EstatClient | None = None, start_year: str = "2024") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _WATCHERS_STATS_DATA_ID,
        cd_tab="140",  # DI
        cd_cat01="100",  # 合計
        cd_area="00000",  # 全国
        cd_time_from=f"{start_year}000000",
        extra={"cdCat02": "100", "cdCat03": "100"},  # 現状判断（方向性）／分野: 合計
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]
    observations: list[Observation] = []
    for v in values:
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
    return SeriesData(
        key="economy_watchers_di",
        label="景気ウォッチャー調査 現状判断DI（全国・全分野）",
        source=f"e-Stat:{_WATCHERS_STATS_DATA_ID}/tab=140,cat01=100,cat02=100,cat03=100",
        frequency="m",
        unit="DI(0-100)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 企業活動・景況: 法人企業統計調査 売上高経常利益率（全産業except金融保険業、全規模）
# --------------------------------------------------------------------------
_CORP_STATS_DATA_ID = "0003060191"


def _quarter_code_to_iso(code: str) -> str | None:
    """e-Statの四半期時間コード（例: '20261' = 2026年第1四半期）をISO日付（四半期初）へ。"""
    if len(code) != 5 or not code.isdigit():
        return None
    year, q = code[:4], code[4]
    month = {"1": "01", "2": "04", "3": "07", "4": "10"}.get(q)
    if month is None:
        return None
    return f"{year}-{month}-01"


def fetch_corporate_profit_margin(client: EstatClient | None = None, start: str = "20241") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _CORP_STATS_DATA_ID,
        cd_cat01="154",  # 売上高経常利益率(当期末)
        cd_time_from=start,
        extra={"cdCat02": "104", "cdCat03": "26"},  # 全産業(除く金融保険業)／全規模
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]
    observations: list[Observation] = []
    for v in values:
        iso = _quarter_code_to_iso(str(v.get("@time")))
        if iso is None:
            continue
        raw = v.get("$")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        observations.append(Observation(date=iso, value=value))
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="corporate_profit_margin",
        label="売上高経常利益率（全産業except金融保険業・全規模）",
        source=f"e-Stat:{_CORP_STATS_DATA_ID}/cat01=154,cat02=104,cat03=26",
        frequency="q",
        unit="%",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 貿易・生産: 機械受注統計 民需（船舶・電力を除く、季調系列）
# --------------------------------------------------------------------------
_MACHINERY_STATS_DATA_ID = "0003355222"


def fetch_core_machinery_orders(client: EstatClient | None = None, start_year: str = "2024") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _MACHINERY_STATS_DATA_ID,
        cd_tab="100",  # 金額
        cd_cat01="160",  # 民間需要(船舶・電力を除く) = いわゆる「コア機械受注」
        cd_time_from=f"{start_year}000000",
        extra={"cdCat02": "100"},  # 季調系列
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]
    observations: list[Observation] = []
    for v in values:
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
    return SeriesData(
        key="core_machinery_orders",
        label="コア機械受注（民需、船舶・電力除く、季調系列）",
        source=f"e-Stat:{_MACHINERY_STATS_DATA_ID}/tab=100,cat01=160,cat02=100",
        frequency="m",
        unit="百万円",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 労働・物価: 労働力調査 完全失業率
# --------------------------------------------------------------------------
_LABOUR_STATS_DATA_ID = "0003005865"


def fetch_unemployment_rate(client: EstatClient | None = None, start_year: str = "2024") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _LABOUR_STATS_DATA_ID,
        cd_tab="02",  # 率
        cd_cat01="000",  # 全産業
        cd_area="00000",  # 全国
        cd_time_from=f"{start_year}000000",
        extra={"cdCat02": "08", "cdCat03": "0"},  # 完全失業者／総数
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]
    observations: list[Observation] = []
    for v in values:
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
    return SeriesData(
        key="unemployment_rate",
        label="完全失業率（全国・総数）",
        source=f"e-Stat:{_LABOUR_STATS_DATA_ID}/tab=02,cat01=000,cat02=08,cat03=0",
        frequency="m",
        unit="%",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 不動産・金融: マネーストックM2（平残）
# --------------------------------------------------------------------------
_MONEY_STOCK_DB = "MD02"
_MONEY_STOCK_M2_CODE = "MAM1NAM2M2MO"


def fetch_money_stock_m2(client: BojClient | None = None, start: str = "202401") -> SeriesData:
    # end_date は指定しない（省略すると収録終了期まで返る。未来月のnull埋めを避ける）。
    client = client or BojClient()
    obs = client.get_data_code(_MONEY_STOCK_DB, _MONEY_STOCK_M2_CODE, start_date=start)
    observations = [
        Observation(date=boj_month_to_iso(o.period), value=(o.value / 10.0 if o.value is not None else None))
        for o in obs
        if o.value is not None
    ]
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="money_stock_m2",
        label="マネーストックM2（平残）",
        source=f"BOJ:{_MONEY_STOCK_DB}/{_MONEY_STOCK_M2_CODE}",
        frequency="m",
        unit="10億円",
        observations=observations,
    )


def fetch_all_sectors() -> list[SeriesData]:
    return [
        fetch_economy_watchers_di(),
        fetch_corporate_profit_margin(),
        fetch_core_machinery_orders(),
        fetch_unemployment_rate(),
        fetch_money_stock_m2(),
    ]


def main() -> None:
    from .store import save_bundle

    series = fetch_all_sectors()
    for s in series:
        latest = s.latest()
        latest_str = f"{latest.date} = {latest.value}" if latest else "（有効値なし）"
        print(f"[OK]   {s.key:24s} {s.label} … {len(s.observations)}点 / 最新 {latest_str}")

    out_path = save_bundle(series, "sectors.json")
    print(f"\n保存: {out_path}（{len(series)}指標）")


if __name__ == "__main__":
    main()
