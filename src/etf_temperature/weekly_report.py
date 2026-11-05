from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import get_report_config, load_temperature_config


BIG = {"C01", "C02", "C03"}
SMALL = {"C04", "C05", "C06"}
DIVIDEND_DEFENSE = {"C11", "C12", "C13", "C15"}
RESOURCE_CYCLE = {"C29", "C30", "C31"}
GROWTH = {"C16", "C17", "C18", "C19", "C20", "C25", "C26", "C27", "C35"}


def _week_bounds_from_label(week: str) -> tuple[str, str]:
    year_text, week_text = week.split("-W", 1)
    start = date.fromisocalendar(int(year_text), int(week_text), 1)
    end = start + timedelta(days=6)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _week_date_line(week: str, validation: dict) -> str:
    default_start, default_end = _week_bounds_from_label(week)
    natural_start = validation.get("week_start") or default_start
    natural_end = validation.get("week_end") or default_end
    trading_start = validation.get("week_first_trading_day") or natural_start
    trading_end = (
        validation.get("week_last_trading_day")
        or validation.get("expected_last_trading_day")
        or validation.get("trading_week_end")
        or natural_end
    )
    return f"> 统计区间：{trading_start} 至 {trading_end}；自然周：{natural_start} 至 {natural_end}。"


def _fmt_num(value, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def _fmt_signed_num(value, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.{digits}f}"



def _fmt_money(value, digits: int = 1, *, signed: bool = True) -> str:
    if pd.isna(value):
        return "-"
    number = _fmt_signed_num(value, digits) if signed else _fmt_num(value, digits)
    return f"{number}亿"


def _fmt_multiple(value, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    return f"{_fmt_num(value, digits)}倍"

def _fmt_pct(value, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}%"


def _fmt_ratio(value, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def _cluster_pct_text(row: pd.Series) -> str:
    return _fmt_pct(row.get("weighted_pct_change")) if bool(row.get("cluster_return_valid", True)) else "待确认"


def _fmt_code(value) -> str:
    if pd.isna(value):
        return "-"
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def _safe_sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return float("nan")
    values = pd.to_numeric(frame[column], errors="coerce")
    return float(values.sum()) if values.notna().any() else float("nan")


def _num_col(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame:
        return pd.Series(float("nan"), index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce")


def _cluster_subset(weekly_cluster: pd.DataFrame, ids: set[str]) -> pd.DataFrame:
    return weekly_cluster[weekly_cluster["cluster_id"].isin(ids)].copy() if not weekly_cluster.empty else weekly_cluster


def _aggregate_clusters(weekly_cluster: pd.DataFrame, ids: set[str], name: str) -> dict:
    subset = _cluster_subset(weekly_cluster, ids)
    if subset.empty:
        return {
            "name": name,
            "flow": float("nan"),
            "pct": float("nan"),
            "size": float("nan"),
            "flow_coverage": float("nan"),
        }
    return_valid = subset.get("cluster_return_valid", pd.Series(True, index=subset.index)).fillna(False)
    price_subset = subset[return_valid].copy()
    size = pd.to_numeric(price_subset.get("total_size_100m"), errors="coerce").fillna(0)
    pct = pd.to_numeric(price_subset.get("weighted_pct_change"), errors="coerce")
    weighted_pct = float((pct.fillna(0) * size).sum() / size.sum()) if size.sum() else float(pct.mean())
    total_size = _num_col(subset, "total_size_100m").fillna(0)
    valid_flow_size = (
        _num_col(subset, "flow_valid_size_100m").fillna(0)
        if "flow_valid_size_100m" in subset
        else total_size
    )
    return {
        "name": name,
        "flow": _safe_sum(subset, "weekly_net_subscription_100m"),
        "pct": weighted_pct,
        "size": float(size.sum()) if size.notna().any() else float("nan"),
        "flow_coverage": float(valid_flow_size.sum() / total_size.sum()) if total_size.sum() else float("nan"),
    }


def _style_line(left: dict, right: dict) -> str:
    def winner(key: str) -> dict:
        if pd.isna(left[key]) and pd.isna(right[key]):
            return left
        if pd.isna(left[key]):
            return right
        if pd.isna(right[key]):
            return left
        return left if left[key] >= right[key] else right

    flow_winner = winner("flow")
    if pd.isna(left["pct"]) or pd.isna(right["pct"]):
        return (
            f"{left['name']}周净申购{_fmt_money(left['flow'])}、资金规模覆盖{_fmt_ratio(left['flow_coverage'])}、周涨跌{_fmt_pct(left['pct'])}；"
            f"{right['name']}周净申购{_fmt_money(right['flow'])}、资金规模覆盖{_fmt_ratio(right['flow_coverage'])}、周涨跌{_fmt_pct(right['pct'])}。"
            f"份额迁移偏向{flow_winner['name']}；至少一侧的集群收益未通过一致性校验，暂不作相对强弱判断。"
        )
    price_winner = winner("pct")
    return (
        f"{left['name']}周净申购{_fmt_money(left['flow'])}、资金规模覆盖{_fmt_ratio(left['flow_coverage'])}、周涨跌{_fmt_pct(left['pct'])}；"
        f"{right['name']}周净申购{_fmt_money(right['flow'])}、资金规模覆盖{_fmt_ratio(right['flow_coverage'])}、周涨跌{_fmt_pct(right['pct'])}。"
        f"份额迁移偏向{flow_winner['name']}，价格相对强的是{price_winner['name']}。"
    )


def _style_line_three(items: list[dict]) -> str:
    valid_flow = [item for item in items if pd.notna(item.get("flow"))]
    valid_price = [item for item in items if pd.notna(item.get("pct"))]
    flow_winner = max(valid_flow, key=lambda item: float(item["flow"])) if valid_flow else None
    price_winner = max(valid_price, key=lambda item: float(item["pct"])) if valid_price else None
    details = "；".join(
        f"{item['name']}周净申购{_fmt_money(item['flow'])}、资金规模覆盖{_fmt_ratio(item['flow_coverage'])}、周涨跌{_fmt_pct(item['pct'])}"
        for item in items
    )
    conclusions = []
    if flow_winner is not None:
        conclusions.append(f"份额迁移偏向{flow_winner['name']}")
    if price_winner is not None:
        conclusions.append(f"价格相对强的是{price_winner['name']}")
    return details + "。" + "，".join(conclusions) + "。"


def _concentration_sample_valid(row: pd.Series) -> bool:
    configured = int(get_report_config().get("min_concentration_member_count", 4))
    explicit = row.get("concentration_sample_valid")
    if pd.notna(explicit):
        return bool(explicit)
    members = pd.to_numeric(pd.Series([row.get("member_count")]), errors="coerce").iloc[0]
    return bool(pd.notna(members) and int(members) >= configured)


def _concentration_materiality_valid(row: pd.Series) -> bool:
    thresholds = load_temperature_config().get("thresholds", {})
    min_abs_flow = float(thresholds.get("min_concentration_abs_flow_100m", 1.0))
    min_flow_to_size = float(thresholds.get("min_concentration_flow_to_size", 0.005))
    flow = pd.to_numeric(pd.Series([row.get("weekly_net_subscription_100m")]), errors="coerce").iloc[0]
    size = pd.to_numeric(pd.Series([row.get("total_size_100m")]), errors="coerce").iloc[0]
    flow_to_size = float(flow) / float(size) if pd.notna(flow) and pd.notna(size) and float(size) else float("nan")
    return bool(
        (pd.notna(flow) and abs(float(flow)) >= min_abs_flow)
        or (pd.notna(flow_to_size) and abs(float(flow_to_size)) >= min_flow_to_size)
    )


def _concentration_analysis_valid(row: pd.Series) -> bool:
    return _concentration_sample_valid(row) and _concentration_materiality_valid(row)


def _concentration_row_mask(table: pd.DataFrame) -> pd.Series:
    """返回集中度分析的行级有效性掩码，dtype 恒为 bool。

    对空 DataFrame 调用 `apply(func, axis=1)` 返回的是 float64/object 空 Series，
    `table[mask]` 会被 pandas 解释为「按列标签取列」而不是行过滤，结果是列被全部丢掉
    （shape 变成 (0, 0)），后续 `table[column]` 直接 KeyError。这里强制布尔掩码。
    """
    if table.empty:
        return pd.Series(True, index=table.index, dtype=bool)
    mask = table.apply(_concentration_analysis_valid, axis=1)
    if not isinstance(mask, pd.Series):
        return pd.Series(True, index=table.index, dtype=bool)
    return mask.astype(bool)


def _contributor_text(row: pd.Series) -> str:
    code = _fmt_code(row.get("max_contributor_code_1d"))
    if code == "-":
        return "-"
    name = row.get("max_contributor_name_1d", "")
    name_text = f" {name}" if isinstance(name, str) and name else ""
    flow = row.get("max_contributor_flow_1d_100m")
    flow_text = f"{_fmt_money(flow)}" if pd.notna(flow) else "金额-"
    members = pd.to_numeric(pd.Series([row.get("member_count")]), errors="coerce").iloc[0]
    if not _concentration_sample_valid(row):
        member_text = str(int(members)) if pd.notna(members) else "不足"
        return f"{code}{name_text}（{flow_text}；样本{member_text}只，不作集中度判断）"
    cluster_flow = row.get("weekly_net_subscription_100m")
    direction = "同向流入" if pd.notna(cluster_flow) and float(cluster_flow) > 0 else "同向流出"
    if not _concentration_materiality_valid(row):
        return f"{code}{name_text}（{flow_text}，占{direction}{_fmt_ratio(row.get('max_contributor_ratio_1d'))}；资金规模较小）"
    return f"{code}{name_text}（{flow_text}，占{direction}{_fmt_ratio(row.get('max_contributor_ratio_1d'))}）"


def _top3_contributor_text(row: pd.Series) -> str:
    if not _concentration_sample_valid(row):
        return "样本不足"
    if not _concentration_materiality_valid(row):
        return "金额不足"
    return _fmt_ratio(row.get("top3_contributor_ratio_1d"))


def _weekly_cluster_table(weekly_cluster: pd.DataFrame) -> str:
    if weekly_cluster.empty:
        return "无数据。"
    rows = []
    for _, row in weekly_cluster.sort_values("cluster_id").iterrows():
        size = row.get("total_size_100m")
        flow = row.get("weekly_net_subscription_100m")
        flow_to_size = float(flow) / float(size) if pd.notna(flow) and pd.notna(size) and float(size) else float("nan")
        rows.append(
            {
                "集群名称": row.get("cluster_name", "-"),
                "周涨跌幅": _cluster_pct_text(row),
                "收益校验": "有效" if row.get("cluster_return_valid", True) else "待确认",
                "周估算净申购": _fmt_money(flow),
                "周末单日资金": _fmt_money(row.get("flow_1d")),
                "前一阶段日均资金": _fmt_money(row.get("flow_prev4_daily_avg")),
                "资金速度": row.get("flow_speed_status", "-"),
                "周初/周末方向": row.get("weekly_flow_direction_change", "-"),
                "资金规模覆盖": _fmt_ratio(row.get("flow_size_coverage")),
                "周净申购/规模": _fmt_ratio(flow_to_size),
                "份额增加成员比例": _fmt_ratio(row.get("share_increase_member_ratio")),
                "最大同向资金贡献ETF": _contributor_text(row),
                "前3只ETF同向贡献比例": _top3_contributor_text(row),
                "毛流入": _fmt_money(row.get("gross_inflow")),
                "毛流出": _fmt_money(row.get("gross_outflow")),
                "抵消率": _fmt_ratio(row.get("flow_offset_ratio")),
                "内部状态": row.get("flow_offset_status", "-"),
                "结构标签": row.get("flow_structure_label", "-"),
                "周成交额": _fmt_money(row.get("total_amount_100m"), signed=False),
                "成交额/4周均值": _fmt_multiple(row.get("weekly_amount_vs_4w")),
                "周末温度": f"{_fmt_num(row.get('cluster_temperature'), 1)} {row.get('temperature_level', '-')} / {row.get('temperature_stage', '-')}",
                "温度变化": _fmt_signed_num(row.get("weekly_temperature_change"), 1),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _top_cluster_names(weekly_cluster: pd.DataFrame, column: str, ascending: bool, n: int = 3) -> str:
    if weekly_cluster.empty or column not in weekly_cluster:
        return "无"
    table = weekly_cluster.dropna(subset=[column]).sort_values(column, ascending=ascending).head(n)
    return "、".join(table["cluster_name"].astype(str).tolist()) if not table.empty else "无"


def _flow_eligible_clusters(weekly_cluster: pd.DataFrame) -> pd.DataFrame:
    if weekly_cluster.empty or "flow_size_coverage" not in weekly_cluster:
        return weekly_cluster
    threshold = float(
        load_temperature_config()["thresholds"].get(
            "weekly_min_material_cluster_flow_size_coverage", 0.90
        )
    )
    return weekly_cluster[_num_col(weekly_cluster, "flow_size_coverage") >= threshold].copy()


def _one_sentence(snapshot: dict, weekly_cluster: pd.DataFrame) -> str:
    market = snapshot["market"]
    flow_clusters = _flow_eligible_clusters(weekly_cluster)
    strongest = _top_cluster_names(flow_clusters, "weekly_net_subscription_100m", False)
    weakest = _top_cluster_names(flow_clusters, "weekly_net_subscription_100m", True)
    hottest = _top_cluster_names(weekly_cluster, "cluster_temperature", False)
    return (
        f"本周A股权益ETF估算净申购{_fmt_money(market['weekly_net_subscription_100m'])}，"
        f"总成交额{_fmt_money(market['weekly_amount_100m'], signed=False)}；份额主要迁入{strongest}，"
        f"主要迁出{weakest}，温度高点集中在{hottest}。"
    )


def _compact_weekly_cluster_flow_table(
    weekly_cluster: pd.DataFrame,
    *,
    ascending: bool,
    sign_filter: str,
    top_n: int = 5,
) -> str:
    table = _flow_eligible_clusters(weekly_cluster)
    if table.empty or "weekly_net_subscription_100m" not in table:
        return "无满足资金规模覆盖门槛的集群。"
    flow = _num_col(table, "weekly_net_subscription_100m")
    table = table[flow.gt(0) if sign_filter == "positive" else flow.lt(0)].copy()
    table = table.sort_values("weekly_net_subscription_100m", ascending=ascending).head(top_n)
    if table.empty:
        return "无符合该方向的集群。"
    rows = []
    for _, row in table.iterrows():
        size = row.get("total_size_100m")
        value = row.get("weekly_net_subscription_100m")
        ratio = float(value) / float(size) if pd.notna(value) and pd.notna(size) and float(size) else float("nan")
        rows.append(
            {
                "集群": row.get("cluster_name", "-"),
                "周估算净申购": _fmt_money(value),
                "净申购/规模": _fmt_ratio(ratio),
                "资金规模覆盖": _fmt_ratio(row.get("flow_size_coverage")),
                "份额扩张广度": _fmt_ratio(row.get("share_increase_member_ratio")),
                "资金速度": row.get("flow_speed_status", "-"),
                "内部状态": row.get("flow_offset_status", "-"),
                "结构标签": row.get("flow_structure_label", "-"),
                "最大同向贡献ETF": _contributor_text(row),
                "周涨跌": _cluster_pct_text(row),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _continuous_table(weekly_etf: pd.DataFrame, direction: str) -> str:
    if weekly_etf.empty:
        return "无数据。"
    trading_days = _num_col(weekly_etf, "trading_days")
    if direction == "in":
        days = _num_col(weekly_etf, "flow_positive_days")
        table = weekly_etf[(days >= trading_days) & (_num_col(weekly_etf, "weekly_net_subscription_100m") > 0)].copy()
        table = table.sort_values("weekly_net_subscription_100m", ascending=False)
    else:
        days = _num_col(weekly_etf, "flow_negative_days")
        table = weekly_etf[(days >= trading_days) & (_num_col(weekly_etf, "weekly_net_subscription_100m") < 0)].copy()
        table = table.sort_values("weekly_net_subscription_100m", ascending=True)
    if table.empty:
        return "无满足全周连续条件的ETF。"
    rows = []
    for _, row in table.head(10).iterrows():
        rows.append(
            {
                "ETF": f"{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}".strip(),
                "集群": row.get("primary_cluster_name", "-"),
                "周涨跌幅": _fmt_pct(row.get("weekly_pct_change")),
                "周估算净申购": _fmt_money(row.get("weekly_net_subscription_100m")),
                "连续天数": int(row.get("trading_days", 0)),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _week_turn_lines(weekly_etf: pd.DataFrame) -> list[str]:
    if weekly_etf.empty:
        return ["- 无数据。"]
    temp_change = _num_col(weekly_etf, "weekly_temperature_change")
    first_level = weekly_etf.get("first_temperature_level", pd.Series("", index=weekly_etf.index)).fillna("")
    end_level = weekly_etf.get("week_end_temperature_level", pd.Series("", index=weekly_etf.index)).fillna("")
    hot_to_cold = weekly_etf[first_level.eq("高温") & ~end_level.eq("高温")]
    cold_to_hot = weekly_etf[first_level.isin(["低温", "偏冷", "中性"]) & end_level.eq("高温")]
    biggest_up = weekly_etf.assign(_change=temp_change).sort_values("_change", ascending=False).head(1)
    biggest_down = weekly_etf.assign(_change=temp_change).sort_values("_change", ascending=True).head(1)
    lines = []
    if not cold_to_hot.empty:
        row = cold_to_hot.sort_values("weekly_temperature_change", ascending=False).iloc[0]
        lines.append(f"- 周初非热、周末高温：{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}升至{row.get('week_end_temperature_stage', row.get('week_end_temperature_level', '-'))}（{_fmt_num(row.get('week_end_temperature'), 1)}），说明本周末风险偏好明显抬升。")
    if not hot_to_cold.empty:
        row = hot_to_cold.sort_values("weekly_temperature_change").iloc[0]
        lines.append(f"- 周初高温、周末降温：{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}降至{row.get('week_end_temperature_stage', row.get('week_end_temperature_level', '-'))}（{_fmt_num(row.get('week_end_temperature'), 1)}），说明周内拥挤度退潮。")
    if not biggest_up.empty:
        row = biggest_up.iloc[0]
        lines.append(f"- 升温最大：{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}温度变化{_fmt_signed_num(row.get('weekly_temperature_change'), 1)}，需要看下周是否扩散到同集群成员。")
    if not biggest_down.empty:
        row = biggest_down.iloc[0]
        lines.append(f"- 降温最大：{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}温度变化{_fmt_signed_num(row.get('weekly_temperature_change'), 1)}，说明本周后段交易热度和份额合力减弱。")
    return lines[:4] if lines else ["- 无明显周初周末温度切换。"]


def _single_day_vs_trend_lines(weekly_etf: pd.DataFrame, weekly_cluster: pd.DataFrame) -> list[str]:
    if weekly_etf.empty:
        return ["- 无数据。"]
    one_day_like = weekly_cluster[
        (
            _num_col(weekly_cluster, "weekly_net_subscription_100m").abs()
            < _num_col(weekly_cluster, "total_amount_100m").abs() * 0.02
        )
        & (_num_col(weekly_cluster, "weekly_amount_vs_4w") >= 1.3)
    ]
    trend_in = weekly_cluster[
        (_num_col(weekly_cluster, "weekly_net_subscription_100m") > 0)
        & (_num_col(weekly_cluster, "share_increase_member_ratio") >= 0.5)
    ].sort_values("weekly_net_subscription_100m", ascending=False)
    trend_out = weekly_cluster[
        (_num_col(weekly_cluster, "weekly_net_subscription_100m") < 0)
        & (_num_col(weekly_cluster, "share_increase_member_ratio") < 0.5)
    ].sort_values("weekly_net_subscription_100m")
    return [
        f"- 持续趋势：{_top_cluster_names(trend_in, 'weekly_net_subscription_100m', False)}同时具备周净申购和成员份额扩张，更像趋势而不是单日脉冲。",
        f"- 持续流出：{_top_cluster_names(trend_out, 'weekly_net_subscription_100m', True)}周净赎回且成员广度偏弱，说明资金收缩不是单只产品噪声。",
        f"- 单日异动候选：{', '.join(one_day_like['cluster_name'].astype(str).head(3).tolist()) if not one_day_like.empty else '无'}成交放大但周份额信号不足，应继续验证。",
    ]


def _weekly_flow_speed_lines(weekly_cluster: pd.DataFrame) -> list[str]:
    if weekly_cluster.empty or "flow_speed_status" not in weekly_cluster:
        return ["- 本周资金速度字段不足，暂不判断周末相对前一阶段日均的加减速。"]
    table = _flow_eligible_clusters(weekly_cluster).copy()
    status = table["flow_speed_status"].fillna("方向不明确").astype(str)
    accelerating = table[status.isin(["持续流入且加速", "持续流出且加速"])].sort_values(
        "flow_acceleration_vs_prev4", key=lambda values: pd.to_numeric(values, errors="coerce").abs(), ascending=False
    )
    slowing = table[status.isin(["持续流入但减速", "持续流出但减速"])].sort_values(
        "weekly_net_subscription_100m",
        key=lambda values: pd.to_numeric(values, errors="coerce").abs(),
        ascending=False,
    )
    reverse_in = table[status.eq("由流出转流入")].sort_values("flow_1d", ascending=False)
    reverse_out = table[status.eq("由流入转流出")].sort_values("flow_1d", ascending=True)
    start_end = table.get("weekly_flow_direction_change", pd.Series("方向不明确", index=table.index)).fillna("方向不明确")
    changed = table[start_end.isin(["由流出转流入", "由流入转流出"])]
    return [
        f"- 周末相对前一阶段日均加速：{_top_cluster_names(accelerating, 'flow_acceleration_vs_prev4', False)}。",
        f"- 周末仍延续但速度下降：{_top_cluster_names(slowing, 'weekly_net_subscription_100m', False)}。",
        f"- 由流出转流入：{_top_cluster_names(reverse_in, 'flow_1d', False)}；由流入转流出：{_top_cluster_names(reverse_out, 'flow_1d', True)}。",
        f"- 直接比较周初与周末，发生方向切换的集群有{len(changed)}个；该口径与周累计净额分开呈现。",
    ]


def _weekly_offset_table(weekly_cluster: pd.DataFrame, statuses: set[str], top_n: int = 5) -> str:
    if weekly_cluster.empty or "flow_offset_status" not in weekly_cluster:
        return "无数据。"
    table = weekly_cluster[weekly_cluster["flow_offset_status"].isin(statuses)].copy()
    table = table.sort_values("gross_turnover_flow", ascending=False).head(top_n)
    if table.empty:
        return "无符合条件的集群。"
    rows = []
    for _, row in table.iterrows():
        max_in = f"{_fmt_code(row.get('max_inflow_etf_code'))} {row.get('max_inflow_etf_name', '')}".strip()
        max_out = f"{_fmt_code(row.get('max_outflow_etf_code'))} {row.get('max_outflow_etf_name', '')}".strip()
        rows.append(
            {
                "集群": row.get("cluster_name", "-"),
                "状态": row.get("flow_offset_status", "-"),
                "毛流入": _fmt_money(row.get("gross_inflow")),
                "毛流出": _fmt_money(row.get("gross_outflow")),
                "净流量": _fmt_money(row.get("net_flow")),
                "抵消率": _fmt_ratio(row.get("flow_offset_ratio")),
                "最大流入ETF": f"{max_in} {_fmt_money(row.get('max_inflow_etf_flow'))}".strip(),
                "最大流出ETF": f"{max_out} {_fmt_money(row.get('max_outflow_etf_flow'))}".strip(),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _weekly_amount_breadth_lines(weekly_cluster: pd.DataFrame) -> list[str]:
    if weekly_cluster.empty or "flow_structure_label" not in weekly_cluster:
        return ["- 资金金额与广度组合字段不足。"]
    label = weekly_cluster["flow_structure_label"].fillna("").astype(str)
    synchronized = weekly_cluster[label.isin(["全面扩散", "全面撤出"])]
    breadth_weak = weekly_cluster[label.isin(["广度较高但力度较弱", "广度较高但流出较弱"])]
    medium_breadth = weekly_cluster[label.eq("流入力度较强，广度中等")]
    medium_divergence = weekly_cluster[label.eq("资金强度较低，内部中度分化")]
    amount_narrow = weekly_cluster[label.eq("金额较大但广度不足")]
    concentrated = weekly_cluster[label.isin(["头部产品集中流入", "头部产品集中流出"])]
    return [
        f"- 金额与广度同步改善或撤出：{_top_cluster_names(synchronized, 'weekly_net_subscription_100m', False)}。",
        f"- 广度较高但力度较弱：{_top_cluster_names(breadth_weak, 'share_increase_member_ratio', False)}；流入力度较强且广度中等：{_top_cluster_names(medium_breadth, 'weekly_net_subscription_100m', False)}。",
        f"- 资金强度较低且内部中度分化：{_top_cluster_names(medium_divergence, 'gross_turnover_flow', False)}；金额较大但广度不足：{_top_cluster_names(amount_narrow, 'weekly_net_subscription_100m', False)}。",
        f"- 头部产品集中驱动：{_top_cluster_names(concentrated, 'weekly_net_subscription_100m', False)}；这些方向不写成全面扩散。",
    ]


def _concentration_lines(weekly_cluster: pd.DataFrame) -> list[str]:
    if weekly_cluster.empty or "max_contributor_ratio_1d" not in weekly_cluster:
        return ["- 暂无可用的资金集中度字段。"]
    table = weekly_cluster.dropna(subset=["max_contributor_ratio_1d"]).copy()
    table = table.loc[_concentration_row_mask(table)]
    if table.empty:
        return ["- 无达到最小成员数要求的集群，暂不作集中度判断。"]
    table["_ratio"] = pd.to_numeric(table["max_contributor_ratio_1d"], errors="coerce")
    lines = []
    for _, row in table.sort_values("_ratio", ascending=False).head(8).iterrows():
        warning = " 集群同向资金主要由单一产品驱动，广度有限。" if row["_ratio"] > 0.5 else ""
        lines.append(
            f"- {row.get('cluster_name')}：最大同向贡献{_contributor_text(row)}，前3只ETF同向贡献{_top3_contributor_text(row)}。{warning}"
        )
    return lines


def _weekly_metric_value(row: pd.Series, column: str) -> str:
    value = row.get(column)
    if column in {
        "weekly_net_subscription_100m",
        "weekly_amount_100m",
        "gross_inflow",
        "gross_outflow",
        "net_flow",
        "gross_turnover_flow",
        "flow_acceleration",
    }:
        return _fmt_money(value, signed=column not in {"weekly_amount_100m", "gross_turnover_flow"})
    if column in {"weekly_pct_change", "weekly_share_change_pct"}:
        return _fmt_pct(value)
    if column in {"weekly_amount_vs_4w"}:
        return _fmt_multiple(value)
    if column in {"week_end_temperature", "weekly_avg_temperature", "weekly_temperature_change"}:
        return _fmt_signed_num(value, 1) if column == "weekly_temperature_change" else _fmt_num(value, 1)
    if column in {"flow_offset_ratio", "flow_acceleration_ratio", "flow_acceleration_ratio_vs_prev4"}:
        return _fmt_ratio(value)
    if column == "weekly_share_change":
        return _fmt_num(value, 0)
    return _fmt_num(value)


def _weekly_etf_ranking_specs(top_n: int) -> list[dict]:
    return [
        {"title": f"周度估算净申购Top{top_n}", "column": "weekly_net_subscription_100m", "ascending": False, "sign_filter": "positive"},
        {"title": f"周度估算净赎回Top{top_n}", "column": "weekly_net_subscription_100m", "ascending": True, "sign_filter": "negative"},
        {"title": f"周末相对前一阶段资金加速Top{top_n}", "column": "flow_acceleration_vs_prev4", "ascending": False},
        {"title": f"周末相对前一阶段资金减速Top{top_n}", "column": "flow_acceleration_vs_prev4", "ascending": True},
        {"title": f"份额增长Top{top_n}", "column": "weekly_share_change", "ascending": False, "sign_filter": "positive"},
        {"title": f"份额减少Top{top_n}", "column": "weekly_share_change", "ascending": True, "sign_filter": "negative"},
        {"title": f"成交额Top{top_n}", "column": "weekly_amount_100m", "ascending": False},
        {"title": f"成交额放大Top{top_n}", "column": "weekly_amount_vs_4w", "ascending": False},
        {"title": f"涨幅Top{top_n}", "column": "weekly_pct_change", "ascending": False},
        {"title": f"跌幅Top{top_n}", "column": "weekly_pct_change", "ascending": True},
        {"title": f"温度Top{top_n}", "column": "week_end_temperature", "ascending": False},
        {"title": f"升温Top{top_n}", "column": "weekly_temperature_change", "ascending": False},
        {"title": f"降温Top{top_n}", "column": "weekly_temperature_change", "ascending": True},
        {"title": f"价涨份额降Top{top_n}", "column": "weekly_pct_change", "ascending": False, "price_share_filter": "up_share_down"},
        {"title": f"价跌份额增Top{top_n}", "column": "weekly_share_change", "ascending": False, "price_share_filter": "down_share_up"},
    ]


def _weekly_etf_ranking_table(weekly_etf: pd.DataFrame, spec: dict, top_n: int) -> str:
    if weekly_etf.empty or spec["column"] not in weekly_etf:
        return "无数据。"
    table = weekly_etf.copy()
    metric = pd.to_numeric(table[spec["column"]], errors="coerce")
    if spec.get("price_share_filter"):
        if not {"weekly_pct_change", "weekly_share_change"}.issubset(table.columns):
            return "无数据。"
        price = pd.to_numeric(table["weekly_pct_change"], errors="coerce")
        share = pd.to_numeric(table["weekly_share_change"], errors="coerce")
        if spec["price_share_filter"] == "up_share_down":
            table = table[(price > 0) & (share < 0)]
        elif spec["price_share_filter"] == "down_share_up":
            table = table[(price < 0) & (share > 0)]
        metric = metric.loc[table.index]
    if spec.get("sign_filter") == "positive":
        table = table[metric.gt(0)]
        metric = metric.loc[table.index]
    elif spec.get("sign_filter") == "negative":
        table = table[metric.lt(0)]
        metric = metric.loc[table.index]
    table["_rank_metric"] = metric
    table = table.dropna(subset=["_rank_metric"]).sort_values("_rank_metric", ascending=spec["ascending"]).head(top_n)
    if table.empty:
        return "无符合该方向的ETF。"
    rows = []
    for rank, (_, row) in enumerate(table.iterrows(), start=1):
        rows.append(
            {
                "排名": rank,
                "ETF": f"{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}".strip(),
                "集群": row.get("primary_cluster_name", "-"),
                "指标": _weekly_metric_value(row, spec["column"]),
                "周涨跌幅": _fmt_pct(row.get("weekly_pct_change")),
                "周估算净申购": _fmt_money(row.get("weekly_net_subscription_100m")),
                "周末温度": _fmt_num(row.get("week_end_temperature"), 1),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _append_weekly_etf_rankings(lines: list[str], weekly_etf: pd.DataFrame, top_n: int) -> None:
    lines.append(f"### 单只ETF周度完整排行榜Top{top_n}")
    lines.append("")
    lines.append("以下榜单保留完整周度观察维度；成交额只代表二级市场换手和活跃度，不写作资金流入。")
    lines.append("")
    for spec in _weekly_etf_ranking_specs(top_n):
        if spec["column"] not in weekly_etf.columns:
            continue
        lines.append(f"#### {spec['title']}")
        lines.append("")
        lines.append(_weekly_etf_ranking_table(weekly_etf, spec, top_n))
        lines.append("")


def _weekly_cluster_rank_table(
    weekly_cluster: pd.DataFrame,
    column: str,
    ascending: bool,
    top_n: int = 10,
    sign_filter: str | None = None,
) -> str:
    if weekly_cluster.empty or column not in weekly_cluster:
        return "无数据。"
    table = weekly_cluster.dropna(subset=[column]).copy()
    if column in {"max_contributor_ratio_1d", "top3_contributor_ratio_1d"}:
        table = table.loc[_concentration_row_mask(table)]
    if column == "weighted_pct_change" and "cluster_return_valid" in table:
        table = table[table["cluster_return_valid"].fillna(False)].copy()
    metric = pd.to_numeric(table[column], errors="coerce")
    if sign_filter == "positive":
        table = table[metric.gt(0)]
        metric = metric.loc[table.index]
    elif sign_filter == "negative":
        table = table[metric.lt(0)]
        metric = metric.loc[table.index]
    table["_rank_metric"] = metric
    table = table.dropna(subset=["_rank_metric"]).sort_values("_rank_metric", ascending=ascending).head(top_n)
    if table.empty:
        return "无符合该方向的集群。"
    rows = []
    for rank, (_, row) in enumerate(table.iterrows(), start=1):
        size = row.get("total_size_100m")
        flow = row.get("weekly_net_subscription_100m")
        flow_to_size = float(flow) / float(size) if pd.notna(flow) and pd.notna(size) and float(size) else float("nan")
        rows.append(
            {
                "排名": rank,
                "集群": row.get("cluster_name", "-"),
                "指标": _weekly_metric_value(row, column) if column in row else _fmt_num(row.get(column)),
                "周涨跌幅": _cluster_pct_text(row),
                "周估算净申购": _fmt_money(flow),
                "周净申购/规模": _fmt_ratio(flow_to_size),
                "份额增加成员比例": _fmt_ratio(row.get("share_increase_member_ratio")),
                "最大同向贡献ETF": _contributor_text(row),
                "前3同向贡献比例": _top3_contributor_text(row),
                "周末温度": _fmt_num(row.get("cluster_temperature"), 1),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _append_weekly_cluster_rankings(lines: list[str], weekly_cluster: pd.DataFrame) -> None:
    specs = [
        ("周度净申购前10集群", "weekly_net_subscription_100m", False, "positive"),
        ("周度净赎回前10集群", "weekly_net_subscription_100m", True, "negative"),
        ("毛流入前10集群", "gross_inflow", False, "positive"),
        ("毛流出前10集群", "gross_outflow", True, "negative"),
        ("资金抵消率前10集群", "flow_offset_ratio", False, None),
        ("周末相对前一阶段资金加速度前10集群", "flow_acceleration_vs_prev4", False, None),
        ("份额增加成员比例前10集群", "share_increase_member_ratio", False, None),
        ("份额增加成员比例后10集群", "share_increase_member_ratio", True, None),
        ("成交额前10集群", "total_amount_100m", False, None),
        ("成交额放大前10集群", "weekly_amount_vs_4w", False, None),
        ("涨幅前10集群", "weighted_pct_change", False, None),
        ("跌幅前10集群", "weighted_pct_change", True, None),
        ("温度前10集群", "cluster_temperature", False, None),
        ("升温前10集群", "weekly_temperature_change", False, None),
        ("降温前10集群", "weekly_temperature_change", True, None),
        ("最大同向ETF贡献比例前10集群", "max_contributor_ratio_1d", False, None),
        ("前3只ETF同向贡献比例前10集群", "top3_contributor_ratio_1d", False, None),
    ]
    lines.append("### 观察集群各类周度排名")
    lines.append("")
    for title, column, ascending, sign_filter in specs:
        if column not in weekly_cluster.columns:
            continue
        lines.append(f"#### {title}")
        lines.append("")
        lines.append(_weekly_cluster_rank_table(weekly_cluster, column, ascending, 10, sign_filter))
        lines.append("")
    lines.append("#### 单边流入集群排名")
    lines.append("")
    lines.append(_weekly_offset_table(weekly_cluster, {"单边流入"}, 10))
    lines.append("")
    lines.append("#### 高分歧换仓集群排名")
    lines.append("")
    lines.append(_weekly_offset_table(weekly_cluster, {"高分歧换仓"}, 10))
    lines.append("")


def _next_week_lines(weekly_cluster: pd.DataFrame) -> list[str]:
    flow_clusters = _flow_eligible_clusters(weekly_cluster)
    inflow = _top_cluster_names(flow_clusters, "weekly_net_subscription_100m", False, 1)
    outflow = _top_cluster_names(flow_clusters, "weekly_net_subscription_100m", True, 1)
    high = weekly_cluster[
        weekly_cluster.get("temperature_level", pd.Series("", index=weekly_cluster.index)).eq("高温")
    ]
    if high.empty:
        top_temperature = weekly_cluster.sort_values("cluster_temperature", ascending=False).head(1)
        if top_temperature.empty:
            temperature_watch = "当前无可用温度集群，继续等待统一温度字段补齐。"
        else:
            row = top_temperature.iloc[0]
            temperature_watch = (
                f"{row.get('cluster_name', '-')}当前为{row.get('temperature_stage', row.get('temperature_level', '-'))}，"
                "尚未达到高温，观察其温度阶段是否继续变化。"
            )
    else:
        row = high.sort_values("cluster_temperature", ascending=False).iloc[0]
        temperature_watch = f"{row.get('cluster_name', '-')}当前为{row.get('temperature_stage', '高温')}，观察高温阶段是否延续或降温。"
    turn = _top_cluster_names(weekly_cluster, "weekly_temperature_change", False, 1)
    concentrated = (
        weekly_cluster[_num_col(weekly_cluster, "max_contributor_ratio_1d") > 0.5]
        if "max_contributor_ratio_1d" in weekly_cluster
        else pd.DataFrame()
    )
    concentrated_name = "高集中度集群" if concentrated.empty else str(concentrated.sort_values("max_contributor_ratio_1d", ascending=False).iloc[0]["cluster_name"])
    return [
        f"1. {inflow}的周净申购能否在下周继续，并扩散到更多成员ETF。",
        f"2. {outflow}的周净赎回是否停止，避免形成连续两周资金收缩。",
        f"3. {temperature_watch}",
        f"4. {turn}的升温能否同时获得价格和份额确认。",
        f"5. {concentrated_name}是否仍由单一ETF主导，还是出现广度修复。",
    ]


def _formal_blocker_line(validation: dict) -> str:
    blockers = validation.get("formal_blockers") or []
    if not blockers:
        return "> 正式门槛：全部通过。"
    return "> 正式门槛未通过：" + "；".join(str(item) for item in blockers) + "。"


def _weekly_appendix_path(output_path: Path, week: str) -> Path:
    return output_path.with_name(f"A股ETF资金结构周报_技术附录_{week}.md")


def _validation_table(validation: dict) -> str:
    specs = [
        ("ETF覆盖率", "etf_coverage", "ratio"),
        ("完整周ETF覆盖率", "full_week_etf_coverage", "ratio"),
        ("行情覆盖率", "weekly_quote_coverage", "ratio"),
        ("资金流ETF数量覆盖率", "weekly_flow_coverage", "ratio"),
        ("资金流单元格覆盖率", "weekly_flow_cell_coverage", "ratio"),
        ("资金流规模加权覆盖率", "weekly_flow_size_coverage", "ratio"),
        ("缺失资金流对应规模", "missing_flow_size_100m", "money"),
        ("重要集群最低资金规模覆盖率", "material_cluster_flow_size_coverage_min", "ratio"),
        ("重要集群未达标数", "material_cluster_flow_coverage_below_threshold", "number"),
        ("分类规模覆盖率", "classified_scale_coverage", "ratio"),
        ("分类成交额覆盖率", "classified_turnover_coverage", "ratio"),
        ("分类资金流覆盖率", "classified_flow_coverage", "ratio"),
        ("核心锚点完整率", "weekly_core_anchor_coverage", "ratio"),
        ("观察集群数", "cluster_count", "number"),
        ("重要未分类ETF数", "important_unclassified_count", "number"),
        ("语义错分数", "semantic_membership_issue_count", "number"),
        ("周报分类快照错配数", "weekly_membership_mismatch_count", "number"),
    ]
    rows = []
    for label, key, kind in specs:
        value = validation.get(key)
        if kind == "ratio":
            text = _fmt_ratio(value)
        elif kind == "money":
            text = _fmt_money(value, signed=False)
        else:
            if isinstance(value, list):
                text = str(len(value))
            else:
                text = "-" if value is None or pd.isna(value) else str(value)
        rows.append({"指标": label, "结果": text})
    return pd.DataFrame(rows).to_markdown(index=False)


def _write_weekly_technical_appendix(
    week: str,
    snapshot: dict,
    weekly_etf: pd.DataFrame,
    weekly_cluster: pd.DataFrame,
    output_path: Path,
) -> Path:
    validation = snapshot.get("validation", {})
    top_n = int(get_report_config()["appendix_top_n"])
    lines = [f"# A股ETF资金结构周报_技术附录_{week}", ""]
    lines.append(_week_date_line(week, validation))
    lines.append("> 本附录保留完整审计、观察集群和单只ETF排行榜；正文仅保留决策相关的Top5。")
    lines.append("")
    lines.append("## 1. 数据质量与正式门槛")
    lines.append("")
    lines.append(_formal_blocker_line(validation))
    lines.append("")
    lines.append(_validation_table(validation))
    lines.append("")
    lines.append("## 2. ETF公司行为与异常调整")
    lines.append("")
    lines.extend(_corporate_action_audit_lines(weekly_etf))
    lines.append("")
    lines.append("## 3. 观察集群完整周度资金表")
    lines.append("")
    lines.append(_weekly_cluster_table(weekly_cluster))
    lines.append("")
    _append_weekly_cluster_rankings(lines, weekly_cluster)
    lines.append("## 4. 连续流入与连续流出")
    lines.append("")
    lines.append("### 全周连续净申购ETF")
    lines.append("")
    lines.append(_continuous_table(weekly_etf, "in"))
    lines.append("")
    lines.append("### 全周连续净赎回ETF")
    lines.append("")
    lines.append(_continuous_table(weekly_etf, "out"))
    lines.append("")
    lines.append("## 5. 资金集中度完整观察")
    lines.append("")
    lines.extend(_concentration_lines(weekly_cluster))
    lines.append("")
    lines.append("## 6. 单只ETF周度完整排行榜")
    lines.append("")
    _append_weekly_etf_rankings(lines, weekly_etf, top_n)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _corporate_action_audit_lines(weekly_etf: pd.DataFrame) -> list[str]:
    if weekly_etf.empty or "corporate_action_events" not in weekly_etf:
        return ["本周未记录ETF拆分、合并或疑似公司行为。"]

    rows: list[dict] = []
    for _, etf in weekly_etf.iterrows():
        payload = etf.get("corporate_action_events")
        if not isinstance(payload, str) or not payload.strip():
            continue
        try:
            events = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            rows.append(
                {
                    "ETF": f"{_fmt_code(etf.get('etf_code'))} {etf.get('etf_name', '')}".strip(),
                    "份额事件日": event.get("date") or "-",
                    "价格生效日": event.get("effective_price_date") or "-",
                    "类型": event.get("type") or "-",
                    "因子": _fmt_num(event.get("factor"), 4),
                    "原始涨跌幅": _fmt_pct(event.get("raw_pct_change")),
                    "调整后涨跌幅": _fmt_pct(event.get("adjusted_pct_change")),
                    "原始份额变化": _fmt_num(event.get("raw_share_change"), 0),
                    "调整后份额变化": _fmt_num(event.get("adjusted_share_change"), 0),
                    "处理结果": event.get("result") or "-",
                }
            )

    if not rows:
        return ["本周未记录ETF拆分、合并或疑似公司行为。"]

    table = pd.DataFrame(rows)
    high_confidence = int(table["处理结果"].eq("高置信度调整").sum())
    excluded = int(table["处理结果"].eq("排除").sum())
    failed = int(table["处理结果"].str.contains("调整失败", na=False).sum())
    return [
        f"- 本周公司行为事件：{len(table)}项；高置信度调整{high_confidence}项，疑似事件排除{excluded}项，调整失败{failed}项。",
        "- 拆分或合并产生的机械价格与份额变化不计入市场涨跌和申购赎回；调整失败的ETF不进入周度收益及资金流排名。",
        "",
        table.to_markdown(index=False),
    ]


def generate_weekly_report(
    week: str,
    snapshot: dict,
    weekly_etf: pd.DataFrame,
    weekly_cluster: pd.DataFrame,
    output_path: Path,
) -> Path:
    market = snapshot["market"]
    big = _aggregate_clusters(weekly_cluster, BIG, "大盘宽基")
    small = _aggregate_clusters(weekly_cluster, SMALL, "中小盘宽基")
    defense = _aggregate_clusters(weekly_cluster, DIVIDEND_DEFENSE, "红利防御")
    resource = _aggregate_clusters(weekly_cluster, RESOURCE_CYCLE, "资源周期")
    growth = _aggregate_clusters(weekly_cluster, GROWTH, "科技成长")

    lines = [f"# A股ETF资金结构周报 {week}", ""]
    validation = snapshot.get("validation", {})
    lines.append(_week_date_line(week, validation))
    lines.append("> 周涨跌幅口径：有效交易日收益复利；无异常调整时等同于本周最后一个交易日收盘价 / 上周最后一个交易日收盘价 - 1。成交额只表示二级市场换手，不写作资金流入。")
    lines.append(f"> 周报状态：{'正式周报' if validation.get('formal_weekly_allowed') else '未满足完整周门槛'}；交易日{validation.get('trading_days', '-')}天，ETF覆盖率{validation.get('etf_coverage', 0):.2%}。")
    lines.append(
        f"> 周数据质量：完整周覆盖率{validation.get('full_week_etf_coverage', 0):.2%}，"
        f"行情覆盖率{validation.get('weekly_quote_coverage', 0):.2%}，"
        f"资金流ETF数量覆盖率{validation.get('weekly_flow_coverage', 0):.2%}，"
        f"资金流规模加权覆盖率{validation.get('weekly_flow_size_coverage', 0):.2%}，"
        f"核心锚点完整率{validation.get('weekly_core_anchor_coverage', 0):.2%}，"
        f"语义错分{validation.get('semantic_membership_issue_count', 0)}只，"
        f"周报分类快照错配{validation.get('weekly_membership_mismatch_count', 0)}只。"
    )
    lines.append(_formal_blocker_line(validation))
    lines.append("")
    lines.append("## 1. 本周总判断")
    lines.append("")
    lines.append(_one_sentence(snapshot, weekly_cluster))
    lines.append(f"全市场周末规模{_fmt_money(market['total_size_100m'], signed=False)}，本周净申购{_fmt_money(market['weekly_net_subscription_100m'])}。")
    lines.append("")

    lines.append("## 2. 风格资金迁移")
    lines.append("")
    lines.append("### 大盘与小盘")
    lines.append("")
    lines.append(_style_line(big, small))
    lines.append("")
    lines.append("### 红利防御、资源周期与成长")
    lines.append("")
    lines.append(_style_line_three([defense, resource, growth]))
    lines.append("")

    lines.append("## 3. 集群资金迁移Top5")
    lines.append("")
    lines.append("### 净申购Top5")
    lines.append("")
    lines.append(_compact_weekly_cluster_flow_table(weekly_cluster, ascending=False, sign_filter="positive"))
    lines.append("")
    lines.append("### 净赎回Top5")
    lines.append("")
    lines.append(_compact_weekly_cluster_flow_table(weekly_cluster, ascending=True, sign_filter="negative"))
    lines.append("")

    lines.append("## 4. 资金速度、反转与内部结构")
    lines.append("")
    lines.append("### 本周资金速度与周初周末方向")
    lines.append("")
    lines.extend(_weekly_flow_speed_lines(weekly_cluster))
    lines.append("")
    lines.append("### 周初到周末的结构变化")
    lines.append("")
    lines.extend(_week_turn_lines(weekly_etf))
    lines.append("")
    lines.append("### 单日异动与持续趋势")
    lines.append("")
    lines.extend(_single_day_vs_trend_lines(weekly_etf, _flow_eligible_clusters(weekly_cluster)))
    lines.append("")
    lines.append("### 资金集中度重点")
    lines.append("")
    lines.extend(_concentration_lines(_flow_eligible_clusters(weekly_cluster))[:3])
    lines.append("")
    lines.append("### 单边流入与高分歧换仓排名")
    lines.append("")
    lines.append("#### 单边流入Top5")
    lines.append("")
    lines.append(_weekly_offset_table(weekly_cluster, {"单边流入"}, 5))
    lines.append("")
    lines.append("#### 高分歧换仓Top5")
    lines.append("")
    lines.append(_weekly_offset_table(weekly_cluster, {"高分歧换仓"}, 5))
    lines.append("")
    lines.append("### 金额与广度是否同步")
    lines.append("")
    lines.extend(_weekly_amount_breadth_lines(weekly_cluster))
    lines.append("")

    lines.append("## 5. 下周观察项")
    lines.append("")
    lines.extend(_next_week_lines(weekly_cluster))
    lines.append("")
    appendix_path = _weekly_appendix_path(output_path, week)
    lines.append("## 6. 口径与附录")
    lines.append("")
    lines.append("- 资金流使用ETF份额变化乘以前一交易日净值估算；它是申购赎回方向代理，不是交易所逐笔申赎数据。")
    lines.append("- 集群资金排名只纳入资金规模覆盖率达到门槛的集群；低覆盖集群不进入正文方向性结论。")
    lines.append(f"- 完整数据质量、公司行为审计、观察集群全表和单只ETF Top{int(get_report_config()['appendix_top_n'])}见 `{appendix_path.name}`。")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_weekly_technical_appendix(week, snapshot, weekly_etf, weekly_cluster, appendix_path)
    return output_path
