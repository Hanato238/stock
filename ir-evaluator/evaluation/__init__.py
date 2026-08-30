"""Phase 4: 投資適格性評価エンジン。

企業の有報 PDF を入力に、審査辞典 RAG（2 段階検索）とマクロ経済環境ブロックを
組み合わせた評価プロンプトを LLM に投げ、リスク評点・要注意項目・投資適格性
コメントを含む構造化結果と Markdown レポートを生成する。

LLM プロバイダはモデル名で切り替わる（`gemini-*` → Gemini, `claude-*` → Anthropic）。
"""

from .classify import classify_industry
from .engine import EvaluationInput, evaluate_company, evaluate_document
from .report import render_markdown, save_report
from .retrieval import Evidence, retrieve_evidence
from .schema import EvaluationResult, RiskScore, WatchItem

__all__ = [
    "EvaluationInput",
    "EvaluationResult",
    "Evidence",
    "RiskScore",
    "WatchItem",
    "classify_industry",
    "evaluate_company",
    "evaluate_document",
    "render_markdown",
    "retrieve_evidence",
    "save_report",
]
