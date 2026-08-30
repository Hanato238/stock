"""評価結果のデータ構造と、LLM が返す JSON からの復元。

スコアの向き（プロジェクト決定）:
    リスク評点は 1〜5 で **1 = 低リスク / 5 = 高リスク**。
    要注意項目リストと向きを揃える（数字が大きいほど危険）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

# リスク評点の軸（この 3 軸で固定）。
RISK_AXES = ("財務", "事業", "経営")

SCORE_MIN = 1
SCORE_MAX = 5

# 総合判定の許容値（LLM 出力を正規化する際の対応表も兼ねる）。
GRADES = ("適格", "条件付き適格", "要精査", "非適格")


@dataclass
class RiskScore:
    """1 軸分のリスク評点。score は 1(低リスク)〜5(高リスク)。"""

    axis: str
    score: int
    rationale: str

    def clamped(self) -> RiskScore:
        s = max(SCORE_MIN, min(SCORE_MAX, int(self.score)))
        return RiskScore(axis=self.axis, score=s, rationale=self.rationale)


@dataclass
class WatchItem:
    """要注意項目。severity は "高" | "中" | "低"。"""

    title: str
    detail: str
    severity: str = "中"


@dataclass
class EvaluationResult:
    company: str
    fiscal_period: str
    overall_grade: str
    summary: str
    risk_scores: list[RiskScore] = field(default_factory=list)
    watch_items: list[WatchItem] = field(default_factory=list)
    investment_comment: str = ""
    model: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # ---- 直列化 -------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ---- LLM 出力からの復元 ----------------------------------------------

    @classmethod
    def from_llm_json(
        cls,
        raw: str | dict,
        *,
        company: str,
        fiscal_period: str,
        model: str,
    ) -> EvaluationResult:
        """LLM が返した JSON（文字列 or dict）から結果を組み立てる。

        余計なコードフェンスや前置きが付いていても寛容にパースする。
        欠損フィールドは安全側の既定値で埋める。
        """
        data = raw if isinstance(raw, dict) else _loads_lenient(raw)

        scores: list[RiskScore] = []
        by_axis = {s.get("axis"): s for s in data.get("risk_scores", []) if isinstance(s, dict)}
        for axis in RISK_AXES:
            src = by_axis.get(axis, {})
            try:
                score = int(src.get("score", 3))
            except (TypeError, ValueError):
                score = 3
            scores.append(
                RiskScore(axis=axis, score=score, rationale=str(src.get("rationale", ""))).clamped()
            )

        watch: list[WatchItem] = []
        for item in data.get("watch_items", []):
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "中")).strip()
            if severity not in ("高", "中", "低"):
                severity = "中"
            watch.append(
                WatchItem(
                    title=str(item.get("title", "")).strip(),
                    detail=str(item.get("detail", "")).strip(),
                    severity=severity,
                )
            )

        grade = _normalize_grade(str(data.get("overall_grade", "")))

        return cls(
            company=company,
            fiscal_period=fiscal_period,
            overall_grade=grade,
            summary=str(data.get("summary", "")).strip(),
            risk_scores=scores,
            watch_items=watch,
            investment_comment=str(data.get("investment_comment", "")).strip(),
            model=model,
        )


def _loads_lenient(text: str) -> dict:
    """```json フェンスや前後の地の文が混じった応答から最初の JSON オブジェクトを取り出す。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 最初の { から対応する } までを括弧の対応で切り出す。
    start = text.find("{")
    if start == -1:
        raise ValueError("LLM 応答に JSON オブジェクトが見つかりません")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("LLM 応答の JSON が閉じていません")


def _normalize_grade(value: str) -> str:
    """LLM の総合判定文字列を GRADES のいずれかへ寄せる。

    「非適格」「条件付き適格」は「適格」を部分文字列に含むため、より具体的な
    ラベル・言い換えを先に判定する。
    """
    value = value.strip()
    # (部分一致する語, 正規化後) を具体的な順に並べる。「適格」は最後。
    rules = [
        ("非適格", "非適格"),
        ("不適格", "非適格"),
        ("条件付", "条件付き適格"),
        ("要精査", "要精査"),
        ("留保", "要精査"),
        ("適格", "適格"),
    ]
    for needle, grade in rules:
        if needle in value:
            return grade
    return value or "要精査"


# 評価プロンプトに埋め込む出力スキーマの説明（人間可読・provider 非依存）。
OUTPUT_SCHEMA_HINT = """\
出力は次の形式の JSON オブジェクトのみ（前後に地の文やコードフェンスを付けない）:

{
  "overall_grade": "適格 | 条件付き適格 | 要精査 | 非適格 のいずれか",
  "summary": "3〜5文の総括",
  "risk_scores": [
    {"axis": "財務", "score": 1-5, "rationale": "根拠（数値・事実ベース）"},
    {"axis": "事業", "score": 1-5, "rationale": "..."},
    {"axis": "経営", "score": 1-5, "rationale": "..."}
  ],
  "watch_items": [
    {"title": "見出し", "detail": "具体的な懸念と確認すべき点", "severity": "高 | 中 | 低"}
  ],
  "investment_comment": "投資判断に資する所見（与信・株式双方の観点で200〜400字）"
}

スコアの向き: 1 = 低リスク / 5 = 高リスク。"""
