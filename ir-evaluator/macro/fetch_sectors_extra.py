"""data/macro/indicator_catalog.json の残り32指標（Tier1: e-Stat 12指標／Tier2: 日銀API 6指標）
のうち、2026-09-02時点で実データ取得を検証できた11指標だけを実装する。

fetch_sectors.py は5タブそれぞれの代表1系列に用途を絞っている（同ファイルのdocstring参照）ため、
それとは別枠として本ファイルへ追加する。macro/refresh.py の週次パイプラインに組み込み済み
（sectors ステップの直後）で、macro/report.py の各タブにミニチャートとして表示される
（グラフ化・レイアウト設計は grill-me で確定。TODO.md参照）。

各関数は実際にAPIへ問い合わせて値が返ることを確認済みのstatsDataId／BOJコードのみを使用する
（未検証のIDやコードを捏造しない、というfetch_cpi.py・fetch_sectors.pyの方針を踏襲）。

見送った7指標（TODO.md「今後取得すべきセクター指標一覧」に理由を記載）:
  Tier1: commerce_dynamics, trade_statistics, tertiary_industry_index, construction_starts,
         service_industry_dynamics, monthly_labour_survey — いずれも「毎回発番される最新の
         月次表をgetStatsListで都度検索する」実装が必要、または該当テーブルをタイトル検索で
         特定できなかった（monthly_labour_surveyは候補テーブルが軒並み2008〜2015年で更新停止の
         旧基準アーカイブと判明）。
  Tier2: consumption_activity_index — 日銀時系列統計データ検索サイトの統計一覧（検索サイト格納
         統計一覧PDF）に掲載が見当たらず、公式に確実な経路はcai.xlsxの直接DLのみ（Excel解析
         ライブラリの追加が必要になるため見送り）。

使い方:
    uv run python -m macro.fetch_sectors_extra
"""

from __future__ import annotations

from .boj import BojClient, boj_month_to_iso
from .estat import EstatClient, estat_month_to_iso
from .fetch_cpi import CPI_CAT01_CORE, CPI_STATS_DATA_ID, CPI_TAB_INDEX
from .fred import Observation
from .store import SeriesData

# ==========================================================================
# Tier 1 (e-Stat)
# ==========================================================================

# --------------------------------------------------------------------------
# 労働・物価: 消費者物価指数（東京都区部、生鮮食品を除く総合）
# 全国コアCPIと同じ統計表、area を東京都区部に変えるだけ（fetch_cpi.py の CPI_STATS_DATA_ID を流用）。
# --------------------------------------------------------------------------
_TOKYO_CPI_AREA = "13A01"


def fetch_tokyo_cpi(client: EstatClient | None = None, start_year: str = "2015") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        CPI_STATS_DATA_ID,
        cd_cat01=CPI_CAT01_CORE,
        cd_area=_TOKYO_CPI_AREA,
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
            value = None
        observations.append(Observation(date=iso, value=value))

    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="tokyo_cpi",
        label="コアCPI（生鮮食品を除く総合、東京都区部、2020年基準）",
        source=f"e-Stat:{CPI_STATS_DATA_ID}/cat01={CPI_CAT01_CORE},area={_TOKYO_CPI_AREA}",
        frequency="m",
        unit="指数(2020=100)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 労働・物価: GDPデフレーター（国内総生産(支出側)、四半期季節調整系列、2020暦年基準）
# --------------------------------------------------------------------------
_GDP_DEFLATOR_STATS_DATA_ID = "0003109787"


def fetch_gdp_deflator(client: EstatClient | None = None, start_year: str = "2015") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _GDP_DEFLATOR_STATS_DATA_ID,
        cd_tab="19",  # 指数
        cd_cat01="11",  # 国内総生産(支出側)
        cd_time_from=f"{start_year}000000",
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations = []
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
        key="gdp_deflator",
        label="GDPデフレーター（国内総生産(支出側)、季節調整系列、2020暦年基準）",
        source=f"e-Stat:{_GDP_DEFLATOR_STATS_DATA_ID}/tab=19,cat01=11",
        frequency="q",
        unit="指数(2020暦年=100)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 消費・小売: 家計調査 消費支出（二人以上の世帯）
# --------------------------------------------------------------------------
_HOUSEHOLD_SURVEY_STATS_DATA_ID = "0002070001"


def fetch_household_survey(client: EstatClient | None = None, start_year: str = "2015") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _HOUSEHOLD_SURVEY_STATS_DATA_ID,
        cd_tab="01",  # 金額
        cd_cat01="059",  # 消費支出
        cd_area="00000",  # 全国
        cd_time_from=f"{start_year}000000",
        extra={"cdCat02": "03"},  # 二人以上の世帯（2000年〜）
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations = []
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
        key="household_survey",
        label="家計調査 消費支出（二人以上の世帯）",
        source=f"e-Stat:{_HOUSEHOLD_SURVEY_STATS_DATA_ID}/tab=01,cat01=059,cat02=03",
        frequency="m",
        unit="円",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 消費・小売: 消費者態度指数（景気動向指数 個別系列の1つ、内閣府）
# --------------------------------------------------------------------------
_CONSUMER_CONFIDENCE_STATS_DATA_ID = "0003446462"


def fetch_consumer_confidence_survey(client: EstatClient | None = None, start_year: str = "2015") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _CONSUMER_CONFIDENCE_STATS_DATA_ID,
        cd_tab="200",  # 系列の数値
        cd_cat01="1060",  # (先行)_L6消費者態度指数
        cd_time_from=f"{start_year}000000",
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations = []
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
        key="consumer_confidence_survey",
        label="消費者態度指数（景気動向指数 個別系列）",
        source=f"e-Stat:{_CONSUMER_CONFIDENCE_STATS_DATA_ID}/tab=200,cat01=1060",
        frequency="m",
        unit="DI(0-100)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 企業活動・景況: 法人企業景気予測調査 国内景況判断BSI（大企業・全産業・当期）
# --------------------------------------------------------------------------
_CORPORATE_OUTLOOK_STATS_DATA_ID = "0003326000"


def fetch_corporate_outlook_survey(client: EstatClient | None = None, start_year: str = "2015") -> SeriesData:
    client = client or EstatClient()
    stat = client.get_stats_data(
        _CORPORATE_OUTLOOK_STATS_DATA_ID,
        cd_cat01="10",  # 当期
        cd_time_from=f"{start_year}000000",
        extra={
            "cdCat02": "20",  # 大企業
            "cdCat03": "50",  # BSI
            "cdCat04": "10",  # 全産業
            "cdCat05": "20",  # 国内
        },
    )
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations = []
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
        key="corporate_outlook_survey",
        label="法人企業景気予測調査 国内景況判断BSI（大企業・全産業・当期）",
        source=f"e-Stat:{_CORPORATE_OUTLOOK_STATS_DATA_ID}/cat01=10,cat02=20,cat03=50,cat04=10,cat05=20",
        frequency="q",
        unit="BSI(%pt)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 貿易・生産: 鉱工業指数 付加価値額生産（鉄鋼業、季節調整済、2020年=100）
#
# この統計表は他の e-Stat 表と異なり、@time の値がそのまま年月に変換できるコードではなく、
# メタ情報（CLASS_INF の time 軸）内の @name（例: "202603"）を都度引く必要がある
# （@code は "0500100" のような表内部の連番で、月の算術的な組み立てができない）。
# --------------------------------------------------------------------------
_STEEL_INDEX_STATS_DATA_ID = "0004052177"
_STEEL_INDEX_CAT01 = "0004000"  # 鉄鋼業


def fetch_steel_industry_index(client: EstatClient | None = None) -> SeriesData:
    client = client or EstatClient()
    meta = client.get_meta_info(_STEEL_INDEX_STATS_DATA_ID)
    time_axis = next(c for c in meta["CLASS_INF"]["CLASS_OBJ"] if c.get("@id") == "time")["CLASS"]
    code_to_yyyymm = {c["@code"]: c["@name"] for c in time_axis if c["@name"].isdigit() and len(c["@name"]) == 6}

    stat = client.get_stats_data(_STEEL_INDEX_STATS_DATA_ID, cd_cat01=_STEEL_INDEX_CAT01)
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    observations = []
    for v in values:
        yyyymm = code_to_yyyymm.get(v.get("@time"))
        if yyyymm is None:
            continue
        raw = v.get("$")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        observations.append(Observation(date=f"{yyyymm[:4]}-{yyyymm[4:6]}-01", value=value))

    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="steel_industry_index",
        label="鉱工業指数 付加価値額生産（鉄鋼業、季節調整済、2020年=100）",
        source=f"e-Stat:{_STEEL_INDEX_STATS_DATA_ID}/cat01={_STEEL_INDEX_CAT01}",
        frequency="m",
        unit="指数(2020=100)",
        observations=observations,
    )


# ==========================================================================
# Tier 2 (BOJ 時系列統計データ検索サイト API)
# ==========================================================================


def _boj_quarter_to_iso(period: str) -> str:
    """日銀短観の四半期期間コード（YYYY + 四半期通番01-04。例: '202602'=2026年Q2）をISO日付へ。

    boj.py の boj_month_to_iso() は月次専用（下2桁=月）なので流用できない
    （四半期の通番をそのまま月として解釈すると Q2 以降がずれる）。
    """
    year, q = period[:4], period[4:6]
    month = (int(q) - 1) * 3 + 1
    return f"{year}-{month:02d}-01"


# --------------------------------------------------------------------------
# 企業活動・景況: 日銀短観 業況判断DI（大企業・製造業・実績）
# --------------------------------------------------------------------------
_TANKAN_DB = "CO"
_TANKAN_CODE = "TK99F1000601GCQ01000"  # D.I./業況/大企業/製造業/実績


def fetch_tankan(client: BojClient | None = None, start: str = "201501") -> SeriesData:
    """start は BOJ 側の四半期通番形式（YYYY+01-04。例: '201501' = 2015年Q1）。"""
    client = client or BojClient()
    obs = client.get_data_code(_TANKAN_DB, _TANKAN_CODE, start_date=start)
    observations = [
        Observation(date=_boj_quarter_to_iso(o.period), value=o.value) for o in obs if o.value is not None
    ]
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="tankan",
        label="日銀短観 業況判断DI（大企業・製造業・実績）",
        source=f"BOJ:{_TANKAN_DB}/{_TANKAN_CODE}",
        frequency="q",
        unit="%ポイント",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 不動産・金融: マネタリーベース平均残高
# --------------------------------------------------------------------------
_MONETARY_BASE_DB = "MD01"
_MONETARY_BASE_CODE = "MABS1AN11"


def fetch_monetary_base(client: BojClient | None = None, start: str = "201501") -> SeriesData:
    client = client or BojClient()
    obs = client.get_data_code(_MONETARY_BASE_DB, _MONETARY_BASE_CODE, start_date=start)
    observations = [Observation(date=boj_month_to_iso(o.period), value=o.value) for o in obs if o.value is not None]
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="monetary_base",
        label="マネタリーベース平均残高",
        source=f"BOJ:{_MONETARY_BASE_DB}/{_MONETARY_BASE_CODE}",
        frequency="m",
        unit="億円",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 労働・物価: 企業物価指数（国内企業物価指数、総平均、2020年=100）
# --------------------------------------------------------------------------
_CGPI_DB = "PR01"
_CGPI_CODE = "PRCG20_2200000000"


def fetch_corporate_goods_price_index(client: BojClient | None = None, start: str = "201501") -> SeriesData:
    client = client or BojClient()
    obs = client.get_data_code(_CGPI_DB, _CGPI_CODE, start_date=start)
    observations = [Observation(date=boj_month_to_iso(o.period), value=o.value) for o in obs if o.value is not None]
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="corporate_goods_price_index",
        label="企業物価指数（国内企業物価指数、総平均、2020年=100）",
        source=f"BOJ:{_CGPI_DB}/{_CGPI_CODE}",
        frequency="m",
        unit="指数(2020=100)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 労働・物価: 企業向けサービス価格指数（基本分類指数、総平均、2020年=100）
# --------------------------------------------------------------------------
_SPPI_DB = "PR02"
_SPPI_CODE = "PRCS20_5200000000"


def fetch_services_producer_price_index(client: BojClient | None = None, start: str = "201501") -> SeriesData:
    client = client or BojClient()
    obs = client.get_data_code(_SPPI_DB, _SPPI_CODE, start_date=start)
    observations = [Observation(date=boj_month_to_iso(o.period), value=o.value) for o in obs if o.value is not None]
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="services_producer_price_index",
        label="企業向けサービス価格指数（基本分類指数、総平均、2020年=100）",
        source=f"BOJ:{_SPPI_DB}/{_SPPI_CODE}",
        frequency="m",
        unit="指数(2020=100)",
        observations=observations,
    )


# --------------------------------------------------------------------------
# 貿易・生産: 国際収支統計 経常収支
# --------------------------------------------------------------------------
_BOP_DB = "BP01"
_BOP_CODE = "BPBP6JYNCB"


def fetch_balance_of_payments(client: BojClient | None = None, start: str = "201501") -> SeriesData:
    client = client or BojClient()
    obs = client.get_data_code(_BOP_DB, _BOP_CODE, start_date=start)
    observations = [Observation(date=boj_month_to_iso(o.period), value=o.value) for o in obs if o.value is not None]
    observations.sort(key=lambda o: o.date)
    return SeriesData(
        key="balance_of_payments",
        label="国際収支統計 経常収支",
        source=f"BOJ:{_BOP_DB}/{_BOP_CODE}",
        frequency="m",
        unit="億円",
        observations=observations,
    )


def fetch_all_extra_sectors() -> list[SeriesData]:
    return [
        fetch_tokyo_cpi(),
        fetch_gdp_deflator(),
        fetch_household_survey(),
        fetch_consumer_confidence_survey(),
        fetch_corporate_outlook_survey(),
        fetch_steel_industry_index(),
        fetch_tankan(),
        fetch_monetary_base(),
        fetch_corporate_goods_price_index(),
        fetch_services_producer_price_index(),
        fetch_balance_of_payments(),
    ]


def main() -> None:
    from .store import save_bundle

    series = fetch_all_extra_sectors()
    for s in series:
        latest = s.latest()
        latest_str = f"{latest.date} = {latest.value}" if latest else "（有効値なし）"
        print(f"[OK]   {s.key:28s} {s.label} … {len(s.observations)}点 / 最新 {latest_str}")

    out_path = save_bundle(series, "sectors_extra.json")
    print(f"\n保存: {out_path}（{len(series)}指標）")


if __name__ == "__main__":
    main()
