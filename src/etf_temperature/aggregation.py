from __future__ import annotations

import numpy as np
import pandas as pd

from .classification import cluster_frame, load_cluster_rules
from .config import get_report_config
from .flow_structure import flow_offset_metrics, flow_speed_metrics, flow_structure_label
from .temperature import cluster_temperature_from_raw


def build_cluster_daily(etf_daily: pd.DataFrame) -> pd.DataFrame:
    rules_df = cluster_frame(load_cluster_rules())
    rows = []
    for _, rule in rules_df.iterrows():
        cid = rule["cluster_id"]
        subset = etf_daily[etf_daily["primary_cluster"].eq(cid)].copy() if not etf_daily.empty else pd.DataFrame()
        size = pd.to_numeric(subset.get("fund_size_100m"), errors="coerce").fillna(0) if not subset.empty else pd.Series(dtype=float)
        total_size = float(size.sum()) if not subset.empty else 0.0
        amount = float(pd.to_numeric(subset.get("amount"), errors="coerce").fillna(0).sum() / 1e8) if not subset.empty else 0.0
        amount5 = float(pd.to_numeric(subset.get("amount_5d_mean"), errors="coerce").fillna(0).sum() / 1e8) if not subset.empty else 0.0
        pct = pd.to_numeric(subset.get("pct_change"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        return_valid = pct.notna()
        return_size = size[return_valid] if not subset.empty else pd.Series(dtype=float)
        weighted_pct = float((pct[return_valid] * return_size).sum() / return_size.sum()) if return_size.sum() else np.nan
        return_5d = pd.to_numeric(subset.get("return_5d"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        return_10d = pd.to_numeric(subset.get("return_10d"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        valid_5d = return_5d.notna()
        valid_10d = return_10d.notna()
        weighted_return_5d = float((return_5d[valid_5d] * size[valid_5d]).sum() / size[valid_5d].sum()) if size[valid_5d].sum() else np.nan
        weighted_return_10d = float((return_10d[valid_10d] * size[valid_10d]).sum() / size[valid_10d].sum()) if size[valid_10d].sum() else np.nan
        flow_subset = subset.copy()
        if not flow_subset.empty:
            flow_subset = flow_subset[
                ~flow_subset.get("flow_validation_status", pd.Series("", index=flow_subset.index)).fillna("").isin(["unresolved", "excluded_corporate_action"])
                & ~flow_subset.get("share_flow_excluded", pd.Series(False, index=flow_subset.index)).fillna(False)
            ].copy()
        net1_series = pd.to_numeric(flow_subset.get("estimated_net_subscription_100m"), errors="coerce") if not flow_subset.empty else pd.Series(dtype=float)
        net5_series = pd.to_numeric(flow_subset.get("estimated_net_subscription_5d_100m"), errors="coerce") if not flow_subset.empty else pd.Series(dtype=float)
        flow_size = pd.to_numeric(flow_subset.get("fund_size_100m"), errors="coerce").fillna(0) if not flow_subset.empty else pd.Series(dtype=float)
        net1_valid = net1_series.notna()
        net5_valid = net5_series.notna()
        net1_valid_size = float(flow_size[net1_valid].sum()) if not flow_subset.empty else 0.0
        net5_valid_size = float(flow_size[net5_valid].sum()) if not flow_subset.empty else 0.0
        net1 = float(net1_series.sum(min_count=1)) if net1_series.notna().any() else np.nan
        net5 = float(net5_series.sum(min_count=1)) if net5_series.notna().any() else np.nan
        share_change = pd.to_numeric(subset.get("share_daily_change"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        share_inc_ratio = float((share_change.dropna() > 0).mean()) if share_change.notna().any() else np.nan
        up_ratio = float((pct[return_valid] > 0).mean()) if return_valid.any() else np.nan
        premium = pd.to_numeric(subset.get("premium_discount"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        max1_code, max1_name, max1_flow, max1_ratio, top3_1_ratio, same1_total = _contributor_stats(
            flow_subset, "estimated_net_subscription_100m"
        )
        max5_code, max5_name, max5_flow, max5_ratio, top3_5_ratio, same5_total = _contributor_stats(
            flow_subset, "estimated_net_subscription_5d_100m"
        )
        speed = flow_speed_metrics(
            net1,
            net5,
            5 if pd.notna(net5) else 0,
        )
        offset = flow_offset_metrics(
            flow_subset,
            "estimated_net_subscription_100m",
            total_size_100m=total_size,
        )
        structure_label = flow_structure_label(
            member_count=len(subset),
            net_flow=offset["net_flow"],
            flow_to_size=net1 / total_size if total_size and pd.notna(net1) else np.nan,
            share_increase_member_ratio=share_inc_ratio,
            max_contributor_ratio=max1_ratio,
            top3_contributor_ratio=top3_1_ratio,
            gross_turnover_flow=offset["gross_turnover_flow"],
            flow_offset_ratio=offset["flow_offset_ratio"],
            total_size_100m=total_size,
        )
        min_concentration_members = int(get_report_config().get("min_concentration_member_count", 4))
        data_complete = bool(not subset.empty and subset[["close", "latest_shares", "nav"]].notna().all(axis=1).mean() >= 0.8)
        median_return = float(pct[return_valid].median()) if return_valid.any() else np.nan
        action_type = subset.get("corporate_action_type", pd.Series(None, index=subset.index)) if not subset.empty else pd.Series(dtype=object)
        action_count = int(action_type.isin(["split", "reverse_split"]).sum())
        suspect_count = int(action_type.eq("suspect").sum())
        cluster_return_valid = bool(
            return_valid.any()
            and not (up_ratio >= 0.75 and weighted_pct < -5.0)
            and not (pd.notna(median_return) and abs(weighted_pct / 100 - median_return) > 0.10)
        )
        rows.append(
            {
                "cluster_id": cid,
                "cluster_name": rule["cluster_name"],
                "member_count": int(len(subset)),
                "total_size_100m": total_size,
                "estimated_net_subscription_100m": net1,
                "estimated_net_subscription_5d_100m": net5,
                **speed,
                **offset,
                "one_day_flow_to_size": net1 / total_size if total_size and pd.notna(net1) else np.nan,
                "five_day_flow_to_size": net5 / total_size if total_size and pd.notna(net5) else np.nan,
                "weighted_pct_change": weighted_pct,
                "weighted_return_5d": weighted_return_5d,
                "weighted_return_10d": weighted_return_10d,
                "total_amount_100m": amount,
                "amount_vs_5d": amount / amount5 if amount5 else np.nan,
                "share_increase_member_ratio": share_inc_ratio,
                "member_up_ratio": up_ratio,
                "member_return_median": median_return,
                "corporate_action_member_count": action_count,
                "corporate_action_suspect_count": suspect_count,
                "cluster_return_valid": cluster_return_valid,
                "median_premium_discount": float(premium.median()) if premium.notna().any() else np.nan,
                "flow_valid_member_count": int(net1_series.notna().sum()) if not subset.empty else 0,
                "one_day_flow_valid_size_100m": net1_valid_size,
                "five_day_flow_valid_size_100m": net5_valid_size,
                "one_day_flow_member_coverage": float(net1_valid.sum() / len(subset)) if len(subset) else 0.0,
                "five_day_flow_member_coverage": float(net5_valid.sum() / len(subset)) if len(subset) else 0.0,
                "one_day_flow_size_coverage": net1_valid_size / total_size if total_size else 0.0,
                "five_day_flow_size_coverage": net5_valid_size / total_size if total_size else 0.0,
                "flow_size_coverage": min(
                    net1_valid_size / total_size if total_size else 0.0,
                    net5_valid_size / total_size if total_size else 0.0,
                ),
                "max_contributor_code_1d": max1_code,
                "max_contributor_name_1d": max1_name,
                "max_contributor_flow_1d_100m": max1_flow,
                "same_direction_flow_total_1d_100m": same1_total,
                "max_contributor_ratio_1d": max1_ratio,
                "top3_contributor_ratio_1d": top3_1_ratio,
                "max_contributor_code_5d": max5_code,
                "max_contributor_name_5d": max5_name,
                "max_contributor_flow_5d_100m": max5_flow,
                "same_direction_flow_total_5d_100m": same5_total,
                "max_contributor_ratio_5d": max5_ratio,
                "top3_contributor_ratio_5d": top3_5_ratio,
                "concentration_sample_valid": bool(len(subset) >= min_concentration_members),
                "flow_structure_label": structure_label,
                "temperature_change": np.nan,
                "structure_judgement": judge_cluster(
                    net1,
                    net5,
                    weighted_pct,
                    amount / amount5 if amount5 else np.nan,
                    data_complete=data_complete,
                ),
            }
        )
    return cluster_temperature_from_raw(pd.DataFrame(rows))


def _max_contributor(subset: pd.DataFrame, column: str) -> tuple[str, float]:
    code, _, _, ratio, _, _ = _contributor_stats(subset, column)
    return code, ratio


def _contributor_stats(subset: pd.DataFrame, column: str) -> tuple[str, str, float, float, float, float]:
    """Return the largest ETF moving in the same direction as the cluster net flow.

    Ratios use gross same-direction flow as the denominator. Opposite-direction ETFs are
    intentionally excluded, so a large redemption cannot be presented as a positive
    contributor to a net-inflow cluster (and vice versa).
    """
    if subset.empty or column not in subset:
        return "", "", np.nan, np.nan, np.nan, np.nan
    flows = pd.to_numeric(subset[column], errors="coerce")
    net_flow = flows.sum(min_count=1)
    if pd.isna(net_flow) or abs(float(net_flow)) < 1e-12:
        return "", "", np.nan, np.nan, np.nan, np.nan
    same_direction = flows[flows.gt(0)] if net_flow > 0 else flows[flows.lt(0)]
    same_direction = same_direction.dropna()
    if same_direction.empty:
        return "", "", np.nan, np.nan, np.nan, np.nan
    gross_same_direction = float(same_direction.abs().sum())
    if not gross_same_direction:
        return "", "", np.nan, np.nan, np.nan, np.nan
    ranked = same_direction.abs().sort_values(ascending=False)
    idx = ranked.index[0]
    name = str(subset.loc[idx, "etf_name"]) if "etf_name" in subset and pd.notna(subset.loc[idx, "etf_name"]) else ""
    return (
        str(subset.loc[idx, "etf_code"]),
        name,
        float(flows.loc[idx]),
        float(ranked.iloc[0] / gross_same_direction),
        float(ranked.head(3).sum() / gross_same_direction),
        gross_same_direction if net_flow > 0 else -gross_same_direction,
    )


def judge_cluster(
    net1: float,
    net5: float,
    weighted_pct: float,
    amount_vs_5d: float | None,
    *,
    data_complete: bool = True,
) -> str:
    if not data_complete:
        return "数据待确认"
    price_up = pd.notna(weighted_pct) and weighted_pct > 0.05
    price_down = pd.notna(weighted_pct) and weighted_pct < -0.05
    flow1_up = pd.notna(net1) and net1 > 0
    flow1_down = pd.notna(net1) and net1 < 0
    flow5_up = pd.notna(net5) and net5 > 0
    flow5_down = pd.notna(net5) and net5 < 0
    active = pd.notna(amount_vs_5d) and amount_vs_5d >= 1.3
    flat_active = pd.notna(weighted_pct) and abs(weighted_pct) <= 0.05 and active
    if price_up and flow1_up and flow5_up:
        return "价格与资金同步增强"
    if price_down and flow1_up and flow5_up:
        return "持续逆向承接"
    if price_down and flow1_up and flow5_down:
        return "单日抄底，中期资金仍弱"
    if price_up and flow1_down and flow5_down:
        return "交易性上涨，申购不足"
    if price_up and flow1_up and flow5_down:
        return "短线转暖，中期尚未确认"
    if price_down and flow1_down and flow5_down:
        return "价格与资金同步降温"
    if flat_active:
        return "高分歧换手"
    return "结构分化"


def _source_count(value: object) -> int:
    parts = [part.strip() for part in str(value).split(",")]
    return len([part for part in parts if part and part.lower() != "nan"])


def _score_column(frame: pd.DataFrame, column: str, *, scale: float = 1.0, cap: float = 20.0, lower: float | None = None) -> pd.Series:
    if column not in frame:
        return pd.Series(0.0, index=frame.index)
    values = pd.to_numeric(frame[column], errors="coerce")
    if lower is not None:
        values = values - lower
    values = values.abs() / scale
    return values.clip(lower=0, upper=cap).fillna(0.0)


def _rank_abnormal_pool(pool: pd.DataFrame) -> pd.DataFrame:
    pool = pool.copy()
    pool["abnormal_signal_count"] = pool["abnormal_source"].map(_source_count)
    amount_score = _score_column(pool, "amount", scale=1e8, cap=20.0)
    flow_score = _score_column(pool, "estimated_net_subscription_100m", scale=1.0, cap=20.0)
    price_score = _score_column(pool, "pct_change", scale=0.5, cap=20.0)
    share_score = _score_column(pool, "share_daily_change_pct", scale=0.2, cap=20.0)
    turnover_score = _score_column(pool, "amount_vs_5d", scale=0.15, cap=20.0, lower=1.0)
    premium_score = _score_column(pool, "premium_discount", scale=0.002, cap=10.0)
    temperature_score = _score_column(pool, "temperature", scale=2.0, cap=20.0, lower=50.0)
    pool["abnormal_score"] = (
        pool["abnormal_signal_count"] * 20.0
        + amount_score
        + flow_score
        + price_score
        + share_score
        + turnover_score
        + premium_score
        + temperature_score
    ).round(2)
    pool["tracking_priority"] = range(1, len(pool) + 1)
    return pool.sort_values(["abnormal_score", "amount"], ascending=[False, False])


def build_abnormal_pool(etf_daily: pd.DataFrame) -> pd.DataFrame:
    if etf_daily.empty:
        return pd.DataFrame()
    report_config = get_report_config()
    candidate_top_n = int(report_config["candidate_rule_top_n"])
    abnormal_pool_size = int(report_config["abnormal_pool_size"])
    candidates = []
    rules = [
        (f"成交额前{candidate_top_n}", "amount", False, candidate_top_n),
        (f"成交额/5日均值前{candidate_top_n}", "amount_vs_5d", False, candidate_top_n),
        (f"单日估算净申购前{candidate_top_n}", "estimated_net_subscription_100m", False, candidate_top_n),
        (f"单日估算净赎回前{candidate_top_n}", "estimated_net_subscription_100m", True, candidate_top_n),
        (f"份额日变化率绝对值前{candidate_top_n}", "share_daily_change_pct_abs", False, candidate_top_n),
        (f"涨幅前{candidate_top_n}", "pct_change", False, candidate_top_n),
        (f"跌幅前{candidate_top_n}", "pct_change", True, candidate_top_n),
        (f"温度前{candidate_top_n}", "temperature", False, candidate_top_n),
    ]
    df = etf_daily.copy()
    if "share_daily_change_pct" in df:
        df["share_daily_change_pct_abs"] = pd.to_numeric(df["share_daily_change_pct"], errors="coerce").abs()
    else:
        df["share_daily_change_pct_abs"] = np.nan
    for source, column, ascending, count in rules:
        if column not in df:
            continue
        top = df.sort_values(column, ascending=ascending).head(count).copy()
        top["abnormal_source"] = source
        candidates.append(top)
    if "premium_discount" in df:
        premium = df[pd.to_numeric(df["premium_discount"], errors="coerce").abs() >= 0.02].copy()
        premium["abnormal_source"] = "异常折溢价ETF"
        candidates.append(premium)
    if "price_share_signal" in df:
        price_share_signal = df["price_share_signal"].fillna("").astype(str)
        price_share = df[price_share_signal.ne("")].copy()
        price_share["abnormal_source"] = price_share["price_share_signal"]
        candidates.append(price_share)
    if "corporate_action_review_required" in df:
        corporate = df[df["corporate_action_review_required"].fillna(False)].copy()
        corporate["abnormal_source"] = "公司行为/异常份额待复核"
        candidates.append(corporate)
    pool = pd.concat(candidates, ignore_index=True, sort=False) if candidates else pd.DataFrame()
    if pool.empty:
        return pool
    agg_map = {
        "etf_name": "first",
        "tracking_index_name": "first",
        "primary_cluster": "first",
        "primary_cluster_name": "first",
        "abnormal_source": lambda s: ",".join(sorted(set(map(str, s)))),
        "amount": "first",
        "share_daily_change": "first",
        "estimated_net_subscription_100m": "first",
        "pct_change": "first",
        "temperature": "first",
        "date": "first",
    }
    for column in [
        "amount_vs_5d",
        "share_daily_change_pct",
        "premium_discount",
        "price_share_signal",
        "fund_size_100m",
    ]:
        if column in pool:
            agg_map[column] = "first"
    pool = pool.groupby("etf_code", as_index=False).agg(agg_map)
    pool = _rank_abnormal_pool(pool).head(abnormal_pool_size).reset_index(drop=True)
    pool["tracking_priority"] = range(1, len(pool) + 1)
    pool["is_in_31_clusters"] = pool["primary_cluster"].ne("")
    pool["one_line_explanation"] = pool.apply(_abnormal_explanation, axis=1)
    pool["source"] = "derived:abnormal_pool"
    pool["source_priority"] = 1
    pool["is_official"] = False
    pool["validation_status"] = "derived"
    return pool


def _abnormal_explanation(row: pd.Series) -> str:
    net = row.get("estimated_net_subscription_100m")
    pct = row.get("pct_change")
    if pd.notna(net) and net > 0 and pd.notna(pct) and pct < 0:
        return "价格回落但份额增加，关注承接是否延续。"
    if pd.notna(net) and net < 0 and pd.notna(pct) and pct > 0:
        return "价格上涨但份额减少，可能偏二级市场交易热度。"
    return "进入多维异常池，需结合份额、价格和成交额共同观察。"
