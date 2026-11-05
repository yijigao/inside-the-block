from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analyze import run_analysis
from .collect import collect
from .config import load_config
from .runner import run_probe, run_setup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe",
        description="Inside the Block: Solana validator transaction-ordering measurement probe. "
        "Dry-run is the default everywhere; live sending requires --live AND CONFIRM_LIVE=1.",
    )
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--keys-dir", default="keys")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--max-fee-sol", type=float, default=0.05, help="hard spend cap in SOL (default 0.05)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="create probe accounts; fund them (live) or simulate funding (default)")
    p_setup.add_argument("--live", action="store_true")

    p_run = sub.add_parser("run", help="fire one probe batch at the next target leader window (live) or simulate it (default dry-run)")
    p_run.add_argument("--live", action="store_true")
    p_run.add_argument("--batch-id", default=None)

    sub.add_parser("collect", help="locate landed probe signatures and build data/summary.{json,csv}")
    sub.add_parser("analyze", help="compute ordering statistics into data/analysis.json and data/tables.md")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    keys_dir = Path(args.keys_dir)
    data_dir = Path(args.data_dir)

    if args.command == "setup":
        if args.live and os.environ.get("CONFIRM_LIVE") != "1":
            print("refusing live setup: set CONFIRM_LIVE=1 as well as --live", file=sys.stderr)
            return 2
        result = run_setup(cfg, keys_dir, data_dir, live=args.live)
        print(json.dumps(result, indent=1))
        return 0
    if args.command == "run":
        records = run_probe(cfg, keys_dir, data_dir, live=args.live, max_fee_sol=args.max_fee_sol, batch_id=args.batch_id)
        print(json.dumps([r.__dict__ for r in records], indent=1, default=str))
        return 0
    if args.command == "collect":
        summary = collect(cfg.rpc_url, data_dir)
        print(f"collected {len(summary)} live probe records")
        return 0
    if args.command == "analyze":
        analysis = run_analysis(data_dir)
        print(json.dumps(analysis["overall"], indent=1))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
