from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import load_temperature_config


FLOW_STRUCTURE_CONFIG_KEYS = {
    "direction_epsilon_100m",
    "acceleration_ratio_min_denominator_100m",
    "gross_flow_abs_min_100m",
    "gross_flow_to_size_min",
    "net_flow_abs_min_100m",
    "net_flow_to_size_min",
    "high_offset_ratio",
    "medium_offset_ratio",
    "one_sided_net_ratio",
    "breadth_high_ratio",
    "breadth_low_ratio",
    "max_contributor_concentration_ratio",
    "top3_contributor_concentration_ratio",
    "diffusion_max_offset_ratio",
}


def flow_structure_config() -> dict[str, float]:
    config = load_temperature_config().get("flow_structure", {}) or {}
    missing = sorted(FLOW_STRUCTURE_CONFIG_KEYS - set(config))
    if missing:
        raise KeyError(f"temperature.yaml missing flow_structure thresholds: {missing}")
    return {key: float(value) for key, value in config.items()}


def _number(value: Any) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(numeric) if pd.notna(numeric) else float("nan")


def flow_direction(value: Any) -> str:
    number = _number(value)
    epsilon = flow_structure_config()["direction_epsilon_100m"]
    if pd.isna(number) or abs(number) <= epsilon:
        return "方向不明确"
    return "流入" if number > 0 else "流出"


def flow_direction_change(start_value: Any, end_value: Any) -> str:
    start = flow_direction(start_value)
    end = flow_direction(end_value)
    if "不明确" in start or "不明确" in end:
        return "方向不明确"
    if start == end:
        return f"持续{start}"
    return f"由{start}转{end}"


def flow_speed_metrics(
    flow_1d: Any,
    flow_5d: Any,
    valid_days: Any = 5,
    *,
    required_days: int = 5,
) -> dict[str, Any]:
    """Calculate flow speed in 100m yuan and classify the latest-day transition.

    Reversal is determined from ``flow_5d - flow_1d`` (the preceding days),
    not from the sign of the five-day total.  This prevents a large last-day
    reversal from being mislabeled as a continuous same-direction flow.
    """

    one_day = _number(flow_1d)
    five_day = _number(flow_5d)
    days_value = _number(valid_days)
    days = int(days_value) if pd.notna(days_value) and days_value > 0 else 0
    five_day_average = five_day / days if days and pd.notna(five_day) else float("nan")
    previous_days = five_day - one_day if pd.notna(one_day) and pd.notna(five_day) else float("nan")
    previous_valid_days = max(days - 1, 0)
    previous_average = (
        previous_days / previous_valid_days
        if previous_valid_days and pd.notna(previous_days)
        else float("nan")
    )
    acceleration = (
        one_day - previous_average
        if pd.notna(one_day) and pd.notna(previous_average)
        else float("nan")
    )
    config = flow_structure_config()
    ratio_floor = config["acceleration_ratio_min_denominator_100m"]
    acceleration_ratio = (
        acceleration / abs(previous_average)
        if pd.notna(acceleration)
        and pd.notna(previous_average)
        and abs(previous_average) >= ratio_floor
        else float("nan")
    )

    status = "方向不明确"
    epsilon = config["direction_epsilon_100m"]
    if (
        days >= int(required_days)
        and pd.notna(one_day)
        and pd.notna(five_day)
        and pd.notna(previous_days)
    ):
        current_up = one_day > epsilon
        current_down = one_day < -epsilon
        previous_up = previous_days > epsilon
        previous_down = previous_days < -epsilon
        if current_up and previous_down:
            status = "由流出转流入"
        elif current_down and previous_up:
            status = "由流入转流出"
        elif current_up and previous_up:
            status = "持续流入且加速" if acceleration > epsilon else "持续流入但减速"
        elif current_down and previous_down:
            status = "持续流出且加速" if acceleration < -epsilon else "持续流出但减速"

    return {
        "flow_1d": one_day,
        "flow_5d": five_day,
        "flow_5d_valid_days": days,
        "flow_5d_daily_avg": five_day_average,
        "flow_prev4_valid_days": previous_valid_days,
        "flow_prev4_total": previous_days,
        "flow_prev4_daily_avg": previous_average,
        "flow_acceleration_vs_prev4": acceleration,
        "flow_acceleration_ratio_vs_prev4": acceleration_ratio,
        "flow_previous_4d": previous_days,
        "flow_acceleration": acceleration,
        "flow_acceleration_ratio": acceleration_ratio,
        "flow_speed_status": status,
    }


def add_flow_speed_columns(
    frame: pd.DataFrame,
    *,
    one_day_column: str = "estimated_net_subscription_100m",
    five_day_column: str = "estimated_net_subscription_5d_100m",
    valid_days_column: str = "flow_5d_valid_days",
    required_days: int = 5,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    one_day = pd.to_numeric(out.get(one_day_column), errors="coerce")
    five_day = pd.to_numeric(out.get(five_day_column), errors="coerce")
    if valid_days_column in out:
        valid_days = pd.to_numeric(out[valid_days_column], errors="coerce").fillna(0)
    else:
        valid_days = pd.Series(np.where(five_day.notna(), required_days, 0), index=out.index)
    metrics = [
        flow_speed_metrics(day, five, days, required_days=required_days)
        for day, five, days in zip(one_day, five_day, valid_days)
    ]
    metric_frame = pd.DataFrame(metrics, index=out.index)
    for column in metric_frame:
        out[column] = metric_frame[column]
    return out


def _largest_etf(
    subset: pd.DataFrame,
    flows: pd.Series,
    *,
    inflow: bool,
) -> tuple[str, str, float]:
    direction = flows[flows.gt(0)] if inflow else flows[flows.lt(0)]
    direction = direction.dropna()
    if direction.empty:
        return "", "", float("nan")
    idx = direction.idxmax() if inflow else direction.idxmin()
    code = str(subset.loc[idx, "etf_code"]).zfill(6) if "etf_code" in subset else ""
    name = str(subset.loc[idx, "etf_name"]) if "etf_name" in subset and pd.notna(subset.loc[idx, "etf_name"]) else ""
    return code, name, float(flows.loc[idx])


def flow_offset_metrics(
    subset: pd.DataFrame,
    flow_column: str,
    *,
    total_size_100m: Any = 0,
) -> dict[str, Any]:
    flows = (
        pd.to_numeric(subset.get(flow_column), errors="coerce")
        if not subset.empty and flow_column in subset
        else pd.Series(dtype=float)
    )
    flows = flows.dropna()
    gross_inflow = float(flows[flows.gt(0)].sum()) if not flows.empty else 0.0
    gross_outflow = float(flows[flows.lt(0)].sum()) if not flows.empty else 0.0
    net_flow = float(flows.sum()) if not flows.empty else float("nan")
    gross_turnover = gross_inflow + abs(gross_outflow)
    offset_ratio = (
        float(np.clip(1 - abs(net_flow) / gross_turnover, 0, 1))
        if gross_turnover > 0 and pd.notna(net_flow)
        else float("nan")
    )
    max_in_code, max_in_name, max_in_flow = _largest_etf(subset, flows, inflow=True)
    max_out_code, max_out_name, max_out_flow = _largest_etf(subset, flows, inflow=False)

    config = flow_structure_config()
    size = _number(total_size_100m)
    gross_abs_ok = gross_turnover >= config["gross_flow_abs_min_100m"]
    gross_ratio_ok = (
        pd.notna(size)
        and size > 0
        and gross_turnover / size >= config["gross_flow_to_size_min"]
    )
    if not (gross_abs_ok or gross_ratio_ok) or gross_turnover <= 0 or pd.isna(net_flow):
        status = "低流量无方向"
    else:
        net_ratio = abs(net_flow) / gross_turnover
        if pd.notna(offset_ratio) and offset_ratio >= config["high_offset_ratio"]:
            status = "高分歧换仓"
        elif net_ratio >= config["one_sided_net_ratio"]:
            status = "单边流入" if net_flow > 0 else "单边流出"
        elif pd.notna(offset_ratio) and offset_ratio >= config["medium_offset_ratio"]:
            status = "中度分化"
        else:
            status = "中度分化"

    return {
        "gross_inflow": gross_inflow,
        "gross_outflow": gross_outflow,
        "net_flow": net_flow,
        "gross_turnover_flow": gross_turnover,
        "flow_offset_ratio": offset_ratio,
        "flow_offset_status": status,
        "max_inflow_etf_code": max_in_code,
        "max_inflow_etf_name": max_in_name,
        "max_inflow_etf_flow": max_in_flow,
        "max_outflow_etf_code": max_out_code,
        "max_outflow_etf_name": max_out_name,
        "max_outflow_etf_flow": max_out_flow,
    }


def flow_structure_label(
    *,
    member_count: Any,
    net_flow: Any,
    flow_to_size: Any,
    share_increase_member_ratio: Any,
    max_contributor_ratio: Any,
    top3_contributor_ratio: Any,
    gross_turnover_flow: Any,
    flow_offset_ratio: Any,
    total_size_100m: Any = 0,
) -> str:
    config = flow_structure_config()
    members = int(_number(member_count)) if pd.notna(_number(member_count)) else 0
    net = _number(net_flow)
    ratio_to_size = _number(flow_to_size)
    breadth = _number(share_increase_member_ratio)
    max_ratio = _number(max_contributor_ratio)
    top3_ratio = _number(top3_contributor_ratio)
    gross = _number(gross_turnover_flow)
    offset = _number(flow_offset_ratio)
    size = _number(total_size_100m)
    if members < 4:
        return "小样本不判断"
    gross_material = pd.notna(gross) and (
        gross >= config["gross_flow_abs_min_100m"]
        or (
            pd.notna(size)
            and size > 0
            and gross / size >= config["gross_flow_to_size_min"]
        )
    )
    net_material = (
        pd.notna(net)
        and (
            abs(net) >= config["net_flow_abs_min_100m"]
            or (pd.notna(ratio_to_size) and abs(ratio_to_size) >= config["net_flow_to_size_min"])
        )
    )
    if not gross_material and not net_material:
        return "低流量无方向"
    if gross_material and pd.notna(offset) and offset >= config["high_offset_ratio"]:
        return "高分歧换仓"
    if pd.isna(net) or abs(net) <= config["direction_epsilon_100m"]:
        return "低流量无方向"

    broad = (
        pd.notna(breadth)
        and (
            (net > 0 and breadth >= config["breadth_high_ratio"])
            or (net < 0 and breadth <= config["breadth_low_ratio"])
        )
    )
    if not net_material:
        if broad:
            return "广度较高但力度较弱" if net > 0 else "广度较高但流出较弱"
        if pd.notna(offset) and offset >= config["medium_offset_ratio"]:
            return "资金强度较低，内部中度分化"
        return "低流量无方向"

    concentrated = (
        (pd.notna(max_ratio) and max_ratio >= config["max_contributor_concentration_ratio"])
        or (pd.notna(top3_ratio) and top3_ratio >= config["top3_contributor_concentration_ratio"])
    )
    if concentrated:
        return "头部产品集中流入" if net > 0 else "头部产品集中流出"
    low_offset = pd.notna(offset) and offset < config["diffusion_max_offset_ratio"]
    if broad and low_offset:
        return "全面扩散" if net > 0 else "全面撤出"
    if net > 0 and pd.notna(breadth) and breadth >= config["breadth_low_ratio"]:
        return "流入力度较强，广度中等"
    return "金额较大但广度不足"
