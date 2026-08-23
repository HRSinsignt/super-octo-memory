"""
Command-line entry point.

Usage:
    python -m jse_platform.cli --data-dir sample_data --format text
    python -m jse_platform.cli --data-dir sample_data --format json --out results.json
    python -m jse_platform.cli --data-dir sample_data --ticker NCBFG
"""

from __future__ import annotations

import argparse
import sys

from .data_sources.csv_source import CSVDataSource
from .pipeline import run_pipeline, score_company
from .reporting import to_json, to_text_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JSE Investment Horizon scoring pipeline")
    parser.add_argument("--data-dir", required=True, help="Directory containing financials.csv, prices.csv, index_prices.csv")
    parser.add_argument("--ticker", help="Score a single ticker instead of the whole universe")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--out", help="Write output to this file instead of stdout")
    args = parser.parse_args(argv)

    source = CSVDataSource(args.data_dir)

    if args.ticker:
        company = source.get_company_financials(args.ticker)
        result = score_company(company)
        results = [result]
    else:
        results = run_pipeline(source)

    if args.format == "json":
        output = to_json(results)
    else:
        output = "\n\n".join(to_text_summary(r) for r in results)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Wrote {len(results)} result(s) to {args.out}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
