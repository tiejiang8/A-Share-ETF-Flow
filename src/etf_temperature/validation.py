from __future__ import annotations

import numpy as np
import pandas as pd

from .anchors import validate_core_anchors
from .classification import validate_membership
from .classification_quality import classification_coverage
from .config import load_temperature_config
from .narrative import composite_one_sentence, validate_narrative
from .official_endpoints import normalize_date
from .quote_freshness import PRECLOSE_QUOTE_STATUS


def _classification_metrics(etf_daily: pd.DataFrame) -> dict:
    return classification_coverage(etf_daily)


def _extreme_flow_metrics(etf_daily: pd.DataFrame) -> dict:
    if etf_daily.empty:
        return {"extreme_flow_trigger_count": 0, "extreme_flow_unresolved_count": 0}
    size = pd.to_numeric(etf_daily.get("fund_size_100m"), errors="coerce").replace(0, pd.NA)
    flow1_ratio = pd.to_numeric(etf_daily.get("estimated_net_subscription_100m"), errors="coerce") / size
    flow5_ratio = pd.to_numeric(etf_daily.get("estimated_net_subscription_5d_100m"), errors="coerce") / size
    share1 = pd.to_numeric(etf_daily.get("share_daily_change_pct"), errors="coerce")
    share5 = pd.to_numeric(etf_daily.get("share_5d_change_pct"), errors="coerce")
    trigger = flow1_ratio.abs().gt(0.05) | flow5_ratio.abs().gt(0.05) | share1.abs().gt(0.20) | share5.abs().gt(0.30)
    status = etf_daily.get("flow_validation_status", pd.Series("ok", index=etf_daily.index)).fillna("ok")
    return {
        "extreme_flow_trigger_count": int(trigger.sum()),
        "extreme_flow_unresolved_count": int((trigger & status.eq("unresolved")).sum()),
    }


def _field_freshness(etf_daily: pd.DataFrame) -> dict:
    if etf_daily.empty:
        return {
            "quote_fresh_coverage": 0.0,
            "share_fresh_coverage": 0.0,
            "nav_fresh_coverage": 0.0,
            "stale_quote_count": 0,
            "stale_share_count": 0,
            "stale_nav_count": 0,
            "max_nav_lag_days": None,
        }

    target = etf_daily.get("date", pd.Series(None, index=etf_daily.index)).map(normalize_date)
    quote_date = etf_daily.get("quote_data_date", pd.Series(None, index=etf_daily.index)).map(normalize_date)
    share_date = etf_daily.get("share_data_date", pd.Series(None, index=etf_daily.index)).map(normalize_date)
    nav_date = etf_daily.get("nav_data_date", pd.Series(None, index=etf_daily.index)).map(normalize_date)
    quote_present = etf_daily.get("close", pd.Series(np.nan, index=etf_daily.index)).notna()
    share_present = etf_daily.get("latest_shares", pd.Series(np.nan, index=etf_daily.index)).notna()
    nav_present = etf_daily.get("nav", pd.Series(np.nan, index=etf_daily.index)).notna()
    max_nav_lag = int(load_temperature_config()["thresholds"].get("daily_max_nav_lag_days", 0))

    quote_fresh = quote_present & quote_date.eq(target)
    share_fresh = share_present & share_date.eq(target)
    target_dt = pd.to_datetime(target, errors="coerce")
    nav_dt = pd.to_datetime(nav_date, errors="coerce")
    nav_lag = (target_dt - nav_dt).dt.days
    nav_fresh = nav_present & nav_lag.ge(0) & nav_lag.le(max_nav_lag)
    count = max(len(etf_daily), 1)
    valid_nav_lag = nav_lag.loc[nav_present & nav_lag.notna()]
    return {
        "quote_fresh_coverage": float(quote_fresh.sum() / count),
        "share_fresh_coverage": float(share_fresh.sum() / count),
        "nav_fresh_coverage": float(nav_fresh.sum() / count),
        "stale_quote_count": int((quote_present & ~quote_fresh).sum()),
        "stale_share_count": int((share_present & ~share_fresh).sum()),
        "stale_nav_count": int((nav_present & ~nav_fresh).sum()),
        "max_nav_lag_days": int(valid_nav_lag.max()) if not valid_nav_lag.empty else None,
    }


def coverage_metrics(
    universe: pd.DataFrame,
    etf_daily: pd.DataFrame,
    membership: pd.DataFrame,
    *,
    cluster_daily: pd.DataFrame | None = None,
    flow_audit: pd.DataFrame | None = None,
) -> dict:
    a_share_count = int(universe["is_a_share_equity_etf"].sum()) if "is_a_share_equity_etf" in universe else len(universe)
    if etf_daily.empty or a_share_count == 0:
        return {
            "a_share_equity_etf_count": a_share_count,
            "quote_coverage": 0.0,
            "share_coverage": 0.0,
            "nav_coverage": 0.0,
            "quote_fresh_coverage": 0.0,
            "share_fresh_coverage": 0.0,
            "nav_fresh_coverage": 0.0,
            "weighted_share_coverage": 0.0,
            "flow_member_coverage": 0.0,
            "flow_size_coverage": 0.0,
            "missing_flow_size_100m": 0.0,
            "official_ratio": 0.0,
            "browser_fallback_count": 0,
            "third_party_fallback_count": 0,
            "date_mismatch_count": 0,
            "corporate_action_exception_count": 0,
            "core_anchor_total": 0,
            "core_anchor_valid_count": 0,
            "core_anchor_coverage": 0.0,
            "classified_count_coverage": 0.0,
            "classified_scale_coverage": 0.0,
            "classified_turnover_coverage": 0.0,
            "classified_flow_coverage": 0.0,
            "important_unclassified_count": 0,
            "extreme_flow_trigger_count": 0,
            "extreme_flow_unresolved_count": 0,
            "missing_quote_codes": [],
            "missing_share_codes": [],
            "missing_nav_codes": [],
            "narrative_consistency_status": False,
            "formal_blockers": ["无可用ETF数据"],
            "formal_report_allowed": False,
        }
    quote_ok = etf_daily["close"].notna()
    share_ok = etf_daily["latest_shares"].notna()
    nav_ok = etf_daily["nav"].notna()
    weights = pd.to_numeric(etf_daily["fund_size_100m"], errors="coerce").fillna(0)
    weighted_share = weights[share_ok].sum() / weights.sum() if weights.sum() else share_ok.mean()
    flow_ok = pd.to_numeric(etf_daily.get("estimated_net_subscription_100m"), errors="coerce").notna()
    flow_valid_size = float(weights[flow_ok].sum())
    total_size = float(weights.sum())
    source_text = etf_daily.get("source", pd.Series("", index=etf_daily.index)).fillna("")
    quote_source = etf_daily.get("quote_source", pd.Series("", index=etf_daily.index)).fillna("")
    quote_status = etf_daily.get("quote_validation_status", pd.Series("", index=etf_daily.index))
    unsettled_codes = etf_daily.loc[quote_status.eq(PRECLOSE_QUOTE_STATUS), "etf_code"].astype(str).tolist()
    share_source = etf_daily.get("share_source", pd.Series("", index=etf_daily.index)).fillna("")
    nav_source = etf_daily.get("nav_source", pd.Series("", index=etf_daily.index)).fillna("")
    official_pattern = r"official|cninfo_nsdk|csrc_eid|fund_company"
    official = (
        quote_source.str.contains(official_pattern, case=False, regex=True)
        & share_source.str.contains(official_pattern, case=False, regex=True)
        & nav_source.str.contains(official_pattern, case=False, regex=True)
    )
    share_daily_change_pct = pd.to_numeric(etf_daily.get("share_daily_change_pct", pd.Series(dtype=float)), errors="coerce")
    metrics = {
        "a_share_equity_etf_count": a_share_count,
        "quote_success_count": int(quote_ok.sum()),
        "share_success_count": int(share_ok.sum()),
        "nav_success_count": int(nav_ok.sum()),
        "quote_coverage": float(quote_ok.sum() / a_share_count),
        "share_coverage": float(share_ok.sum() / a_share_count),
        "nav_coverage": float(nav_ok.sum() / a_share_count),
        "weighted_share_coverage": float(weighted_share),
        "flow_member_coverage": float(flow_ok.mean()) if len(flow_ok) else 0.0,
        "flow_size_coverage": flow_valid_size / total_size if total_size else 0.0,
        "missing_flow_size_100m": total_size - flow_valid_size,
        "official_ratio": float(official.mean()) if len(official) else 0.0,
        "browser_fallback_count": int(quote_source.str.contains("browser_fallback|cninfo_nsdk", case=False, regex=True).sum()),
        "third_party_fallback_count": int(source_text.str.contains("third_party", case=False, regex=False).sum()),
        "date_mismatch_count": int(
            ((etf_daily.get("quote_data_date") != etf_daily.get("date")) & etf_daily.get("quote_data_date").notna()).sum()
        )
        if "quote_data_date" in etf_daily
        else 0,
        "corporate_action_exception_count": int((share_daily_change_pct.abs() > 0.20).sum()),
        "missing_quote_codes": etf_daily.loc[~quote_ok, "etf_code"].astype(str).head(50).tolist(),
        "missing_share_codes": etf_daily.loc[~share_ok, "etf_code"].astype(str).head(50).tolist(),
        "missing_nav_codes": etf_daily.loc[~nav_ok, "etf_code"].astype(str).head(50).tolist(),
    }
    if unsettled_codes:
        metrics["unsettled_quote_codes"] = unsettled_codes
    metrics.update(_field_freshness(etf_daily))
    _, anchor_metrics = validate_core_anchors(universe, membership, etf_daily)
    metrics.update(anchor_metrics)
    metrics.update(_classification_metrics(etf_daily))
    if flow_audit is None:
        metrics.update(_extreme_flow_metrics(etf_daily))
    else:
        status = flow_audit.get("flow_validation_status", pd.Series(dtype=str)).fillna("")
        metrics.update(
            {
                "extreme_flow_trigger_count": int(len(flow_audit)),
                "extreme_flow_unresolved_count": int(status.eq("unresolved").sum()),
            }
        )
    cluster_for_narrative = cluster_daily if cluster_daily is not None else pd.DataFrame()
    narrative = composite_one_sentence(cluster_for_narrative)
    metrics.update(validate_narrative(narrative, cluster_for_narrative))
    cluster_ok = validate_membership(membership)["is_valid"]
    thresholds = load_temperature_config()["thresholds"]
    checks = {
        "观察集群不完整": cluster_ok,
        "核心锚点不完整": metrics["core_anchor_coverage"] == 1.0,
        "存在重要未分类ETF": metrics["important_unclassified_count"] == 0,
        "分类规模覆盖率不足": metrics["classified_scale_coverage"] >= 0.95,
        "分类成交额覆盖率不足": metrics["classified_turnover_coverage"] >= 0.90,
        "分类资金流覆盖率不足": metrics["classified_flow_coverage"] >= 0.90,
        "行情覆盖率不足": metrics["quote_coverage"] >= float(thresholds.get("daily_min_quote_coverage", 0.98)),
        "行情时点不新鲜": metrics["quote_fresh_coverage"] >= float(thresholds.get("daily_min_quote_coverage", 0.98)),
        "存在未补齐的收盘前行情": not unsettled_codes,
        "份额覆盖率不足": metrics["share_coverage"] >= float(thresholds.get("daily_min_share_coverage", 0.98)),
        "份额时点不新鲜": metrics["share_fresh_coverage"] >= float(thresholds.get("daily_min_share_coverage", 0.98)),
        "净值覆盖率不足": metrics["nav_coverage"] >= float(thresholds.get("daily_min_nav_coverage", 0.95)),
        "净值时点不新鲜": metrics["nav_fresh_coverage"] >= float(thresholds.get("daily_min_nav_fresh_coverage", 0.95)),
        "资金流有效规模覆盖率不足": metrics["flow_size_coverage"] >= float(thresholds.get("daily_min_flow_size_coverage", 0.95)),
        "存在未解决极端资金流": metrics["extreme_flow_unresolved_count"] == 0,
        "报告叙事与数据不一致": metrics["narrative_consistency_status"],
    }
    metrics["formal_blockers"] = [label for label, passed in checks.items() if not passed]
    metrics["formal_report_allowed"] = not metrics["formal_blockers"]
    return metrics


def validation_markdown(metrics: dict, membership_validation: dict) -> str:
    lines = ["# 数据验证", ""]
    lines.append("## 覆盖率")
    for key, value in metrics.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## 集群")
    for key, value in membership_validation.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"
