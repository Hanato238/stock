"""プロバイダ非依存の LLM 呼び出し層。

モデル名からプロバイダを判定して振り分ける:
    - ``gemini-*``            → Google Gemini（google-genai。既存の OCR/Embedding と同じ SDK）
    - ``claude-*`` / ``anthropic:*`` → Anthropic Claude（anthropic SDK。任意依存）
    - ``gpt-*`` / ``o1-*`` / ``o3-*`` / ``openai:*`` → OpenAI（openai SDK。任意依存）

新プロバイダは ``_BACKENDS`` に 1 関数追加すれば増やせる。各バックエンドの SDK は
遅延 import で、そのプロバイダを実際に使うときだけ必要になる。

評価モデルの既定は環境変数 ``EVAL_MODEL``（未設定なら ``gemini-2.5-pro``）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_EVAL_MODEL = os.environ.get("EVAL_MODEL", "gemini-3.1-pro-preview")

# 評価は長文出力になり得るので広めに取る。
DEFAULT_MAX_OUTPUT_TOKENS = 8192


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str


class LLMError(RuntimeError):
    """LLM 呼び出しに関する失敗（SDK 未導入・API エラー等）。"""


# --------------------------------------------------------------------------
# プロバイダ判定
# --------------------------------------------------------------------------

def resolve_provider(model: str) -> str:
    lowered = model.lower()
    if ":" in lowered:
        prefix = lowered.split(":", 1)[0]
        if prefix in _BACKENDS:
            return prefix
    if lowered.startswith("gemini"):
        return "gemini"
    if lowered.startswith("claude"):
        return "anthropic"
    if lowered.startswith(("gpt-", "gpt4", "o1-", "o3-", "o4-")):
        return "openai"
    raise LLMError(
        f"モデル名 '{model}' からプロバイダを判定できません。"
        "'gemini-*' / 'claude-*' / 'gpt-*'、または 'provider:model' 形式で指定してください。"
    )


def _strip_provider_prefix(model: str) -> str:
    return model.split(":", 1)[1] if ":" in model else model


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------

def generate_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    light: bool = False,
) -> LLMResponse:
    """JSON を返すことを期待するプロンプトを投げ、生テキストを返す。

    Args:
        light: 単純な抽出タスク。可能なら thinking を無効化して速度・コストを優先する
            （Gemini の思考が出力トークンを食い潰して空応答になるのを防ぐ）。

    パース（コードフェンス除去・寛容な JSON 抽出）は呼び出し側の責務
    （:meth:`evaluation.schema.EvaluationResult.from_llm_json`）。
    """
    model = model or DEFAULT_EVAL_MODEL
    provider = resolve_provider(model)
    backend = _BACKENDS[provider]
    bare_model = _strip_provider_prefix(model)
    try:
        text = backend(prompt, system, bare_model, max_output_tokens, want_json=True, light=light)
    except LLMError:
        raise
    except Exception as e:  # SDK 固有の例外を LLMError に正規化する
        raise LLMError(f"{provider} 呼び出しに失敗しました（{bare_model}）: {e}") from e
    return LLMResponse(text=text, model=bare_model, provider=provider)


# --------------------------------------------------------------------------
# バックエンド実装（遅延 import）
# --------------------------------------------------------------------------

def _gemini_generate(
    prompt: str,
    system: str | None,
    model: str,
    max_output_tokens: int,
    *,
    want_json: bool,
    light: bool = False,
) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:  # pragma: no cover - 既定依存なので通常起きない
        raise LLMError("google-genai が見つかりません（uv sync してください）") from e

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise LLMError("GOOGLE_API_KEY が未設定です")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        max_output_tokens=max_output_tokens,
        system_instruction=system,
        response_mime_type="application/json" if want_json else None,
        # 単純抽出は thinking off（Gemini 3 系は既定 on で、少ない出力上限だと
        # 思考トークンを使い切って本文が空になることがある）。
        thinking_config=types.ThinkingConfig(thinking_budget=0) if light else None,
    )
    resp = client.models.generate_content(model=model, contents=prompt, config=config)
    text = resp.text or ""
    if not text.strip():
        raise LLMError(
            f"Gemini から空応答（model={model}, "
            f"finish={getattr(resp.candidates[0], 'finish_reason', '?') if resp.candidates else '?'}）"
        )
    return text


def _anthropic_generate(
    prompt: str,
    system: str | None,
    model: str,
    max_output_tokens: int,
    *,
    want_json: bool,
    light: bool = False,  # Claude 側では未使用（SDK が空応答をリトライ吸収）
) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise LLMError(
            "anthropic SDK が未導入です。`uv add anthropic` で追加してください。"
        ) from e

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY が未設定です")

    client = anthropic.Anthropic()
    sys_text = system or ""
    if want_json:
        sys_text = (sys_text + "\n\n必ず JSON オブジェクトのみを返してください。").strip()

    # 大きめ出力になり得るのでストリーミングで受ける（HTTP タイムアウト回避）。
    with client.messages.stream(
        model=model,
        max_tokens=max_output_tokens,
        system=sys_text or anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()

    parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    text = "".join(parts)
    if not text.strip():
        raise LLMError(f"Claude から空応答（model={model}, stop_reason={message.stop_reason}）")
    return text


def _openai_generate(
    prompt: str,
    system: str | None,
    model: str,
    max_output_tokens: int,
    *,
    want_json: bool,
    light: bool = False,  # OpenAI 側では未使用
) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise LLMError("openai SDK が未導入です。`uv add openai` で追加してください。") from e

    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY が未設定です")

    client = OpenAI()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_output_tokens,
        response_format={"type": "json_object"} if want_json else {"type": "text"},
    )
    text = resp.choices[0].message.content or ""
    if not text.strip():
        raise LLMError(f"OpenAI から空応答（model={model}）")
    return text


_BACKENDS: dict[str, Callable[..., str]] = {
    "gemini": _gemini_generate,
    "anthropic": _anthropic_generate,
    "openai": _openai_generate,
}
