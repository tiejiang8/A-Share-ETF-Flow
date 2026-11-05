"""Generate summary artifacts from explicitly fictional records, without HTTP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from etf_temperature.aggregation import build_cluster_daily
from etf_temperature.official_endpoints import ROOT
from etf_temperature.summary import write_summary_artifacts


def run_example(input_path: Path, output_root: Path) -> dict:
    example = json.loads(input_path.read_text(encoding="utf-8"))
    if example.get("synthetic_demo") is not True:
        raise ValueError("The offline example accepts only explicitly synthetic data.")
    date = example["date"]
    etf = pd.DataFrame(example["etfs"])
    etf["date"] = date
    for column in ["quote_data_date", "share_data_date", "nav_data_date"]:
        if column not in etf:
            etf[column] = date
        else:
            etf[column] = etf[column].fillna(date)
    etf.loc[etf["close"].isna(), "quote_data_date"] = None
    etf.loc[etf["latest_shares"].isna(), "share_data_date"] = None
    etf["amount_5d_mean"] = etf["amount"]
    for column in ["pct_change", "return_5d", "return_10d", "premium_discount", "temperature"]:
        etf[column] = 0.0
    etf["flow_validation_status"] = "ok"
    etf.loc[etf["estimated_net_subscription_100m"].isna(), "flow_validation_status"] = "unresolved"
    etf["share_flow_excluded"] = False
    clusters = build_cluster_daily(etf)
    clusters["date"] = date
    count = len(etf)
    total_size = float(etf["fund_size_100m"].sum())
    flow_valid_size = float(etf.loc[etf["estimated_net_subscription_100m"].notna(), "fund_size_100m"].sum())
    metrics = {
        "synthetic_demo": True,
        "a_share_equity_etf_count": count,
        "formal_report_allowed": False,
        "formal_blockers": [],
        "quote_coverage": float(etf["close"].notna().mean()),
        "share_coverage": float(etf["latest_shares"].notna().mean()),
        "nav_coverage": float(etf["nav"].notna().mean()),
        "quote_fresh_coverage": float((etf["close"].notna() & etf["quote_data_date"].eq(date)).mean()),
        "share_fresh_coverage": float((etf["latest_shares"].notna() & etf["share_data_date"].eq(date)).mean()),
        "nav_fresh_coverage": float((etf["nav"].notna() & etf["nav_data_date"].eq(date)).mean()),
        "flow_member_coverage": float(etf["estimated_net_subscription_100m"].notna().mean()),
        "flow_size_coverage": flow_valid_size / total_size,
        "weighted_share_coverage": float(etf.loc[etf["latest_shares"].notna(), "fund_size_100m"].sum()) / total_size,
        "missing_flow_size_100m": total_size - flow_valid_size,
        "official_ratio": 0.0,
        "missing_quote_codes": etf.loc[etf["close"].isna(), "etf_code"].tolist(),
        "missing_share_codes": etf.loc[etf["latest_shares"].isna(), "etf_code"].tolist(),
        "missing_nav_codes": [],
        "stale_nav_count": 1,
        "max_nav_lag_days": 3,
        "extreme_flow_unresolved_count": 0,
        "unclassified_count": int(etf["primary_cluster"].eq("").sum()),
    }
    return write_summary_artifacts(output_root / date, date, etf, clusters, metrics, outputs_dir=output_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1] / "examples/synthetic_input.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/demo")
    args = parser.parse_args()
    result = run_example(args.input, args.output_dir.resolve())
    print(json.dumps({"synthetic_demo": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
