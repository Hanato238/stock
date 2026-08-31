"""e-Stat API v3.0 クライアント（政府統計。総務省 CPI・業種別 DI/IIP 用）。

FRED の日本 CPI 月次指数が現行維持されていないため、CPI は一次ソースの
総務省統計（e-Stat）から取得する。無料のアプリ ID が必要（環境変数 ESTAT_APP_ID）。

正式な統計表 ID（statsDataId）は search_stats_list() で検索して特定する。
getStatsData の応答は分類コード（品目・地域・時間）で値が展開されるため、
CLASS_INF（メタ定義）と併せて対象系列（例: 総合・全国）を絞り込む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

load_dotenv()

ESTAT_BASE_URL = "https://api.e-stat.go.jp/rest/3.0/app/json"


@dataclass
class StatsTable:
    """統計表の検索結果 1 件。"""

    stats_data_id: str
    title: str
    survey_date: str  # 調査年月（表側の目安）


class EstatClient:
    def __init__(self, app_id: str | None = None):
        self.app_id = app_id or os.environ["ESTAT_APP_ID"]
        self.session = requests.Session()

    def search_stats_list(self, search_word: str, limit: int = 20) -> list[StatsTable]:
        """統計表を横断検索する（正式な statsDataId 特定用）。"""
        resp = self.session.get(
            f"{ESTAT_BASE_URL}/getStatsList",
            params={"appId": self.app_id, "searchWord": search_word, "limit": limit},
        )
        resp.raise_for_status()
        body = resp.json()["GET_STATS_LIST"]
        datalist = body.get("DATALIST_INF", {})
        tables = datalist.get("TABLE_INF", [])
        if isinstance(tables, dict):  # 1 件のとき dict になる
            tables = [tables]
        out: list[StatsTable] = []
        for t in tables:
            title = t.get("TITLE")
            title_str = title.get("$") if isinstance(title, dict) else title
            out.append(
                StatsTable(
                    stats_data_id=str(t.get("@id")),
                    title=str(title_str),
                    survey_date=str(t.get("SURVEY_DATE", "")),
                )
            )
        return out

    def get_meta_info(self, stats_data_id: str) -> dict:
        """統計表の分類定義（CLASS_INF）を取得する。対象コード特定用。"""
        resp = self.session.get(
            f"{ESTAT_BASE_URL}/getMetaInfo",
            params={"appId": self.app_id, "statsDataId": stats_data_id},
        )
        resp.raise_for_status()
        return resp.json()["GET_META_INFO"]["METADATA_INF"]

    def get_stats_data(
        self,
        stats_data_id: str,
        cd_cat01: str | None = None,
        cd_area: str | None = None,
        cd_time_from: str | None = None,
        cd_tab: str | None = None,
        limit: int | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict:
        """統計データ本体を取得する。分類コードで対象系列を絞り込む。

        Args:
            stats_data_id: 統計表 ID。
            cd_cat01: 分類事項1コード（例: CPI の「総合」）。
            cd_area: 地域コード（例: 全国）。
            cd_time_from: 取得開始時間コード（例: 201501000000）。
            cd_tab: 表章事項コード（例: 景気動向指数の CI/DI 切り替え）。
            limit: 取得件数上限。
            extra: cat02/cat03 等、上記に無い分類軸を絞り込む場合に
                ``{"cdCat02": "08", "cdCat03": "0"}`` の形で渡す。
        """
        params: dict[str, str] = {"appId": self.app_id, "statsDataId": stats_data_id}
        if cd_cat01:
            params["cdCat01"] = cd_cat01
        if cd_area:
            params["cdArea"] = cd_area
        if cd_time_from:
            params["cdTimeFrom"] = cd_time_from
        if cd_tab:
            params["cdTab"] = cd_tab
        if limit:
            params["limit"] = str(limit)
        if extra:
            params.update(extra)

        resp = self.session.get(f"{ESTAT_BASE_URL}/getStatsData", params=params)
        resp.raise_for_status()
        return resp.json()["GET_STATS_DATA"]["STATISTICAL_DATA"]


def estat_month_to_iso(time_code: str) -> str | None:
    """e-Stat の月次時間コード（例: '2026000707' = 2026年7月）を ISO 日付へ。

    CPI 表の月次コードは YYYY + '00' + MM + MM の 10 桁（月が 6-7 桁目）。
    年次コード（例: '2026000000'）など月に解決できないものは None。
    """
    s = str(time_code)
    if len(s) == 10 and s[4:6] == "00":
        year, month = s[0:4], s[6:8]
        if month.isdigit() and 1 <= int(month) <= 12:
            return f"{year}-{month}-01"
    return None
