"""投資適格性評価の CLI。

使い方:
  # ローカルの有報 PDF を評価
  uv run python -m evaluation.cli --pdf data/ir/森永乳業/S100TPOT.pdf --fiscal-period 2025-03

  # 企業名で EDINET から取得して評価
  uv run python -m evaluation.cli --company 森永乳業 --from 2025-06-01 --to 2025-07-31

  # 評価モデルを切り替え（既定は EVAL_MODEL、未設定なら gemini-2.5-pro）
  uv run python -m evaluation.cli --pdf ... --fiscal-period 2025-03 --model claude-opus-5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import EvaluationInput, evaluate_company, evaluate_document
from .report import render_markdown, save_report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="企業IRの投資適格性を評価する")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", type=Path, help="評価する有報 PDF のパス")
    src.add_argument("--company", help="企業名（EDINET から有報を取得）")

    p.add_argument("--fiscal-period", help="決算期 YYYY-MM（--pdf 時は必須、--company 時は任意）")
    p.add_argument("--from", dest="date_from", help="--company 時: 検索開始日 YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", help="--company 時: 検索終了日 YYYY-MM-DD")

    p.add_argument("--model", help="評価モデル（例: gemini-3.1-pro-preview, claude-opus-5, gpt-4o）")
    p.add_argument("--industry-model", help="業種判定に使う軽量モデル（既定: INDUSTRY_MODEL / gemini-flash-latest）")
    p.add_argument("--n-evidence", type=int, default=14, help="審査辞典から引く証拠チャンク数")
    p.add_argument("--out", type=Path, default=Path("data/reports"), help="レポート保存先ディレクトリ")
    p.add_argument("--no-evidence", action="store_true", help="レポート付録の辞典チャンクを省く")
    p.add_argument("--json", action="store_true", help="構造化結果(JSON)も標準出力へ")
    p.add_argument("--stdout", action="store_true", help="レポートをファイル保存せず標準出力へ")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.pdf:
        if not args.fiscal_period:
            print("--pdf 使用時は --fiscal-period が必須です", file=sys.stderr)
            return 2
        if not args.pdf.exists():
            print(f"PDF が見つかりません: {args.pdf}", file=sys.stderr)
            return 2
        bundle = evaluate_document(
            EvaluationInput(
                pdf_path=args.pdf,
                fiscal_period=args.fiscal_period,
                model=args.model,
                industry_model=args.industry_model,
                n_evidence=args.n_evidence,
            )
        )
    else:
        if not (args.date_from and args.date_to):
            print("--company 使用時は --from と --to が必須です", file=sys.stderr)
            return 2
        bundle = evaluate_company(
            args.company,
            args.date_from,
            args.date_to,
            fiscal_period=args.fiscal_period,
            model=args.model,
            industry_model=args.industry_model,
            n_evidence=args.n_evidence,
        )

    include_evidence = not args.no_evidence
    markdown = render_markdown(bundle, include_evidence=include_evidence)

    if args.stdout:
        print(markdown)
    else:
        path = save_report(bundle, directory=args.out, include_evidence=include_evidence)
        print(f"レポート保存: {path}", file=sys.stderr)

    if args.json:
        print(bundle.result.to_json())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
