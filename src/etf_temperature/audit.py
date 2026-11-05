from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .anchors import validate_core_anchors, write_core_anchor_audit
from .classification_quality import classification_coverage, classify_unclassified_importance
from .config import load_temperature_config
from .official_endpoints import CONFIG_DIR, NORMALIZED_DIR, OUTPUTS_DIR, normalize_date, read_dataframe
from .validation import coverage_metrics


AUDIT_DIR = OUTPUTS_DIR / "audit"


def load_official_suspension_exceptions(path: Path | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """Load date-bound official suspension evidence used for flow reconciliation."""

    config_path = path or CONFIG_DIR / "official_suspensions.yaml"
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    records = payload.get("suspensions") or []
    exceptions: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        code = str(record.get("etf_code") or "").strip().zfill(6)
        date_text = normalize_date(record.get("date"))
        source_url = str(record.get("source_url") or "").strip()
        if code.strip("0") and date_text and source_url:
            exceptions[(code, date_text)] = dict(record)
    return exceptions


def audit_extreme_flows(etf_daily: pd.DataFrame, cluster_daily: pd.DataFrame | None = None) -> pd.DataFrame:
    if etf_daily.empty:
        return pd.DataFrame()
    df = etf_daily.copy()
    size = pd.to_numeric(df.get("fund_size_100m"), errors="coerce").replace(0, np.nan)
    flow1 = pd.to_numeric(df.get("estimated_net_subscription_100m"), errors="coerce")
    flow5 = pd.to_numeric(df.get("estimated_net_subscription_5d_100m"), errors="coerce")
    if "raw_estimated_net_subscription_100m" in df:
        flow1 = flow1.where(flow1.notna(), pd.to_numeric(df["raw_estimated_net_subscription_100m"], errors="coerce"))
    if "raw_estimated_net_subscription_5d_100m" in df:
        flow5 = flow5.where(flow5.notna(), pd.to_numeric(df["raw_estimated_net_subscription_5d_100m"], errors="coerce"))
    share1 = pd.to_numeric(df.get("share_daily_change_pct"), errors="coerce")
    share5 = pd.to_numeric(df.get("share_5d_change_pct"), errors="coerce")
    df["one_day_flow_to_size"] = flow1 / size
    df["five_day_flow_to_size"] = flow5 / size
    triggered = (
        df["one_day_flow_to_size"].abs().gt(0.05)
        | df["five_day_flow_to_size"].abs().gt(0.05)
        | share1.abs().gt(0.20)
        | share5.abs().gt(0.30)
        | df.get("share_flow_excluded", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    )
    out = df[triggered].copy()
    if out.empty:
        return out
    target_date = out.get("date", pd.Series(None, index=out.index)).map(normalize_date)
    quote_date = out.get("quote_data_date", pd.Series(None, index=out.index)).map(normalize_date)
    share_date = out.get("share_data_date", pd.Series(None, index=out.index)).map(normalize_date)
    nav_date = out.get("nav_data_date", pd.Series(None, index=out.index)).map(normalize_date)
    nav_lag = (pd.to_datetime(target_date, errors="coerce") - pd.to_datetime(nav_date, errors="coerce")).dt.days
    max_nav_lag = int(load_temperature_config()["thresholds"].get("daily_max_nav_lag_days", 0))
    fields_complete = out[["close", "latest_shares", "nav"]].notna().all(axis=1)
    dates_fresh = quote_date.eq(target_date) & share_date.eq(target_date) & nav_lag.ge(0) & nav_lag.le(max_nav_lag)
    share_nav_complete = out[["latest_shares", "nav"]].notna().all(axis=1)
    share_nav_fresh = share_date.eq(target_date) & nav_lag.ge(0) & nav_lag.le(max_nav_lag)
    suspension_exceptions = load_official_suspension_exceptions()
    exception_records = [
        suspension_exceptions.get((str(code).strip().zfill(6), date_text))
        for code, date_text in zip(out["etf_code"], target_date)
    ]
    official_suspension = pd.Series(
        [record is not None for record in exception_records],
        index=out.index,
        dtype=bool,
    ) & out["close"].isna()
    out["suspension_source_url"] = [
        str(record.get("source_url") or "") if record else ""
        for record in exception_records
    ]
    previously_unresolved = out.get("flow_validation_status", pd.Series("", index=out.index)).fillna("").isin(
        ["unresolved", "stale_source_excluded"]
    )
    # 恒等式：当日份额变动为 0 时，净申购 = Δ份额 × 净值 = 0，与净值取哪个日期无关。
    # 仅用于覆盖「净值晚一个交易日才发布」这类发布滞后；净值长期不更新（已终止/休眠标的）
    # 不属于发布滞后，不适用此恒等式救援，否则会把死数据拉回已解决状态。
    if "share_daily_change" in out:
        share_change = pd.to_numeric(out["share_daily_change"], errors="coerce")
    else:
        share_change = pd.Series(np.nan, index=out.index)
    zero_share_change = share_change.notna() & share_change.eq(0)
    # 恒等式只依赖「份额 × 净值」，与收盘价无关：当日无成交（行情源没有 K 线）时，
    # 只要官方份额与净值都在目标日且份额零变动，净申购同样恒为 0，不应判为未解决。
    zero_share_change_resolved = (
        zero_share_change
        & share_nav_complete
        & share_date.eq(target_date)
        & nav_lag.ge(0)
        & nav_lag.le(max_nav_lag + 1)
    )
    out["flow_validation_status"] = np.select(
        [
            out.get("share_flow_excluded", pd.Series(False, index=out.index)).fillna(False).astype(bool),
            official_suspension & share_nav_complete & share_nav_fresh,
            fields_complete & dates_fresh & ~previously_unresolved,
            # 兜底：仅当净值时点已导致无法常规对账时，才用「份额零变动 ⇒ 净申购为零」这条恒等式救援。
            zero_share_change_resolved,
        ],
        [
            "excluded_corporate_action",
            "resolved_official_suspension",
            "resolved_by_source_reconciliation",
            "resolved_zero_share_change",
        ],
        default="unresolved",
    )
    out["reconciliation_note"] = np.select(
        [
            out["flow_validation_status"].eq("excluded_corporate_action"),
            out["flow_validation_status"].eq("resolved_by_source_reconciliation"),
            out["flow_validation_status"].eq("resolved_zero_share_change"),
        ],
        [
            "份额异常无法确认，资金流已在聚合前剔除。",
            "行情、份额和净值字段完整且时点一致；保留估算资金流，并在审计表中提示极端比例。",
            "当日份额无变化，净申购按恒等式取 0，净值日期不影响该读数。",
        ],
        default="行情、份额或净值存在缺口/时点过期，极端资金流未解决。",
    )
    out.loc[
        out["flow_validation_status"].eq("resolved_official_suspension"),
        "reconciliation_note",
    ] = "官方公告确认当日停牌；资金流使用同日官方份额与净值完成对账，不虚构停牌日成交价。"
    out.loc[
        out["flow_validation_status"].eq("resolved_zero_share_change")
        & out.get("close", pd.Series(np.nan, index=out.index)).isna(),
        "reconciliation_note",
    ] = "当日无成交行情且份额无变化，净申购按恒等式取 0；该读数只依赖份额与净值，不依赖收盘价。"
    cols = [
        "date",
        "etf_code",
        "etf_name",
        "primary_cluster",
        "primary_cluster_name",
        "fund_size_100m",
        "estimated_net_subscription_100m",
        "raw_estimated_net_subscription_100m",
        "one_day_flow_to_size",
        "estimated_net_subscription_5d_100m",
        "raw_estimated_net_subscription_5d_100m",
        "five_day_flow_to_size",
        "share_daily_change_pct",
        "share_5d_change_pct",
        "flow_validation_status",
        "reconciliation_note",
        "suspension_source_url",
    ]
    return out[[col for col in cols if col in out.columns]].sort_values(
        ["flow_validation_status", "five_day_flow_to_size"],
        ascending=[True, False],
    )


def audit_cluster_concentration(etf_daily: pd.DataFrame) -> pd.DataFrame:
    if etf_daily.empty or "primary_cluster" not in etf_daily:
        return pd.DataFrame()
    records = []
    for cluster_id, group in etf_daily[etf_daily["primary_cluster"].fillna("").ne("")].groupby("primary_cluster"):
        for column in ["estimated_net_subscription_100m", "estimated_net_subscription_5d_100m"]:
            flows = pd.to_numeric(group[column], errors="coerce")
            denom = flows.abs().sum()
            if not denom or pd.isna(denom):
                continue
            idx = flows.abs().idxmax()
            records.append(
                {
                    "cluster_id": cluster_id,
                    "cluster_name": group.loc[idx, "primary_cluster_name"],
                    "metric": column,
                    "max_contributor_code": group.loc[idx, "etf_code"],
                    "max_contributor_name": group.loc[idx, "etf_name"],
                    "max_contributor_flow_100m": flows.loc[idx],
                    "max_contributor_abs_ratio": abs(flows.loc[idx]) / denom,
                    "flow_validation_status": "triggered" if abs(flows.loc[idx]) / denom > 0.5 else "ok",
                }
            )
    return pd.DataFrame(records)


def write_audit_outputs(report_date: str | None = None) -> dict[str, str]:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    if report_date:
        from .snapshot import SnapshotStore

        snapshot = SnapshotStore().load(report_date)
        etf_daily = snapshot.etf_daily
        cluster_daily = snapshot.cluster_daily
        membership = snapshot.membership()
        universe = etf_daily.copy()
        universe["is_a_share_equity_etf"] = True
    else:
        universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
        membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
        etf_daily = read_dataframe(NORMALIZED_DIR / "etf_daily.parquet")
        cluster_daily = read_dataframe(NORMALIZED_DIR / "cluster_daily.parquet")

    anchor_audit, anchor_metrics = validate_core_anchors(universe, membership, etf_daily)
    core_anchor_path = write_core_anchor_audit(anchor_audit)

    membership_path = AUDIT_DIR / "cluster_membership_audit.csv"
    membership.to_csv(membership_path, index=False, encoding="utf-8-sig")

    unclassified = classify_unclassified_importance(etf_daily)
    unclassified_path = AUDIT_DIR / "unclassified_etf_audit.csv"
    unclassified.to_csv(unclassified_path, index=False, encoding="utf-8-sig")

    extreme = audit_extreme_flows(etf_daily, cluster_daily)
    extreme_path = AUDIT_DIR / "extreme_flow_audit.csv"
    extreme.to_csv(extreme_path, index=False, encoding="utf-8-sig")

    universe_review = universe.copy()
    if not universe_review.empty:
        universe_review = universe_review.rename(
            columns={
                "etf_code": "ETF代码",
                "etf_name": "ETF名称",
                "tracking_index_name": "跟踪指数",
                "is_a_share_equity_etf": "当前是否纳入",
                "exclusion_reason": "判断依据",
                "source": "数据来源",
            }
        )
        universe_review["建议是否纳入"] = universe_review["当前是否纳入"]
    universe_review_path = AUDIT_DIR / "universe_exclusion_review.csv"
    universe_review.to_csv(universe_review_path, index=False, encoding="utf-8-sig")

    concentration = audit_cluster_concentration(etf_daily)
    unresolved_count = int((extreme.get("flow_validation_status", pd.Series(dtype=str)) == "unresolved").sum()) if not extreme.empty else 0
    reconciliation_path = AUDIT_DIR / "extreme_flow_reconciliation.md"
    reconciliation_path.write_text(_flow_reconciliation_markdown(extreme, concentration), encoding="utf-8")

    metrics = coverage_metrics(
        universe,
        etf_daily,
        membership,
        cluster_daily=cluster_daily,
        flow_audit=extreme,
    )
    metrics.update(anchor_metrics)
    metrics.update(classification_coverage(etf_daily))
    current_report_path = AUDIT_DIR / "current_report_audit.md"
    current_report_path.write_text(_current_report_markdown(metrics, membership, unclassified, extreme, unresolved_count), encoding="utf-8")
    return {
        "current_report_audit": str(current_report_path),
        "cluster_membership_audit": str(membership_path),
        "core_anchor_audit": str(core_anchor_path),
        "extreme_flow_audit": str(extreme_path),
        "unclassified_etf_audit": str(unclassified_path),
        "universe_exclusion_review": str(universe_review_path),
        "extreme_flow_reconciliation": str(reconciliation_path),
    }


def _current_report_markdown(
    metrics: dict[str, Any],
    membership: pd.DataFrame,
    unclassified: pd.DataFrame,
    extreme: pd.DataFrame,
    unresolved_count: int,
) -> str:
    lines = [
        "# 当前日报审计",
        "",
        f"- A股权益ETF数量: {metrics.get('a_share_equity_etf_count', 0)}",
        f"- 未分类ETF数量: {metrics.get('unclassified_count', 0)}",
        f"- 重要未分类ETF数量: {metrics.get('important_unclassified_count', 0)}",
        f"- 分类规模覆盖率: {metrics.get('classified_scale_coverage', 0):.2%}",
        f"- 分类成交额覆盖率: {metrics.get('classified_turnover_coverage', 0):.2%}",
        f"- 分类资金流覆盖率: {metrics.get('classified_flow_coverage', 0):.2%}",
        f"- 核心锚点覆盖率: {metrics.get('core_anchor_coverage', 0):.2%}",
        f"- 极端资金流触发数量: {len(extreme)}",
        f"- 极端资金流未解决数量: {unresolved_count}",
        "",
        "## 未分类原因",
        "",
        "未分类主要来自宽基以外的细分行业、指数名缩写、深市拟合指数名称缺失，以及历史规则过度保守。新版规则只把可确定映射到固定观察集群的产品纳入主集群，剩余产品进入合理未分类或疑似漏分类审计。",
        "",
        "## 锚点和误分类",
        "",
        f"- 512070 已通过人工覆盖归入券商/证券，避免因指数名包含沪深300误归 C02。",
        f"- 核心锚点缺失代码: {metrics.get('core_anchor_missing_codes', [])}",
        f"- 核心锚点数据缺口代码: {metrics.get('core_anchor_missing_data_codes', [])}",
        "",
        "## 未分类样例",
        "",
    ]
    if unclassified.empty:
        lines.append("无未分类ETF。")
    else:
        cols = ["etf_code", "etf_name", "tracking_index_name", "fund_size_100m", "amount", "unclassified_bucket"]
        lines.append(unclassified[[col for col in cols if col in unclassified.columns]].head(30).to_markdown(index=False))
    return "\n".join(lines) + "\n"


def _flow_reconciliation_markdown(extreme: pd.DataFrame, concentration: pd.DataFrame) -> str:
    lines = ["# 极端资金流对账", ""]
    if extreme.empty:
        lines.append("未触发极端资金流二次审计。")
    else:
        status_counts = extreme["flow_validation_status"].value_counts(dropna=False).to_dict()
        lines.append(f"- 状态分布: {status_counts}")
        lines.append("")
        cols = [
            "etf_code",
            "etf_name",
            "primary_cluster_name",
            "estimated_net_subscription_100m",
            "one_day_flow_to_size",
            "estimated_net_subscription_5d_100m",
            "five_day_flow_to_size",
            "flow_validation_status",
            "reconciliation_note",
        ]
        lines.append(extreme[[col for col in cols if col in extreme.columns]].head(80).to_markdown(index=False))
    lines.append("")
    lines.append("## 集群贡献集中度")
    if concentration.empty:
        lines.append("无。")
    else:
        lines.append(concentration.sort_values("max_contributor_abs_ratio", ascending=False).head(80).to_markdown(index=False))
    return "\n".join(lines) + "\n"
