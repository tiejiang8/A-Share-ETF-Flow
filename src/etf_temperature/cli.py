from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

from .official_endpoints import NORMALIZED_DIR, RAW_DIR
from .audit import audit_extreme_flows, write_audit_outputs
from .analysis_backfill import backfill_market_data
from .pipeline import backfill_nav_history, make_summary, probe_official, rebuild_history, rebuild_universe, run_pipeline, validate_anchors, validate_clusters, validate_run
from .weekly import run_weekly, validate_weekly


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="python -m etf_temperature.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe-official")
    sub.add_parser("rebuild-universe")
    sub.add_parser("validate-clusters")
    anchor_p = sub.add_parser("validate-anchors")
    anchor_p.add_argument("--date", default=None)

    audit_p = sub.add_parser("audit-flows")
    audit_p.add_argument("--date", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--date", default="auto", help="auto or YYYYMMDD/YYYY-MM-DD")
    run_p.add_argument("--refresh", action="store_true", help="force official refresh even when a local snapshot exists")
    run_p.add_argument("--full-refresh", action="store_true", help="ignore source caches and use the full official lookback window")
    run_p.add_argument(
        "--refresh-recent-days",
        type=int,
        default=None,
        help="recent trading-day window to refresh; implies --refresh",
    )

    val_p = sub.add_parser("validate")
    val_p.add_argument("--date", required=True)

    summary_p = sub.add_parser("make-summary", help="从本地缓存重建 AI 可读摘要（summary.json / llms.txt / snapshot.md），不联网")
    summary_p.add_argument("--date", required=True, help="YYYY-MM-DD")

    nav_backfill_p = sub.add_parser("backfill-nav")
    nav_backfill_p.add_argument("--start", required=True)
    nav_backfill_p.add_argument("--end", required=True)

    market_backfill_p = sub.add_parser("backfill-market-data")
    market_backfill_p.add_argument("--start", required=True)
    market_backfill_p.add_argument("--end", required=True)
    market_backfill_p.add_argument("--warmup-days", type=int, default=45)
    market_backfill_p.add_argument("--quote-batch-size", type=int, default=100)
    market_backfill_p.add_argument("--date-chunk-size", type=int, default=20)
    market_backfill_p.add_argument(
        "--skip-szse-shares",
        action="store_true",
        help="skip the currently unavailable SZSE historical-share endpoint while completing other sources",
    )
    market_backfill_p.add_argument(
        "--skip-fund-company-aum",
        action="store_true",
        help="skip official fund-company AUM/NAV collection",
    )

    rebuild_p = sub.add_parser("rebuild")
    rebuild_p.add_argument("--start", required=True)
    rebuild_p.add_argument("--end", required=True)
    rebuild_p.add_argument(
        "--recompute",
        action="store_true",
        help="recompute daily snapshots from local market-data cache without official prefetch",
    )

    weekly_p = sub.add_parser("run-weekly")
    weekly_p.add_argument("--week", default="auto", help="auto or YYYY-Www")

    weekly_val_p = sub.add_parser("validate-weekly")
    weekly_val_p.add_argument("--week", default="auto", help="auto or YYYY-Www")

    args = parser.parse_args(argv)
    if args.command == "probe-official":
        _print_json(probe_official())
        return 0
    if args.command == "rebuild-universe":
        universe, membership = rebuild_universe()
        _print_json({"etf_universe_rows": len(universe), "membership_rows": len(membership)})
        return 0
    if args.command == "validate-clusters":
        result = validate_clusters()
        _print_json(result)
        return 0 if result.get("is_valid") else 2
    if args.command == "validate-anchors":
        result = validate_anchors(args.date)
        _print_json(result)
        return 0 if result.get("core_anchor_valid") else 1
    if args.command == "audit-flows":
        outputs = write_audit_outputs(args.date)
        _print_json(outputs)
        from .snapshot import SnapshotStore

        snapshot = SnapshotStore().load(args.date)
        extreme = audit_extreme_flows(snapshot.etf_daily, snapshot.cluster_daily)
        unresolved = 0
        if not extreme.empty and "flow_validation_status" in extreme:
            unresolved = int((extreme["flow_validation_status"] == "unresolved").sum())
        return 0 if unresolved == 0 else 1
    if args.command == "run":
        if args.full_refresh and args.refresh_recent_days is not None:
            parser.error("--full-refresh cannot be used with --refresh-recent-days")
        refresh_recent_days = args.refresh_recent_days if args.refresh_recent_days is not None else 2
        refresh_requested = bool(args.refresh or args.full_refresh or args.refresh_recent_days is not None)
        result = run_pipeline(
            args.date,
            refresh=refresh_requested,
            refresh_recent_days=refresh_recent_days,
            full_refresh=args.full_refresh,
        )
        _print_json(
            {
                "report_date": result.report_date,
                "report_path": str(result.report_path),
                "chart_dir": str(result.chart_dir),
                "raw_data_path": str(RAW_DIR),
                "normalized_data_path": str(NORMALIZED_DIR),
                "used_cache": result.used_cache,
                "cache_reason": result.cache_reason,
                "metrics": result.metrics,
                "unresolved_issues": result.unresolved_issues,
            }
        )
        return 0 if result.metrics.get("formal_report_allowed") else 2
    if args.command == "validate":
        result = validate_run(args.date)
        _print_json(result)
        return 0 if result.get("formal_report_allowed") else 2
    if args.command == "make-summary":
        result = make_summary(args.date)
        _print_json(result)
        return 0
    if args.command == "backfill-nav":
        result = backfill_nav_history(args.start, args.end)
        _print_json(result)
        return 0 if result.get("after", {}).get("ready") else 2
    if args.command == "backfill-market-data":
        result = backfill_market_data(
            args.start,
            args.end,
            warmup_days=args.warmup_days,
            quote_batch_size=args.quote_batch_size,
            date_chunk_size=args.date_chunk_size,
            skip_szse_shares=args.skip_szse_shares,
            skip_fund_company_aum=args.skip_fund_company_aum,
        )
        _print_json(result)
        return 0 if result.get("market_data_ready") else 2
    if args.command == "rebuild":
        results = rebuild_history(args.start, args.end, recompute=args.recompute)
        formal_by_date = {
            result.report_date: bool(result.metrics.get("formal_report_allowed"))
            for result in results
        }
        _print_json(
            {
                "runs": [result.report_date for result in results],
                "formal_report_allowed_by_date": formal_by_date,
            }
        )
        return 0 if results and all(formal_by_date.values()) else 2
    if args.command == "run-weekly":
        result = run_weekly(args.week)
        _print_json(
            {
                "week": result.week,
                "weekly_etf_path": str(result.weekly_etf_path),
                "weekly_cluster_path": str(result.weekly_cluster_path),
                "snapshot_path": str(result.snapshot_path),
                "validation_path": str(result.validation_path),
                "report_path": str(result.report_path) if result.report_path else None,
                "validation": result.validation,
            }
        )
        return 0 if result.validation.get("formal_weekly_allowed") else 1
    if args.command == "validate-weekly":
        result = validate_weekly(args.week)
        _print_json(result)
        return 0 if result.get("formal_weekly_allowed") else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
