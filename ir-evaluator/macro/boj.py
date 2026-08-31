"""日本銀行 時系列統計データ検索サイトの API クライアント（コードAPI）。

2026-02-18 に新規公開された API（登録・APIキー不要）。e-Stat に掲載されていない
日銀統計（マネーストック・マネタリーベース・企業物価指数・短観等）はこちらが一次
情報源になる。マニュアル: https://www.stat-search.boj.or.jp/info/api_manual.pdf
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

BOJ_BASE_URL = "https://www.stat-search.boj.or.jp/api/v1"


@dataclass
class BojObservation:
    period: str  # YYYYMM 形式の文字列（月次系列の場合）
    value: float | None


class BojClient:
    def __init__(self):
        self.session = requests.Session()

    def get_data_code(
        self,
        db: str,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[BojObservation]:
        """コードAPI で1系列の時系列データを取得する（getDataCode）。

        Args:
            db: DB名（例: "MD02" マネーストック）。
            code: 系列コード（例: "MAM1NAM2M2MO"）。カンマ区切りの複数系列には未対応
                （呼び出し側で1系列ずつ呼ぶ）。
            start_date: 開始期（例: "202401"、月次の場合 YYYYMM）。
            end_date: 終了期（例: "202608"）。
        """
        params: dict[str, str] = {"format": "json", "lang": "jp", "db": db, "code": code}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date

        resp = self.session.get(f"{BOJ_BASE_URL}/getDataCode", params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("STATUS") != 200:
            raise BojError(f"BOJ API エラー: {data.get('MESSAGE', '不明なエラー')}")

        resultset = data.get("RESULTSET", [])
        if not resultset:
            return []
        series = resultset[0]
        dates = series["VALUES"]["SURVEY_DATES"]
        values = series["VALUES"]["VALUES"]
        return [BojObservation(period=str(d), value=v) for d, v in zip(dates, values, strict=True)]


class BojError(RuntimeError):
    """BOJ API 呼び出しの失敗。"""


def boj_month_to_iso(period: str) -> str:
    """YYYYMM形式の月次期間をISO日付（月初）へ変換する。"""
    year, month = period[:4], period[4:6]
    return f"{year}-{month}-01"
