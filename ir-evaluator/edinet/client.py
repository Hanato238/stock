"""EDINET API v2 クライアント"""

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

EDINET_BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"
# 有報: docTypeCode=120, 決算短信（連結）: 140, 決算短信（非連結）: 150
DOC_TYPE_ANNUAL_REPORT = "120"
DOC_TYPE_EARNINGS_BRIEF_CONSOLIDATED = "140"
DOC_TYPE_EARNINGS_BRIEF_NONCONSOLIDATED = "150"


class EdinetClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ["EDINET_API_KEY"]
        self.session = requests.Session()
        self.session.params = {"Subscription-Key": self.api_key}

    def get_documents(self, date: str, doc_type: int = 2) -> dict:
        """指定日の提出書類一覧を取得する。
        doc_type: 1=メタデータのみ, 2=メタデータ+書類一覧
        """
        resp = self.session.get(
            f"{EDINET_BASE_URL}/documents.json",
            params={"date": date, "type": doc_type},
        )
        resp.raise_for_status()
        return resp.json()

    def search_company(self, company_name: str, date_from: str, date_to: str) -> list[dict]:
        """企業名で有報・決算短信を検索する（日付範囲で走査）。"""
        from datetime import date, timedelta

        results = []
        current = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
        target_types = {DOC_TYPE_ANNUAL_REPORT, DOC_TYPE_EARNINGS_BRIEF_CONSOLIDATED, DOC_TYPE_EARNINGS_BRIEF_NONCONSOLIDATED}

        while current <= end:
            date_str = current.isoformat()
            try:
                data = self.get_documents(date_str, doc_type=2)
                for doc in data.get("results", []):
                    if company_name in (doc.get("filerName") or "") and doc.get("docTypeCode") in target_types:
                        results.append(doc)
            except requests.HTTPError:
                pass
            time.sleep(0.5)  # レート制限対策
            current += timedelta(days=1)

        return results

    def get_document_by_edinet_code(self, edinet_code: str, date_from: str, date_to: str) -> list[dict]:
        """EDINETコードで有報・決算短信を取得する（日付範囲で走査）。"""
        from datetime import date, timedelta

        results = []
        current = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
        target_types = {DOC_TYPE_ANNUAL_REPORT, DOC_TYPE_EARNINGS_BRIEF_CONSOLIDATED, DOC_TYPE_EARNINGS_BRIEF_NONCONSOLIDATED}

        while current <= end:
            date_str = current.isoformat()
            try:
                data = self.get_documents(date_str, doc_type=2)
                for doc in data.get("results", []):
                    if doc.get("edinetCode") == edinet_code and doc.get("docTypeCode") in target_types:
                        results.append(doc)
            except requests.HTTPError:
                pass
            time.sleep(0.5)
            current += timedelta(days=1)

        return results

    def download_pdf(self, doc_id: str, output_dir: Path) -> Path:
        """書類PDFをダウンロードして保存する。戻り値は保存先パス。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        resp = self.session.get(
            f"{EDINET_BASE_URL}/documents/{doc_id}",
            params={"type": 2},  # type=2: PDF
            stream=True,
        )
        resp.raise_for_status()

        output_path = output_dir / f"{doc_id}.pdf"
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path

    def download_xbrl(self, doc_id: str, output_dir: Path) -> Path:
        """書類XBRLをダウンロードして保存する（財務数値抽出用）。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        resp = self.session.get(
            f"{EDINET_BASE_URL}/documents/{doc_id}",
            params={"type": 1},  # type=1: XBRL
            stream=True,
        )
        resp.raise_for_status()

        output_path = output_dir / f"{doc_id}.xbrl.zip"
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path
