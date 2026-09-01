"""マクロ経済モニターの更新パイプラインを1コマンドで実行する。

これまで `japan_market` → `overall`（FRED+CPI）→ `us` → `sectors` → `sectors_extra` →
`report` の6回の手動実行が必要だった（各モジュールの `main()` を参照）。このスクリプトは
同じ処理を1コマンドにまとめ、Phase 5 で想定している週次自動更新（cron/Scheduler）
の実行単位にもそのまま使える。

1ステップが失敗しても（例: 一部APIキー未設定、e-Statの一時的な不通）残りのステップは
続行する。失敗したステップはファイルを書き換えないため、そのデータは前回実行時の
キャッシュのまま残る（`report` ステップはその古いキャッシュでも生成を試みる）。

使い方:
    uv run python -m macro.refresh
    uv run python -m macro.refresh --overall-start 2015-01-01 --model gemini-flash-latest

各ステップの取得開始日は、それぞれの元モジュール（`fetch.py` / `fetch_us.py` 等）が単体実行
されたときの既定値と揃えている（`overall` は長期系列なので2015年〜、`us` はレポートのミニ
チャート用に直近1年程度、という設計が元々異なるため、共通の `--start` 1本にまとめてしまうと
`us` 側が意図せず10年分に膨れ上がる。実際に一度その事故で `data/macro/us.json` が肥大化した
ため、ステップごとに独立したオプションへ分離した）。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass

from .fetch import fetch_overall
from .fetch_cpi import fetch_cpi
from .fetch_jp_market import fetch_ci_di, fetch_jp_market
from .fetch_sectors import fetch_all_sectors
from .fetch_sectors_extra import fetch_all_extra_sectors
from .fetch_us import fetch_us
from .report import load_macro_bundle, save_report
from .store import save_bundle


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str


def _run_step(name: str, fn: Callable[[], str]) -> StepResult:
    try:
        return StepResult(name=name, ok=True, detail=fn())
    except Exception as e:  # このステップの失敗で残りのステップを止めない（週次パイプラインのため）
        return StepResult(name=name, ok=False, detail=f"{type(e).__name__}: {e}")


def _step_japan_market(ci_di_start_year: str, market_start: str) -> str:
    series = fetch_ci_di(start_year=ci_di_start_year) + fetch_jp_market(start=market_start)
    path = save_bundle(series, "japan_market.json")
    return f"{path}（{len(series)}指標）"


def _step_overall(start: str) -> str:
    fetched, failures = fetch_overall(start=start)
    cpi_note = ""
    try:
        fetched.append(fetch_cpi(start_year=start[:4]))
    except Exception as e:  # CPI 単体の失敗で overall 全体を失敗にはしない（fetch.py の既存挙動を踏襲）
        cpi_note = f"、CPIスキップ({type(e).__name__})"
    if not fetched:
        raise RuntimeError("取得成功した指標がありません（FRED_API_KEY / ESTAT_APP_ID を確認）")
    path = save_bundle(fetched, "overall.json")
    fail_note = f"、{len(failures)}件失敗: {', '.join(k for k, _ in failures)}" if failures else ""
    return f"{path}（{len(fetched)}指標{fail_note}{cpi_note}）"


def _step_us(start: str) -> str:
    series = fetch_us(start=start)
    path = save_bundle(series, "us.json")
    return f"{path}（{len(series)}指標）"


def _step_sectors() -> str:
    series = fetch_all_sectors()
    path = save_bundle(series, "sectors.json")
    return f"{path}（{len(series)}指標）"


def _step_sectors_extra() -> str:
    series = fetch_all_extra_sectors()
    path = save_bundle(series, "sectors_extra.json")
    return f"{path}（{len(series)}指標）"


def _step_report(model: str | None) -> str:
    bundle = load_macro_bundle(model=model)
    path = save_report(bundle)
    return str(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="マクロ経済モニターの更新パイプラインを一括実行")
    parser.add_argument("--overall-start", default="2015-01-01", help="overall（FRED+CPI）の取得開始日 YYYY-MM-DD")
    parser.add_argument("--us-start", default="2025-01-01", help="us の取得開始日 YYYY-MM-DD")
    parser.add_argument("--ci-di-start-year", default="2024", help="japan_market の CI/DI 取得開始年")
    parser.add_argument("--market-start", default="2025-08-01", help="japan_market の日経平均等 取得開始日")
    parser.add_argument("--model", default=None, help="読み方・総合見立て・分野解説に使うモデル")
    args = parser.parse_args(argv)

    steps: list[tuple[str, Callable[[], str]]] = [
        ("japan_market", lambda: _step_japan_market(args.ci_di_start_year, args.market_start)),
        ("overall", lambda: _step_overall(args.overall_start)),
        ("us", lambda: _step_us(args.us_start)),
        ("sectors", lambda: _step_sectors()),
        ("sectors_extra", lambda: _step_sectors_extra()),
        ("report", lambda: _step_report(args.model)),
    ]

    results = [_run_step(name, fn) for name, fn in steps]

    print("\n=== マクロ更新パイプライン 結果 ===")
    for r in results:
        tag = "[OK]" if r.ok else "[FAIL]"
        print(f"{tag.ljust(7)}{r.name:14s} {r.detail}")

    if any(not r.ok for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
