"""マクロ指標データ → LLM で「読み方」パネル・「総合見立て」を生成する。

evaluation プロンプト設計と同じ方針: 数値の集計・フォーマットはすべてコード側で
行い、LLM には解釈・言語化のみを委ねる（LLM に数値そのものを計算させない）。

macro パッケージは evaluation パッケージへ依存しない設計だが（逆方向は
evaluation.engine が macro.context を遅延 import している）、LLM 呼び出し層
（evaluation.llm）は provider 非依存で汎用的なため、ここでも遅延 import で
再利用する。静的な相互依存は発生しない。
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .store import SeriesData, load_bundle

_DEFAULT_NARRATIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "macro"
_NARRATIVE_FILENAME = "narrative.json"

DEFAULT_NARRATIVE_MODEL = os.environ.get("NARRATIVE_MODEL", "gemini-flash-latest")

TONES = ("expand", "neutral", "contract")

_TONE_LABEL_FALLBACK = {
    "expand": "拡大局面",
    "neutral": "巡航速度の成長",
    "contract": "後退局面",
}


@dataclass
class NarrativeParagraph:
    heading: str
    body: str


@dataclass
class MacroNarrative:
    region: str  # "japan" | "us"
    tone: str  # "expand" | "neutral" | "contract"
    tone_label: str
    paragraphs: list[NarrativeParagraph] = field(default_factory=list)
    verdict: str = ""
    model: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "region": self.region,
            "tone": self.tone,
            "tone_label": self.tone_label,
            "paragraphs": [{"heading": p.heading, "body": p.body} for p in self.paragraphs],
            "verdict": self.verdict,
            "model": self.model,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MacroNarrative:
        return cls(
            region=d["region"],
            tone=d["tone"],
            tone_label=d["tone_label"],
            paragraphs=[NarrativeParagraph(**p) for p in d.get("paragraphs", [])],
            verdict=d.get("verdict", ""),
            model=d.get("model", ""),
            generated_at=d.get("generated_at", ""),
        )


class NarrativeError(RuntimeError):
    """マクロ見立て生成の失敗（データ不足・LLM 失敗など）。"""


@dataclass
class SectorNarrative:
    """セクター指標カタログの1分野（タブ）分の短い解説文。"""

    tab: str
    indicator_key: str
    body: str
    model: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "tab": self.tab,
            "indicator_key": self.indicator_key,
            "body": self.body,
            "model": self.model,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SectorNarrative:
        return cls(
            tab=d["tab"],
            indicator_key=d["indicator_key"],
            body=d["body"],
            model=d.get("model", ""),
            generated_at=d.get("generated_at", ""),
        )


# タブ（5分野）ごとの代表指標キー（data/macro/sectors.json、macro/fetch_sectors.py が
# 実際に取得・検証した5系列）。macro/report.py の表示側もこのマッピングを共有する。
SECTOR_TAB_KEYS: dict[str, str] = {
    "消費・小売": "economy_watchers_di",
    "企業活動・景況": "corporate_profit_margin",
    "貿易・生産": "core_machinery_orders",
    "労働・物価": "unemployment_rate",
    "不動産・金融": "money_stock_m2",
}


# --------------------------------------------------------------------------
# 事実の整理（コード側で計算・フォーマット。LLM には渡すだけ）
# --------------------------------------------------------------------------

def _fmt(value: float, unit: str) -> str:
    if unit.startswith("指数"):
        return f"{value:.1f}"
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "円":
        return f"{value:,.0f}円"
    if unit == "pt":
        return f"{value:,.0f}"
    if unit == "円/ドル":
        return f"{value:.1f}円"
    if unit == "10億円":
        return f"{value / 1000:.1f}兆円"
    return f"{value:g}{unit}"


def _recent(series: SeriesData, days: int = 730) -> SeriesData:
    """直近 `days` 日分に絞った複製を返す（長期系列を短期系列と時間軸を揃えるため）。

    overall.json（2015年〜）のような長期系列をそのまま _series_fact に渡すと
    「起点比」が10年スパンになり、CI/DI・日経平均（直近1〜2年）と時間軸が揃わず
    LLM が混同しやすい。決算期比較用の他系列とは別に、読み方生成専用の直近窓を作る。
    """
    valid = [o for o in series.observations if o.value is not None]
    if not valid:
        return series
    cutoff = date.fromisoformat(valid[-1].date) - timedelta(days=days)
    trimmed = [o for o in valid if date.fromisoformat(o.date) >= cutoff]
    return SeriesData(
        key=series.key,
        label=series.label,
        source=series.source,
        frequency=series.frequency,
        unit=series.unit,
        observations=trimmed or valid[-1:],
    )


def _series_fact(s: SeriesData) -> str:
    """1系列の直近値・期間内変化・期間内レンジをテキスト化する。"""
    latest = s.latest()
    if latest is None:
        return f"- {s.label}: データなし"
    line = f"- {s.label}: {_fmt(latest.value, s.unit)}（{latest.date}時点）"

    valid = [o for o in s.observations if o.value is not None]
    if len(valid) >= 2:
        first = valid[0]
        if s.unit == "%":
            delta = latest.value - first.value
            line += f" ／ 期間内変化 {delta:+.2f}%pt（起点 {first.date}: {_fmt(first.value, s.unit)}）"
        elif s.unit == "指数":
            # 裸の「指数」（CFNAI等、ゼロ近傍で振動）は基準値が小さく/ゼロに近く
            # なり得るため、変化率(%)ではなく絶対差（ポイント）で表す。
            delta = latest.value - first.value
            line += f" ／ 期間内変化 {delta:+.2f}ポイント（起点 {first.date}: {_fmt(first.value, s.unit)}）"
        elif first.value:
            pct = (latest.value - first.value) / abs(first.value) * 100.0
            line += f" ／ 期間内変化 {pct:+.1f}%（起点 {first.date}: {_fmt(first.value, s.unit)}）"
        vmax = max(valid, key=lambda o: o.value)
        vmin = min(valid, key=lambda o: o.value)
        line += (
            f" ／ 期間内高値 {_fmt(vmax.value, s.unit)}（{vmax.date}）"
            f" ／ 期間内安値 {_fmt(vmin.value, s.unit)}（{vmin.date}）"
        )
    return line


_JAPAN_MARKET_KEYS = (
    "ci_leading",
    "ci_coincident",
    "ci_lagging",
    "di_leading",
    "di_coincident",
    "di_lagging",
    "nikkei225",
    "jp_10y",
)
_OVERALL_KEYS = ("usd_jpy", "policy_rate", "nominal_gdp", "cpi_core")
_US_KEYS = (
    "cfnai",
    "cfnai_ma3",
    "sp500",
    "us_10y",
    "fed_funds",
    "us_cpi",
    "us_cpi_core",
    "us_gdp_growth",
)


def _build_japan_facts() -> list[str]:
    market = load_bundle("japan_market.json")
    overall = load_bundle("overall.json")
    facts = [_series_fact(market[k]) for k in _JAPAN_MARKET_KEYS if k in market]
    facts += [_series_fact(_recent(overall[k])) for k in _OVERALL_KEYS if k in overall]
    if not facts:
        raise NarrativeError(
            "japan_market.json / overall.json が見つかりません。"
            "`uv run python -m macro.fetch_jp_market` / `uv run python -m macro.fetch` を先に実行してください。"
        )
    return facts


def _build_us_facts() -> list[str]:
    us = load_bundle("us.json")
    facts = [_series_fact(us[k]) for k in _US_KEYS if k in us]
    if not facts:
        raise NarrativeError(
            "us.json が見つかりません。`uv run python -m macro.fetch_us` を先に実行してください。"
        )
    return facts


# --------------------------------------------------------------------------
# LLM 生成
# --------------------------------------------------------------------------

_PROMPT = """\
あなたは日本の投資家向けにマクロ経済の状況を解説するアナリストです。
以下は{region_ja}の主要マクロ指標の実測データです（すべて事実。数値の再計算は不要）。

{facts}

このデータに基づいて、以下を日本語で作成してください。
1. tone: 現在の景気循環が "expand"（拡大局面）・"neutral"（トレンド並み・巡航速度）・
   "contract"（後退局面）のいずれに近いか
2. tone_label: 上記を8〜12文字程度の日本語ラベルで（例: "緩やかな拡大局面"）
3. paragraphs: 2〜3個の解説段落（見出しと本文）。景気動向指数/活動指数、株式市場、
   金利・為替・物価、のようにテーマ別に分ける。各本文は150〜250字程度、必ず上記データの
   具体的な数値を引用すること。データにない数値を創作しないこと。
4. verdict: 総合見立てを2〜3文で。景気循環そのものへの見立てに限定し、個別株の
   売買判断や「買い時/売り時」には踏み込まないこと（それは別ページの役割）。

出力は次の JSON のみ（前後に地の文やコードフェンスを付けない）:
{{"tone": "expand|neutral|contract", "tone_label": "...", "paragraphs": [{{"heading": "...", "body": "..."}}, ...], "verdict": "..."}}
"""

_REGION_JA = {"japan": "日本", "us": "米国"}


def generate_narrative(region: str, *, model: str | None = None) -> MacroNarrative:
    """region: "japan" | "us". データ取得済みの JSON バンドルから読み方・総合見立てを生成する。"""
    if region == "japan":
        facts = _build_japan_facts()
    elif region == "us":
        facts = _build_us_facts()
    else:
        raise ValueError(f"未対応の region: {region!r}（'japan' か 'us'）")

    # 遅延 import: macro パッケージを evaluation パッケージへ静的依存させないため。
    from evaluation.llm import LLMError, generate_json
    from evaluation.schema import _loads_lenient

    prompt = _PROMPT.format(region_ja=_REGION_JA[region], facts="\n".join(facts))
    use_model = model or DEFAULT_NARRATIVE_MODEL
    try:
        resp = generate_json(prompt, model=use_model, max_output_tokens=4096)
        data = _loads_lenient(resp.text)
    except LLMError as e:
        raise NarrativeError(f"LLM呼び出しに失敗しました: {e}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise NarrativeError(f"LLM応答のパースに失敗しました: {e}") from e

    tone = str(data.get("tone", "")).strip()
    if tone not in TONES:
        tone = "neutral"
    tone_label = str(data.get("tone_label", "")).strip() or _TONE_LABEL_FALLBACK[tone]

    paragraphs: list[NarrativeParagraph] = []
    for p in data.get("paragraphs", []):
        if not isinstance(p, dict):
            continue
        heading = str(p.get("heading", "")).strip()
        body = str(p.get("body", "")).strip()
        if heading and body:
            paragraphs.append(NarrativeParagraph(heading=heading, body=body))

    return MacroNarrative(
        region=region,
        tone=tone,
        tone_label=tone_label,
        paragraphs=paragraphs,
        verdict=str(data.get("verdict", "")).strip(),
        model=use_model,
    )


# --------------------------------------------------------------------------
# セクター指標カタログ（5分野タブ）の解説生成
# --------------------------------------------------------------------------

_SECTOR_PROMPT = """\
あなたは日本の投資家向けにセクター（経済分野）ごとの景況を解説するアナリストです。
以下は日本経済を5つの分野に分けたときの、各分野を代表する指標の実測データです
（すべて事実。数値の再計算は不要）。

{facts}

各分野について、その代表指標の動きが何を意味するかを日本語で80〜150字程度の
短い解説文にしてください。
- 必ず上記データの具体的な数値を引用すること。データにない数値を創作しないこと
- 分野同士は互いに独立に評釈してよい。日本経済全体の基調と無理に整合を取ろうとしないこと
- 個別銘柄の売買判断や「買い時/売り時」には踏み込まないこと

出力は次の JSON のみ（前後に地の文やコードフェンスを付けない）:
{{"sectors": [{{"tab": "分野名", "body": "..."}}, ...]}}
"""


def generate_sector_narratives(*, model: str | None = None) -> dict[str, SectorNarrative]:
    """5分野（タブ）それぞれの代表指標から、短い解説を1回のLLM呼び出しでまとめて生成する。

    `data/macro/sectors.json`（`macro.fetch_sectors`）が無ければ NarrativeError。
    呼び出し側（macro.report）はベストエフォートでこれを捕捉しスキップする想定
    （代表指標のチャート自体は sectors.json 無しでも表示できるため）。
    """
    try:
        sectors = load_bundle("sectors.json")
    except FileNotFoundError as e:
        raise NarrativeError(
            f"{e.filename} が見つかりません。`uv run python -m macro.fetch_sectors` を先に実行してください。"
        ) from e

    facts: list[str] = []
    available: dict[str, str] = {}  # tab -> indicator_key（実際にデータがある分だけ）
    for tab, key in SECTOR_TAB_KEYS.items():
        series = sectors.get(key)
        if series is None:
            continue
        facts.append(f"【{tab}】（代表指標: {series.label}）\n{_series_fact(_recent(series))}")
        available[tab] = key

    if not facts:
        raise NarrativeError("sectors.json に代表指標のデータがありません。")

    # 遅延 import: macro パッケージを evaluation パッケージへ静的依存させないため。
    from evaluation.llm import LLMError, generate_json
    from evaluation.schema import _loads_lenient

    prompt = _SECTOR_PROMPT.format(facts="\n\n".join(facts))
    use_model = model or DEFAULT_NARRATIVE_MODEL
    try:
        resp = generate_json(prompt, model=use_model, max_output_tokens=2048, light=True)
        data = _loads_lenient(resp.text)
    except LLMError as e:
        raise NarrativeError(f"LLM呼び出しに失敗しました: {e}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise NarrativeError(f"LLM応答のパースに失敗しました: {e}") from e

    raw = data.get("sectors", [])
    bodies: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            tab = str(item.get("tab", "")).strip()
            body = str(item.get("body", "")).strip()
            if tab in available and body:
                bodies[tab] = body

    return {
        tab: SectorNarrative(tab=tab, indicator_key=key, body=bodies[tab], model=use_model)
        for tab, key in available.items()
        if tab in bodies
    }


# --------------------------------------------------------------------------
# 永続化（企業評価レポート側が LLM を再呼び出しせず引用するためのキャッシュ）
# --------------------------------------------------------------------------

def _read_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_narratives(
    japan: MacroNarrative,
    us: MacroNarrative,
    *,
    directory: Path = _DEFAULT_NARRATIVE_DIR,
    filename: str = _NARRATIVE_FILENAME,
) -> Path:
    """日米の見立てを1ファイルにまとめて保存する（週次更新でマクロページ生成時に呼ぶ想定）。

    既存ファイルに `sectors`（`save_sector_narratives` が書いた分野解説）があれば
    読み込んでマージし、上書きで消さない（呼び出し順序に依存しないようにするため）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    payload = _read_existing(path)
    payload["japan"] = japan.to_dict()
    payload["us"] = us.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_narratives(
    *, directory: Path = _DEFAULT_NARRATIVE_DIR, filename: str = _NARRATIVE_FILENAME
) -> dict[str, MacroNarrative] | None:
    """保存済みの日米見立てを読み込む。未生成なら None（LLM は呼ばない）。"""
    path = directory / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    regions = {k: v for k, v in data.items() if k in ("japan", "us")}
    if not regions:
        return None
    return {region: MacroNarrative.from_dict(d) for region, d in regions.items()}


def save_sector_narratives(
    sectors: dict[str, SectorNarrative],
    *,
    directory: Path = _DEFAULT_NARRATIVE_DIR,
    filename: str = _NARRATIVE_FILENAME,
) -> Path:
    """5分野の解説を `sectors` キーへ保存する（japan/us キーは保持したままマージ）。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    payload = _read_existing(path)
    payload["sectors"] = {tab: sn.to_dict() for tab, sn in sectors.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_sector_narratives(
    *, directory: Path = _DEFAULT_NARRATIVE_DIR, filename: str = _NARRATIVE_FILENAME
) -> dict[str, SectorNarrative] | None:
    """保存済みの分野解説を読み込む。未生成なら None（LLM は呼ばない）。"""
    path = directory / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("sectors")
    if not raw:
        return None
    return {tab: SectorNarrative.from_dict(d) for tab, d in raw.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="マクロの読み方・総合見立て／分野解説をLLM生成")
    parser.add_argument("region", choices=("japan", "us", "sectors"))
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if args.region == "sectors":
        result = generate_sector_narratives(model=args.model)
        print(json.dumps({tab: sn.to_dict() for tab, sn in result.items()}, ensure_ascii=False, indent=2))
        return

    narrative = generate_narrative(args.region, model=args.model)
    print(json.dumps(narrative.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
