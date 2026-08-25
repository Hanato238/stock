"""Gemini Batch API 呼び出しヘルパー（OCR・Embedding共通）"""

import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

POLL_INTERVAL = 30
TERMINAL_STATES = {
    types.JobState.JOB_STATE_SUCCEEDED,
    types.JobState.JOB_STATE_FAILED,
    types.JobState.JOB_STATE_CANCELLED,
    types.JobState.JOB_STATE_EXPIRED,
    types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
}

_client = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _client


def upload_file(path, mime_type: str, display_name: str) -> types.File:
    """ファイルをGemini Files APIにアップロードする（バッチのファイル参照用）。
    SDKはstr/PathLikeを渡すとファイル名をHTTPヘッダーX-Goog-Upload-File-Nameにそのまま載せるため、
    日本語ファイル名だとUnicodeEncodeErrorになる。開いたファイルオブジェクトを渡してこれを回避する。
    """
    with open(path, "rb") as f:
        return get_client().files.upload(
            file=f, config=types.UploadFileConfig(mime_type=mime_type, display_name=display_name)
        )


def wait_for_batch(job: types.BatchJob, poll_interval: int = POLL_INTERVAL) -> types.BatchJob:
    """バッチジョブが終了状態になるまでポーリングする。"""
    client = get_client()
    while job.state not in TERMINAL_STATES:
        print(f"  batch {job.name}: {job.state.name}")
        time.sleep(poll_interval)
        job = client.batches.get(name=job.name)
    print(f"  batch {job.name}: {job.state.name}")
    return job
