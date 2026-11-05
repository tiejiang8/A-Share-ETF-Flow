from __future__ import annotations

import pandas as pd


def _pct_rank(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() == 0:
        return pd.Series(0.5, index=series.index)
    return values.rank(pct=True).fillna(0.0)


def choose_representatives(etf_daily: pd.DataFrame) -> pd.DataFrame:
    if etf_daily.empty:
        return pd.DataFrame()
    records = []
    for cluster_id, group in etf_daily[etf_daily["primary_cluster"].ne("")].groupby("primary_cluster"):
        group = group.copy()
        group["size_score"] = _pct_rank(group["fund_size_100m"])
        group["liquidity_score"] = _pct_rank(group["amount_5d_mean"])
        group["completeness_score"] = (
            group[["close", "latest_shares", "nav"]].notna().sum(axis=1) / 3
        )
        group["representative_score"] = (
            group["size_score"] * 0.50
            + group["liquidity_score"] * 0.40
            + group["completeness_score"] * 0.10
        )
        selected = group.sort_values(
            ["completeness_score", "representative_score", "fund_size_100m", "amount"],
            ascending=[False, False, False, False],
        ).iloc[0]
        records.append(
            {
                "cluster_id": cluster_id,
                "cluster_name": selected.get("primary_cluster_name"),
                "representative_code": selected["etf_code"],
                "representative_name": selected.get("expanded_name") or selected.get("etf_name"),
                "representative_score": selected["representative_score"],
                "size_score": selected["size_score"],
                "liquidity_score": selected["liquidity_score"],
                "completeness_score": selected["completeness_score"],
                "date": selected.get("date"),
                "source": "derived:representative_policy",
                "source_priority": 1,
                "source_url": "config/cluster_rules.yaml",
                "is_official": False,
                "validation_status": "ok",
            }
        )
    return pd.DataFrame(records)
