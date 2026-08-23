"""企業名または証券コードからIRを取得するハイレベルAPI"""

from pathlib import Path

from .client import EdinetClient


def fetch_ir_for_company(
    company_name: str,
    date_from: str,
    date_to: str,
    output_dir: Path = Path("data/ir"),
    api_key: str | None = None,
) -> list[dict]:
    """企業名でIR書類を検索・ダウンロードし、ファイルパスを含むメタデータリストを返す。

    Returns:
        [{"docID": ..., "filerName": ..., "docTypeCode": ..., "pdf_path": Path}, ...]
    """
    client = EdinetClient(api_key=api_key)
    docs = client.search_company(company_name, date_from, date_to)

    results = []
    for doc in docs:
        doc_id = doc["docID"]
        company_dir = output_dir / doc.get("filerName", "unknown").replace("/", "_")
        try:
            pdf_path = client.download_pdf(doc_id, company_dir)
            results.append({**doc, "pdf_path": pdf_path})
            print(f"Downloaded: {doc.get('filerName')} / {doc.get('docDescription')} -> {pdf_path}")
        except Exception as e:
            print(f"Failed to download {doc_id}: {e}")

    return results
