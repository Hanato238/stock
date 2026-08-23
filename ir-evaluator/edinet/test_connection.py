"""EDINET API接続テスト — APIキー取得後にこのスクリプトで動作確認する"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from edinet.client import EdinetClient


def main():
    api_key = os.environ.get("EDINET_API_KEY")
    if not api_key:
        print("Error: EDINET_API_KEY が .env に設定されていません")
        print("  1. https://api.edinet-fsa.go.jp/ でAPIキーを取得")
        print("  2. .env.example を .env にコピーしてキーを設定")
        sys.exit(1)

    client = EdinetClient(api_key)
    print("EDINET API接続テスト...")

    # 直近の提出日で書類一覧を取得
    test_date = "2024-06-28"  # トヨタ等の有報提出が多い時期
    data = client.get_documents(test_date, doc_type=2)

    status = data.get("metadata", {}).get("status")
    count = data.get("metadata", {}).get("resultset", {}).get("count", 0)
    print(f"Status: {status}")
    print(f"書類数: {count} 件 ({test_date})")

    # 有報だけ抽出して表示
    annual_reports = [
        d for d in data.get("results", [])
        if d.get("docTypeCode") == "120"
    ]
    print(f"うち有価証券報告書: {len(annual_reports)} 件")
    for doc in annual_reports[:5]:
        print(f"  - {doc.get('filerName')} [{doc.get('edinetCode')}] {doc.get('docDescription')}")

    print("\nOK: EDINET API が正常に動作しています")


if __name__ == "__main__":
    main()
