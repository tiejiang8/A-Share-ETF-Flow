from __future__ import annotations

from pathlib import Path

import pandas as pd

from ._atomic import atomic_write_text
from .config import get_report_config, load_temperature_config
from .flow_structure import flow_structure_config
from .temperature import temperature_band_definition


COLUMN_LABELS = {
    "cluster_id": "集群ID",
    "cluster_name": "集群名称",
    "member_count": "成员数",
    "representative_code": "代表ETF代码",
    "representative_name": "代表ETF名称",
    "etf_code": "ETF代码",
    "etf_name": "ETF名称",
    "primary_cluster_name": "所属集群",
    "tracking_index_name": "跟踪指数",
    "abnormal_source": "异常来源",
    "abnormal_signal_count": "异动信号数",
    "abnormal_score": "异动评分",
    "tracking_priority": "跟踪优先级",
    "price_share_signal": "价量份额信号",
    "total_size_100m": "总规模(亿元)",
    "estimated_net_subscription_100m": "单日估算净申购(亿元)",
    "estimated_net_subscription_5d_100m": "5日估算净申购(亿元)",
    "flow_1d": "单日资金(亿元)",
    "flow_5d": "5日资金(亿元)",
    "flow_5d_valid_days": "5日有效交易日数",
    "flow_5d_daily_avg": "5日资金日均(亿元)",
    "flow_prev4_valid_days": "前4日有效交易日数",
    "flow_prev4_total": "前4日资金(亿元)",
    "flow_prev4_daily_avg": "前4日资金日均(亿元)",
    "flow_acceleration_vs_prev4": "相对前4日资金加速度(亿元)",
    "flow_acceleration_ratio_vs_prev4": "相对前4日资金加速度比率",
    "flow_previous_4d": "此前4日资金(亿元)",
    "flow_acceleration": "资金加速度(亿元)",
    "flow_acceleration_ratio": "资金加速度比率",
    "flow_speed_status": "资金速度状态",
    "gross_inflow": "毛流入(亿元)",
    "gross_outflow": "毛流出(亿元)",
    "net_flow": "净流量(亿元)",
    "gross_turnover_flow": "资金毛流量(亿元)",
    "flow_offset_ratio": "资金抵消率",
    "flow_offset_status": "内部资金状态",
    "max_inflow_etf_code": "最大流入ETF",
    "max_inflow_etf_name": "最大流入ETF名称",
    "max_inflow_etf_flow": "最大流入金额(亿元)",
    "max_outflow_etf_code": "最大流出ETF",
    "max_outflow_etf_name": "最大流出ETF名称",
    "max_outflow_etf_flow": "最大流出金额(亿元)",
    "flow_structure_label": "资金结构标签",
    "weighted_pct_change": "规模加权涨跌幅",
    "total_amount_100m": "成交额(亿元)",
    "amount": "成交额(亿元)",
    "amount_vs_5d": "成交额/前5日均值",
    "share_increase_member_ratio": "份额增加成员占比",
    "member_up_ratio": "上涨成员占比",
    "cluster_temperature": "集群温度",
    "temperature": "温度",
    "temperature_change": "温度变化",
    "cluster_temperature_change_1d": "1日温度变化",
    "cluster_temperature_change_3d": "3日温度变化",
    "cluster_temperature_slope_5d": "5日温度斜率",
    "temperature_direction": "温度方向",
    "temperature_level": "温度层级",
    "temperature_phase": "温度阶段",
    "temperature_stage": "温度阶段",
    "absolute_temperature": "绝对温度",
    "market_percentile_temperature": "全市场分位温度",
    "cluster_percentile_temperature": "集群内分位温度",
    "final_temperature": "合成温度",
    "temperature_confidence": "温度置信度",
    "cluster_temperature_confidence": "集群温度置信度",
    "one_day_flow_size_coverage": "单日资金流有效规模覆盖率",
    "five_day_flow_size_coverage": "5日资金流有效规模覆盖率",
    "flow_size_coverage": "资金流有效规模覆盖率",
    "structure_judgement": "结构判断",
    "one_day_flow_to_size": "日净申购/规模",
    "five_day_flow_to_size": "5日净申购/规模",
    "max_contributor_code_1d": "日同向最大贡献ETF",
    "max_contributor_name_1d": "日同向最大贡献ETF名称",
    "max_contributor_flow_1d_100m": "日同向最大贡献金额(亿元)",
    "same_direction_flow_total_1d_100m": "日同向资金总额(亿元)",
    "max_contributor_ratio_1d": "日同向贡献率",
    "top3_contributor_ratio_1d": "前3同向贡献率",
    "share_daily_change": "份额日变化(份)",
    "share_daily_change_pct": "份额日变化率",
    "pct_change": "涨跌幅",
    "premium_discount": "折溢价率",
    "is_in_31_clusters": "是否在观察集群内",
    "one_line_explanation": "一句话解释",
    "sample_note": "样本提示",
    "watch_reason": "观察理由",
}

PCT_COLUMNS = {
    "pct_change",
    "weighted_pct_change",
}

RATIO_PCT_COLUMNS = {
    "share_increase_member_ratio",
    "member_up_ratio",
    "one_day_flow_to_size",
    "five_day_flow_to_size",
    "max_contributor_ratio_1d",
    "max_contributor_ratio_5d",
    "top3_contributor_ratio_1d",
    "top3_contributor_ratio_5d",
    "temperature_confidence",
    "cluster_temperature_confidence",
    "one_day_flow_size_coverage",
    "five_day_flow_size_coverage",
    "flow_size_coverage",
    "share_daily_change_pct",
    "share_5d_change_pct",
    "premium_discount",
    "flow_acceleration_ratio",
    "flow_acceleration_ratio_vs_prev4",
    "flow_offset_ratio",
}

YUAN_TO_100M_COLUMNS = {"amount"}

COUNT_COLUMNS = {"member_count", "abnormal_signal_count", "tracking_priority", "flow_5d_valid_days", "flow_prev4_valid_days"}

CODE_COLUMNS = {"etf_code", "representative_code", "max_inflow_etf_code", "max_outflow_etf_code"}


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


def _fmt_pct(value, digits: int = 2) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}%"


def _fmt_int(value) -> str:
    if pd.isna(value):
        return "-"
    return f"{int(round(float(value))):,}"


def _fmt_code(value) -> str:
    if pd.isna(value):
        return "-"
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def _fmt_weight(value) -> str:
    if pd.isna(value):
        return "-"
    number = float(value)
    return f"{number:.0%}" if abs(number * 100 - round(number * 100)) < 0.000001 else f"{number:.1%}"


def _fmt_value(value, column: str) -> str:
    if pd.isna(value):
        return "-"
    if column in CODE_COLUMNS:
        return _fmt_code(value)
    if column in COUNT_COLUMNS:
        return _fmt_int(value)
    if column in PCT_COLUMNS:
        return _fmt_pct(value)
    if column in RATIO_PCT_COLUMNS:
        return _fmt_pct(float(value) * 100)
    if column == "is_in_31_clusters":
        return "是" if bool(value) else "否"
    if column in YUAN_TO_100M_COLUMNS:
        return _fmt_num(float(value) / 1e8)
    if column in {"share_daily_change", "share_5d_change", "latest_shares"}:
        return _fmt_int(value)
    if column in {"amount_vs_5d"}:
        return f"{_fmt_num(value)}倍"
    if column in {
        "total_size_100m",
        "estimated_net_subscription_100m",
        "estimated_net_subscription_5d_100m",
        "flow_1d",
        "flow_5d",
        "flow_5d_daily_avg",
        "flow_prev4_total",
        "flow_prev4_daily_avg",
        "flow_acceleration_vs_prev4",
        "flow_previous_4d",
        "flow_acceleration",
        "gross_inflow",
        "gross_outflow",
        "net_flow",
        "gross_turnover_flow",
        "max_inflow_etf_flow",
        "max_outflow_etf_flow",
        "total_amount_100m",
        "cluster_temperature",
        "temperature",
        "temperature_change",
        "cluster_temperature_change_1d",
        "cluster_temperature_change_3d",
        "cluster_temperature_slope_5d",
        "abnormal_score",
    }:
        return _fmt_num(value)
    return str(value)


def _format_table(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    if df.empty:
        return "无数据。"
    cols = []
    for col in columns or list(df.columns):
        if col in df.columns and col not in cols:
            cols.append(col)
    table = df[cols].copy()
    for col in cols:
        table[col] = table[col].map(lambda value, c=col: _fmt_value(value, c))
    table = table.rename(columns={col: COLUMN_LABELS.get(col, col) for col in cols})
    return table.to_markdown(index=False)


def _top_table(df: pd.DataFrame, column: str, n: int = 10, ascending: bool = False) -> str:
    if df.empty or column not in df:
        return "无数据。"
    cols = []
    for col in ["etf_code", "etf_name", "primary_cluster_name", column, "pct_change", "temperature"]:
        if col not in cols:
            cols.append(col)
    table = df.sort_values(column, ascending=ascending)[cols].head(n).copy()
    return _format_table(table, cols)


def _weight_text(weights: dict, labels: dict[str, str]) -> str:
    parts = []
    for key, value in weights.items():
        parts.append(f"{labels.get(key, key)}{_fmt_weight(value)}")
    return "、".join(parts)


def _methodology_lines() -> list[str]:
    config = load_temperature_config()
    report_config = get_report_config()
    cluster_labels = {
        "weighted_price_trend": "价格趋势",
        "cluster_momentum": "短期动量",
        "turnover_relative_level": "成交活跃",
        "one_day_net_subscription_to_size": "单日净申购/规模",
        "five_day_net_subscription_to_size": "5日净申购/规模",
        "member_breadth": "成员广度",
        "premium_discount_risk": "折溢价风险",
    }
    product_labels = {
        "price_trend": "价格趋势",
        "short_momentum": "短期动量",
        "turnover_activity": "成交活跃",
        "one_day_net_subscription_to_size": "单日净申购/规模",
        "five_day_net_subscription_to_size": "5日净申购/规模",
        "price_volume_share_consistency": "价量份额一致性",
        "premium_discount_risk": "折溢价风险",
    }
    final_labels = {
        "absolute_temperature": "绝对温度",
        "market_percentile_temperature": "全市场分位",
        "cluster_percentile_temperature": "集群内分位",
    }
    thresholds = config["thresholds"]
    return [
        "- 温度为0-100分的相对热度指标，不是收益预测；越高表示价格、份额、成交和广度组合读数越热。",
        f"- 集群温度权重：{_weight_text(config['cluster_temperature_weights'], cluster_labels)}。",
        f"- 单只ETF先按{_weight_text(config['product_temperature_weights'], product_labels)}生成绝对温度，再按{_weight_text(config['final_temperature_weights'], final_labels)}合成；数据缺项会按置信度向50分收敛。",
        f"- 温度分层：{temperature_band_definition()}；升降温由统一计算字段中的1日、3日和5日斜率共同判断。",
        f"- 正文资金迁移每个方向最多展示Top{report_config['main_cluster_flow_top_n']}，综合异常ETF展示Top{report_config['main_abnormal_top_n']}；附录保留Top{report_config['appendix_top_n']}和去重观察池Top{report_config['watchlist_top_n']}；单只K线最多展示前{min(int(report_config['main_chart_top_n']), 5)}张。",
        "- 若交易所提供最小申购赎回单位，则同步计算份额变化对应的申赎单位数和整数贴合度，作为估算资金流的辅助校验；该字段缺失不等于资金流为零。",
        f"- 连续热度识别阈值：高温>={thresholds['high_temperature']}，高位降温锚点>={thresholds['cooling_from_high_min_previous_temperature']}。",
    ]


def _history_evidence_lines(metrics: dict) -> list[str]:
    report_config = get_report_config()
    min_days = int(report_config["min_backtest_trading_days"])
    days = int(metrics.get("history_trading_days", 0) or 0)
    if days < min_days:
        return [
            f"- 历史有效性：当前本地历史样本 {days} 个交易日，未达到 {min_days} 个交易日；不输出胜率、平均收益和最大回撤。",
        ]
    return [
        f"- 历史有效性：当前本地历史样本 {days} 个交易日，已达到 {min_days} 个交易日门槛；胜率模块需要单独按信号类型回测后再发布。",
    ]


def _investment_usage_lines() -> list[str]:
    return [
        "- 价涨份额增：趋势确认候选，关注延续性，避免高温极值处追涨。",
        "- 价跌份额增：逆向承接候选，只进入观察池，等待次日份额延续、成交不塌和价格止跌确认。",
        "- 价跌份额降：风险释放或资金撤退，降低优先级，除非后续出现止跌和份额回补。",
        "- 高温降温：拥挤退潮信号，优先做风控；低温升温只代表冷启动，不能单独构成买入理由。",
    ]


def _turnover_band(value) -> str:
    if pd.isna(value):
        return "-"
    number = float(value)
    if number >= 1.3:
        label = "放大"
    elif number <= 0.8:
        label = "收缩"
    else:
        label = "正常"
    return f"{_fmt_num(number)}倍 {label}"


def _flow_phrase(day_value, five_day_value) -> str:
    return f"日{_fmt_signed_num(day_value)}亿 / 5日{_fmt_signed_num(five_day_value)}亿"


def _cluster_cards_table(cluster_daily: pd.DataFrame, representatives: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "无数据。"
    reps = (
        representatives[["cluster_id", "representative_code", "representative_name"]]
        if not representatives.empty
        else pd.DataFrame(columns=["cluster_id", "representative_code", "representative_name"])
    )
    table = cluster_daily.merge(reps, on="cluster_id", how="left")
    rows = []
    for _, row in table.iterrows():
        code = _fmt_code(row.get("representative_code"))
        name = "" if pd.isna(row.get("representative_name")) else str(row.get("representative_name"))
        rows.append(
            {
                "集群": f"{row['cluster_id']} {row['cluster_name']}",
                "代表ETF": f"{code} {name}".strip(),
                "资金": _flow_phrase(
                    row.get("estimated_net_subscription_100m"),
                    row.get("estimated_net_subscription_5d_100m"),
                ),
                "价格": _fmt_pct(row.get("weighted_pct_change")),
                "成交": _turnover_band(row.get("amount_vs_5d")),
                "份额面": f"{_fmt_pct(float(row['share_increase_member_ratio']) * 100) if pd.notna(row.get('share_increase_member_ratio')) else '-'}增份额",
                "温度": _temperature_detail(row),
                "信号": row.get("structure_judgement", "-"),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _markdown_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def one_sentence(cluster_daily: pd.DataFrame) -> str:
    from .narrative import composite_one_sentence

    return composite_one_sentence(cluster_daily)


def data_quality_table(metrics: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["A股权益ETF数量", metrics.get("a_share_equity_etf_count", 0), "ok"],
            ["行情成功数量", f"{metrics.get('quote_success_count', 0)} / {metrics.get('quote_coverage', 0):.2%}", "ok" if metrics.get("quote_coverage", 0) >= 0.98 else "不足"],
            ["份额成功数量", f"{metrics.get('share_success_count', 0)} / {metrics.get('share_coverage', 0):.2%}", "ok" if metrics.get("share_coverage", 0) >= 0.95 else "不足"],
            ["净值成功数量", f"{metrics.get('nav_success_count', 0)} / {metrics.get('nav_coverage', 0):.2%}", "ok" if metrics.get("nav_coverage", 0) >= 0.95 else "不足"],
            ["核心锚点完整率", f"{metrics.get('core_anchor_coverage', 0):.2%}", "ok" if metrics.get("core_anchor_coverage", 0) == 1 else "不足"],
            ["分类规模覆盖率", f"{metrics.get('classified_scale_coverage', 0):.2%}", "ok" if metrics.get("classified_scale_coverage", 0) >= 0.95 else "不足"],
            ["极端资金流待复核", metrics.get("extreme_flow_unresolved_count", 0), "ok" if not metrics.get("extreme_flow_unresolved_count", 0) else "需复核"],
            ["公司行为异常数量", metrics.get("corporate_action_exception_count", 0), "需复核" if metrics.get("corporate_action_exception_count", 0) else "ok"],
        ],
        columns=["项目", "数量/比例", "状态"],
    )


def cluster_table(cluster_daily: pd.DataFrame, representatives: pd.DataFrame) -> pd.DataFrame:
    reps = representatives[["cluster_id", "representative_code", "representative_name"]] if not representatives.empty else pd.DataFrame(columns=["cluster_id"])
    table = cluster_daily.merge(reps, on="cluster_id", how="left")
    cols = [
        "cluster_name",
        "total_size_100m",
        "estimated_net_subscription_100m",
        "one_day_flow_to_size",
        "estimated_net_subscription_5d_100m",
        "cluster_temperature",
        "temperature_direction",
        "structure_judgement",
    ]
    return table[cols]


def _short_text(text: str, limit: int = 180) -> str:
    text = str(text).strip()
    if len(text) <= limit:
        return text
    candidates = []
    for mark in ["。", "；"]:
        pos = text.rfind(mark, 0, limit)
        if pos >= int(limit * 0.55):
            candidates.append(pos + 1)
    if not candidates:
        pos = text.rfind("，", 0, limit)
        if pos >= int(limit * 0.65):
            candidates.append(pos + 1)
    cut = max(candidates) if candidates else limit
    return text[:cut].rstrip("，；。 ") + "。"


def _cluster_names(cluster_daily: pd.DataFrame, column: str, *, ascending: bool = False, n: int = 3) -> str:
    if cluster_daily.empty or column not in cluster_daily:
        return "无"
    table = cluster_daily.dropna(subset=[column]).sort_values(column, ascending=ascending).head(n)
    if table.empty:
        return "无"
    return "、".join(table["cluster_name"].astype(str).tolist())


def _first_cluster_metric(cluster_daily: pd.DataFrame, column: str, *, ascending: bool = False) -> tuple[str, float]:
    if cluster_daily.empty or column not in cluster_daily:
        return "无", float("nan")
    table = cluster_daily.dropna(subset=[column]).sort_values(column, ascending=ascending).head(1)
    if table.empty:
        return "无", float("nan")
    row = table.iloc[0]
    return str(row.get("cluster_name", "无")), float(row.get(column))


def _flow_eligible_clusters(cluster_daily: pd.DataFrame, coverage_column: str = "flow_size_coverage") -> pd.DataFrame:
    if cluster_daily.empty or coverage_column not in cluster_daily:
        return cluster_daily
    threshold = float(
        load_temperature_config()["thresholds"].get("daily_min_cluster_flow_size_coverage", 0.90)
    )
    coverage = pd.to_numeric(cluster_daily[coverage_column], errors="coerce")
    return cluster_daily[coverage.ge(threshold)].copy()


def _research_summary(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "数据不足，暂不生成正式市场判断。"
    day_clusters = _flow_eligible_clusters(cluster_daily, "one_day_flow_size_coverage")
    five_clusters = _flow_eligible_clusters(cluster_daily, "five_day_flow_size_coverage")
    day_in_name, day_in = _first_cluster_metric(day_clusters, "estimated_net_subscription_100m", ascending=False)
    day_out_name, day_out = _first_cluster_metric(day_clusters, "estimated_net_subscription_100m", ascending=True)
    five_in_name, five_in = _first_cluster_metric(five_clusters, "estimated_net_subscription_5d_100m", ascending=False)
    price_up = _cluster_names(cluster_daily, "weighted_pct_change", ascending=False, n=3)
    price_down = _cluster_names(cluster_daily, "weighted_pct_change", ascending=True, n=3)
    hot_clusters = cluster_daily[
        cluster_daily.get("temperature_level", pd.Series("", index=cluster_daily.index)).eq("高温")
    ]
    hot = _cluster_names(hot_clusters, "cluster_temperature", ascending=False, n=3)
    hot_text = f"高温集群集中在{hot}" if hot != "无" else "当前无达到75分的高温集群"
    flow = pd.to_numeric(day_clusters.get("estimated_net_subscription_100m"), errors="coerce")
    pct = pd.to_numeric(day_clusters.get("weighted_pct_change"), errors="coerce")
    reverse = day_clusters[(flow > 0) & (pct < 0)].sort_values("estimated_net_subscription_100m", ascending=False)
    trend = day_clusters[(flow > 0) & (pct > 0)].sort_values("estimated_net_subscription_100m", ascending=False)
    reverse_names = "、".join(reverse["cluster_name"].astype(str).head(3).tolist()) if not reverse.empty else "无"
    trend_names = "、".join(trend["cluster_name"].astype(str).head(3).tolist()) if not trend.empty else "无"
    return (
        f"今日ETF资金主线是{day_in_name}单日承接最强（{_fmt_signed_num(day_in, 1)}亿元），"
        f"{five_in_name}延续5日净申购优势（{_fmt_signed_num(five_in, 1)}亿元），"
        f"{day_out_name}是主要净赎回方向（{_fmt_signed_num(day_out, 1)}亿元）。"
        f"价格相对占优的是{price_up}，相对承压的是{price_down}；"
        f"{hot_text}。结构上，{reverse_names}更像下跌中的逆向承接，"
        f"{trend_names}更像价格与份额同步走强。"
    )


def _cluster_matrix_table(cluster_daily: pd.DataFrame, representatives: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "无数据。"
    rows = []
    for _, row in cluster_daily.sort_values("cluster_id").iterrows():
        rows.append(
            {
                "集群名称": row.get("cluster_name", "-"),
                "当日涨跌幅": _fmt_pct(row.get("weighted_pct_change")),
                "单日估算净申购": f"{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿",
                "5日估算净申购": f"{_fmt_signed_num(row.get('estimated_net_subscription_5d_100m'), 1)}亿",
                "单日净申购/规模": _fmt_value(row.get("one_day_flow_to_size"), "one_day_flow_to_size"),
                "份额增加成员比例": _fmt_value(row.get("share_increase_member_ratio"), "share_increase_member_ratio"),
                "最大同向资金贡献ETF": _contributor_text(row),
                "前3同向贡献比例": _top3_contributor_text(row),
                "成交额/前5日均值": _fmt_value(row.get("amount_vs_5d"), "amount_vs_5d"),
                "温度": _temperature_detail(row),
                "温度方向": row.get("temperature_direction", "-"),
                "温度阶段": row.get("temperature_stage", row.get("temperature_phase", "-")),
                "结构判断": row.get("structure_judgement", "-"),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _single_member_cluster_note(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty or "member_count" not in cluster_daily:
        return ""
    member_count = pd.to_numeric(cluster_daily["member_count"], errors="coerce")
    single = cluster_daily[member_count.le(1)]
    if single.empty:
        return ""
    names = "、".join(single.sort_values("cluster_id")["cluster_name"].astype(str).tolist())
    return f"样本提示：{names}为单ETF集群，方向信号代表性弱于多成员集群。"


def _cluster_rank_table(
    cluster_daily: pd.DataFrame,
    column: str,
    title: str,
    ascending: bool = False,
    n: int = 10,
    sign_filter: str | None = None,
) -> str:
    if cluster_daily.empty or column not in cluster_daily:
        return "无数据。"
    table = cluster_daily.dropna(subset=[column]).copy()
    if column in {"max_contributor_ratio_1d", "top3_contributor_ratio_1d"}:
        minimum = int(get_report_config().get("min_concentration_member_count", 4))
        table = table.loc[_concentration_row_mask(table)]
    values = pd.to_numeric(table[column], errors="coerce")
    if sign_filter == "positive":
        table = table[values.gt(0)]
    elif sign_filter == "negative":
        table = table[values.lt(0)]
    if table.empty:
        if sign_filter == "positive":
            return "无净申购集群。"
        if sign_filter == "negative":
            return "无净赎回集群。"
        return "无数据。"
    cols = [
        "cluster_id",
        "cluster_name",
        "member_count",
        "total_size_100m",
        column,
        "weighted_pct_change",
        "estimated_net_subscription_100m",
        "estimated_net_subscription_5d_100m",
        "share_increase_member_ratio",
        "amount_vs_5d",
        "cluster_temperature",
        "temperature_direction",
        "temperature_stage",
        "max_contributor_code_1d",
        "max_contributor_name_1d",
        "max_contributor_ratio_1d",
        "top3_contributor_ratio_1d",
        "structure_judgement",
    ]
    table = table.sort_values(column, ascending=ascending).head(n)
    note = ""
    if sign_filter and len(table) < n:
        direction = "净申购" if sign_filter == "positive" else "净赎回"
        note = f"仅列示{len(table)}个{direction}集群；其余集群未满足该方向。"
    rendered = _format_table(table, cols)
    return f"{note}\n\n{rendered}" if note else rendered


def _compact_cluster_flow_table(
    cluster_daily: pd.DataFrame,
    *,
    ascending: bool,
    sign_filter: str,
    n: int = 5,
) -> str:
    if cluster_daily.empty or "estimated_net_subscription_100m" not in cluster_daily:
        return "无数据。"
    table = _flow_eligible_clusters(cluster_daily, "one_day_flow_size_coverage")
    if table.empty:
        return "无满足资金规模覆盖门槛的集群。"
    flow = pd.to_numeric(table["estimated_net_subscription_100m"], errors="coerce")
    table = table[flow.gt(0) if sign_filter == "positive" else flow.lt(0)].copy()
    table = table.sort_values("estimated_net_subscription_100m", ascending=ascending).head(n)
    if table.empty:
        return "无符合方向的集群。"
    direction = "净申购" if sign_filter == "positive" else "净赎回"
    note = f"仅列示{len(table)}个{direction}集群；其余集群未满足该方向或资金规模覆盖门槛。" if len(table) < n else ""
    rows = []
    for _, row in table.iterrows():
        rows.append(
            {
                "集群": row.get("cluster_name", "-"),
                "单日估算净申购": f"{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿",
                "5日估算净申购": f"{_fmt_signed_num(row.get('estimated_net_subscription_5d_100m'), 1)}亿",
                "前4日资金日均": f"{_fmt_signed_num(row.get('flow_prev4_daily_avg'), 1)}亿",
                "资金速度": row.get("flow_speed_status", "-"),
                "净申购/规模": _fmt_value(row.get("one_day_flow_to_size"), "one_day_flow_to_size"),
                "成员广度": _fmt_value(row.get("share_increase_member_ratio"), "share_increase_member_ratio"),
                "毛流入/毛流出": (
                    f"{_fmt_signed_num(row.get('gross_inflow'), 1)} / {_fmt_signed_num(row.get('gross_outflow'), 1)}亿"
                    if pd.notna(row.get("gross_inflow")) and pd.notna(row.get("gross_outflow"))
                    else "-"
                ),
                "抵消率/内部状态": (
                    f"{_fmt_value(row.get('flow_offset_ratio'), 'flow_offset_ratio')} / {row.get('flow_offset_status', '-')}"
                ),
                "资金结构标签": row.get("flow_structure_label", "-"),
                "有效规模": _fmt_value(row.get("one_day_flow_size_coverage"), "one_day_flow_size_coverage"),
                "最大同向ETF贡献": _contributor_text(row),
                "涨跌幅": _fmt_pct(row.get("weighted_pct_change")),
            }
        )
    rendered = pd.DataFrame(rows).to_markdown(index=False)
    return f"{note}\n\n{rendered}" if note else rendered


def _append_key_cluster_tables(lines: list[str], cluster_daily: pd.DataFrame) -> None:
    specs = [
        ("单日净申购前10集群", "estimated_net_subscription_100m", False, "positive"),
        ("单日净赎回前10集群", "estimated_net_subscription_100m", True, "negative"),
        ("5日净申购前10集群", "estimated_net_subscription_5d_100m", False, "positive"),
        ("5日净赎回前10集群", "estimated_net_subscription_5d_100m", True, "negative"),
        ("份额增加成员比例前10集群", "share_increase_member_ratio", False, None),
        ("份额增加成员比例后10集群", "share_increase_member_ratio", True, None),
        ("成交额前10集群", "total_amount_100m", False, None),
        ("成交额放大前10集群", "amount_vs_5d", False, None),
        ("价格最强前10集群", "weighted_pct_change", False, None),
        ("价格最弱前10集群", "weighted_pct_change", True, None),
        ("温度前10集群", "cluster_temperature", False, None),
        ("升温前10集群", "temperature_change", False, None),
        ("降温前10集群", "temperature_change", True, None),
        ("最大同向ETF贡献比例前10集群", "max_contributor_ratio_1d", False, None),
        ("前3只ETF贡献比例前10集群", "top3_contributor_ratio_1d", False, None),
    ]
    for title, column, ascending, sign_filter in specs:
        if column not in cluster_daily.columns:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.append(_cluster_rank_table(cluster_daily, column, title, ascending=ascending, n=10, sign_filter=sign_filter))
        lines.append("")
    if {"weighted_pct_change", "estimated_net_subscription_100m"}.issubset(cluster_daily.columns):
        price = pd.to_numeric(cluster_daily["weighted_pct_change"], errors="coerce")
        flow = pd.to_numeric(cluster_daily["estimated_net_subscription_100m"], errors="coerce")
        quadrant_specs = [
            ("价涨份额降前10集群", (price > 0) & (flow < 0), "weighted_pct_change", False),
            ("价跌份额增前10集群", (price < 0) & (flow > 0), "estimated_net_subscription_100m", False),
            ("价涨份额增前10集群", (price > 0) & (flow > 0), "estimated_net_subscription_100m", False),
            ("价跌份额降前10集群", (price < 0) & (flow < 0), "estimated_net_subscription_100m", True),
        ]
        for title, mask, column, ascending in quadrant_specs:
            lines.append(f"### {title}")
            lines.append("")
            lines.append(_cluster_rank_table(cluster_daily[mask].copy(), column, title, ascending=ascending, n=10))
            lines.append("")


def _abnormal_pool_brief(abnormal_pool: pd.DataFrame, top_n: int = 20) -> str:
    if abnormal_pool.empty:
        return "无。"
    cols = [
        "tracking_priority",
        "etf_code",
        "etf_name",
        "primary_cluster_name",
        "abnormal_signal_count",
        "abnormal_source",
        "pct_change",
        "estimated_net_subscription_100m",
        "share_daily_change_pct",
        "premium_discount",
        "temperature",
        "temperature_direction",
        "temperature_stage",
    ]
    return _format_table(abnormal_pool.head(top_n), cols)


def _appendix_path(report_path: Path, report_date: str) -> Path:
    return report_path.with_name(f"A股ETF资金温度日报_技术附录_{report_date}.md")


def _quality_missing_line(metrics: dict) -> str:
    parts = []
    for label, key in [("行情缺失", "missing_quote_codes"), ("份额缺失", "missing_share_codes"), ("净值缺失", "missing_nav_codes")]:
        codes = metrics.get(key, []) or []
        parts.append(f"{label}: {', '.join(map(str, codes[:20])) if codes else '无'}")
    return "；".join(parts)


def _formal_blocker_line(metrics: dict) -> str:
    blockers = metrics.get("formal_blockers") or []
    return "无" if not blockers else "；".join(map(str, blockers))


def _safe_sum(frame: pd.DataFrame, column: str, *, yuan_to_100m: bool = False) -> float:
    if frame.empty or column not in frame:
        return float("nan")
    values = pd.to_numeric(frame[column], errors="coerce")
    if not values.notna().any():
        return float("nan")
    total = float(values.sum())
    return total / 1e8 if yuan_to_100m else total


def _fmt_signed_plain(value, digits: int = 1) -> str:
    if pd.isna(value):
        return "-"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.{digits}f}"


def _temperature_detail(row: pd.Series) -> str:
    temp = row.get("cluster_temperature")
    level = row.get("temperature_level", "-")
    value = "-" if pd.isna(temp) else f"{_fmt_num(temp, 1)} {level}"
    change_1d = row.get("cluster_temperature_change_1d", row.get("temperature_change"))
    change_3d = row.get("cluster_temperature_change_3d")
    slope_5d = row.get("cluster_temperature_slope_5d")
    return (
        f"{value}；1日{_fmt_signed_plain(change_1d)}，"
        f"3日{_fmt_signed_plain(change_3d)}，5日斜率{_fmt_signed_plain(slope_5d)}"
    )


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
    flow = pd.to_numeric(pd.Series([row.get("estimated_net_subscription_100m")]), errors="coerce").iloc[0]
    flow_to_size = pd.to_numeric(pd.Series([row.get("one_day_flow_to_size")]), errors="coerce").iloc[0]
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
    flow_text = f"{_fmt_signed_num(flow, 1)}亿" if pd.notna(flow) else "金额-"
    members = pd.to_numeric(pd.Series([row.get("member_count")]), errors="coerce").iloc[0]
    if not _concentration_sample_valid(row):
        member_text = _fmt_int(members) if pd.notna(members) else "不足"
        return f"{code}{name_text}（{flow_text}；样本{member_text}只，不作集中度判断）"
    ratio = row.get("max_contributor_ratio_1d")
    cluster_flow = row.get("estimated_net_subscription_100m")
    direction = "同向流入" if pd.notna(cluster_flow) and float(cluster_flow) > 0 else "同向流出"
    if not _concentration_materiality_valid(row):
        return f"{code}{name_text}（{flow_text}，占{direction}{_fmt_value(ratio, 'max_contributor_ratio_1d')}；资金规模较小）"
    return f"{code}{name_text}（{flow_text}，占{direction}{_fmt_value(ratio, 'max_contributor_ratio_1d')}）"


def _top3_contributor_text(row: pd.Series) -> str:
    if not _concentration_sample_valid(row):
        return "样本不足"
    if not _concentration_materiality_valid(row):
        return "金额不足"
    return _fmt_value(row.get("top3_contributor_ratio_1d"), "top3_contributor_ratio_1d")


def _cluster_subset(cluster_daily: pd.DataFrame, ids: list[str] | set[str]) -> pd.DataFrame:
    return cluster_daily[cluster_daily["cluster_id"].isin(set(ids))].copy() if not cluster_daily.empty else cluster_daily


def _aggregate_clusters(cluster_daily: pd.DataFrame, ids: list[str] | set[str], name: str) -> dict:
    subset = _cluster_subset(cluster_daily, ids)
    if subset.empty:
        return {"name": name, "pct": float("nan"), "flow1": float("nan"), "flow5": float("nan"), "size": float("nan"), "flow_coverage": float("nan")}
    size = pd.to_numeric(subset.get("total_size_100m"), errors="coerce").fillna(0)
    pct = pd.to_numeric(subset.get("weighted_pct_change"), errors="coerce")
    weighted_pct = float((pct.fillna(0) * size).sum() / size.sum()) if size.sum() else float(pct.mean())
    valid_flow_size = pd.to_numeric(
        subset.get("one_day_flow_valid_size_100m", size), errors="coerce"
    ).fillna(0)
    return {
        "name": name,
        "pct": weighted_pct,
        "flow1": _safe_sum(subset, "estimated_net_subscription_100m"),
        "flow5": _safe_sum(subset, "estimated_net_subscription_5d_100m"),
        "size": float(size.sum()) if size.notna().any() else float("nan"),
        "flow_coverage": float(valid_flow_size.sum() / size.sum()) if size.sum() else float("nan"),
    }


def _metric_brief(item: dict) -> str:
    return (
        f"{item['name']}：涨跌{_fmt_pct(item.get('pct'))}，"
        f"日{_fmt_signed_num(item.get('flow1'), 1)}亿，"
        f"5日{_fmt_signed_num(item.get('flow5'), 1)}亿，"
        f"资金规模覆盖{_fmt_pct(float(item.get('flow_coverage')) * 100) if pd.notna(item.get('flow_coverage')) else '-'}"
    )


def _best_by(items: list[dict], key: str, *, lighter_when_negative: bool = False) -> dict:
    clean = [item for item in items if pd.notna(item.get(key))]
    if not clean:
        return items[0]
    if lighter_when_negative and all(float(item[key]) < 0 for item in clean):
        return max(clean, key=lambda item: float(item[key]))
    return max(clean, key=lambda item: float(item[key]))


def _market_overview_table(metrics: dict, cluster_daily: pd.DataFrame, etf_daily: pd.DataFrame) -> str:
    total_size = _safe_sum(etf_daily, "fund_size_100m")
    if pd.isna(total_size):
        total_size = _safe_sum(cluster_daily, "total_size_100m")
    day_flow = _safe_sum(etf_daily, "estimated_net_subscription_100m")
    if pd.isna(day_flow):
        day_flow = _safe_sum(cluster_daily, "estimated_net_subscription_100m")
    net_sub = (
        pd.to_numeric(etf_daily["estimated_net_subscription_100m"], errors="coerce").dropna()
        if "estimated_net_subscription_100m" in etf_daily
        else pd.Series(dtype="float64")
    )
    gross_inflow = float(net_sub[net_sub > 0].sum()) if not net_sub.empty else 0.0
    gross_outflow = float(net_sub[net_sub < 0].sum()) if not net_sub.empty else 0.0
    five_flow = _safe_sum(etf_daily, "estimated_net_subscription_5d_100m")
    if pd.isna(five_flow):
        five_flow = _safe_sum(cluster_daily, "estimated_net_subscription_5d_100m")
    turnover = _safe_sum(etf_daily, "amount", yuan_to_100m=True)
    if pd.isna(turnover):
        turnover = _safe_sum(cluster_daily, "total_amount_100m")

    share_change = (
        pd.to_numeric(etf_daily["share_daily_change"], errors="coerce")
        if not etf_daily.empty and "share_daily_change" in etf_daily
        else pd.Series(dtype=float)
    )
    valid_share = share_change.dropna()
    share_inc_ratio = float((valid_share > 0).mean()) if not valid_share.empty else float("nan")
    q = _price_share_counts(etf_daily, cluster_daily)
    rows = [
        ("A股权益ETF总规模", f"{_fmt_num(total_size, 1)}亿元"),
        ("单日估算净申购", f"{_fmt_signed_num(day_flow, 1)}亿元"),
        ("单日整体流入", f"{_fmt_num(gross_inflow, 1)}亿元"),
        ("单日整体流出", f"{_fmt_num(abs(gross_outflow), 1)}亿元"),
        ("5日估算净申购", f"{_fmt_signed_num(five_flow, 1)}亿元"),
        ("总成交额", f"{_fmt_num(turnover, 1)}亿元"),
        ("资金流ETF只数覆盖率", _fmt_pct(float(metrics.get("flow_member_coverage", 0)) * 100)),
        ("资金流规模加权覆盖率", _fmt_pct(float(metrics.get("flow_size_coverage", 0)) * 100)),
        ("资金流缺失对应规模", f"{_fmt_num(metrics.get('missing_flow_size_100m'), 1)}亿元"),
        ("份额增加ETF比例", _fmt_pct(share_inc_ratio * 100) if pd.notna(share_inc_ratio) else "-"),
        ("价涨份额增数量", q.get("价涨份额增", 0)),
        ("价涨份额降数量", q.get("价涨份额降", 0)),
        ("价跌份额增数量", q.get("价跌份额增", 0)),
        ("价跌份额降数量", q.get("价跌份额降", 0)),
    ]
    return pd.DataFrame(rows, columns=["指标", "读数"]).to_markdown(index=False)


def _price_share_counts(etf_daily: pd.DataFrame, cluster_daily: pd.DataFrame) -> dict[str, int]:
    if not etf_daily.empty and {"pct_change", "share_daily_change"}.issubset(etf_daily.columns):
        price = pd.to_numeric(etf_daily["pct_change"], errors="coerce")
        share = pd.to_numeric(etf_daily["share_daily_change"], errors="coerce")
    else:
        price = pd.to_numeric(cluster_daily.get("weighted_pct_change"), errors="coerce")
        share = pd.to_numeric(cluster_daily.get("estimated_net_subscription_100m"), errors="coerce")
    return {
        "价涨份额增": int(((price > 0) & (share > 0)).sum()),
        "价涨份额降": int(((price > 0) & (share < 0)).sum()),
        "价跌份额增": int(((price < 0) & (share > 0)).sum()),
        "价跌份额降": int(((price < 0) & (share < 0)).sum()),
    }


def _persistence_text(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "数据不足，暂不能区分单日异动与连续趋势。"
    cluster_daily = _flow_eligible_clusters(cluster_daily)
    if cluster_daily.empty:
        return "资金流规模覆盖不足，暂不能区分单日异动与连续趋势。"
    if "flow_speed_status" in cluster_daily:
        status = cluster_daily["flow_speed_status"].fillna("方向不明确").astype(str)
        same_in = cluster_daily[status.str.startswith("持续流入")].sort_values("flow_1d", ascending=False)
        same_out = cluster_daily[status.str.startswith("持续流出")].sort_values("flow_1d", ascending=True)
        reversals = cluster_daily[status.isin(["由流出转流入", "由流入转流出"])]
        unclear_count = int(status.eq("方向不明确").sum())
    else:
        day = pd.to_numeric(cluster_daily.get("estimated_net_subscription_100m"), errors="coerce")
        five = pd.to_numeric(cluster_daily.get("estimated_net_subscription_5d_100m"), errors="coerce")
        same_in = cluster_daily[(day > 0) & (five > 0)].sort_values("estimated_net_subscription_5d_100m", ascending=False)
        same_out = cluster_daily[(day < 0) & (five < 0)].sort_values("estimated_net_subscription_5d_100m", ascending=True)
        reversals = cluster_daily[((day > 0) & (five <= 0)) | ((day < 0) & (five >= 0))]
        unclear_count = 0
    active = cluster_daily.sort_values("amount_vs_5d", ascending=False).head(2) if "amount_vs_5d" in cluster_daily else pd.DataFrame()
    parts = [
        f"持续流入集群较突出的是{_cluster_names(same_in, 'flow_1d' if 'flow_1d' in same_in else 'estimated_net_subscription_100m', n=3)}",
        f"持续流出集群较突出的是{_cluster_names(same_out, 'flow_1d' if 'flow_1d' in same_out else 'estimated_net_subscription_100m', ascending=True, n=3)}",
        f"此前数日与当日发生方向反转的集群有{len(reversals)}个",
    ]
    if unclear_count:
        parts.append(f"另有{unclear_count}个集群因5日数据不足或方向接近零不作判断")
    if not active.empty:
        parts.append(f"成交相对活跃的是{'、'.join(active['cluster_name'].astype(str).tolist())}，该项只代表二级市场换手")
    return "；".join(parts) + "。"


def _flow_structure_candidates(cluster_daily: pd.DataFrame, limit: int = 6) -> pd.DataFrame:
    if cluster_daily.empty or "flow_speed_status" not in cluster_daily:
        return pd.DataFrame()
    table = _flow_eligible_clusters(cluster_daily, "one_day_flow_size_coverage").copy()
    if table.empty:
        return table
    flow = pd.to_numeric(table.get("flow_1d"), errors="coerce")
    gross = pd.to_numeric(table.get("gross_turnover_flow"), errors="coerce").fillna(0)
    acceleration = pd.to_numeric(table.get("flow_acceleration_vs_prev4"), errors="coerce").abs().fillna(0)
    config = flow_structure_config()
    size = pd.to_numeric(table.get("total_size_100m"), errors="coerce")
    flow_to_size = pd.to_numeric(table.get("one_day_flow_to_size"), errors="coerce")
    net_material = flow.abs().ge(config["net_flow_abs_min_100m"]) | flow_to_size.abs().ge(
        config["net_flow_to_size_min"]
    )
    gross_material = gross.ge(config["gross_flow_abs_min_100m"]) | gross.div(size.replace(0, pd.NA)).ge(
        config["gross_flow_to_size_min"]
    )
    material = net_material | gross_material
    table = table[material].copy()
    if table.empty:
        return table
    status = table["flow_speed_status"].fillna("").astype(str)
    offset_status = table.get("flow_offset_status", pd.Series("", index=table.index)).fillna("").astype(str)
    structure = table.get("flow_structure_label", pd.Series("", index=table.index)).fillna("").astype(str)
    priority = status.map(
        {
            "由流出转流入": 6.0,
            "由流入转流出": 6.0,
            "持续流入且加速": 4.5,
            "持续流出且加速": 4.5,
            "持续流入但减速": 3.5,
            "持续流出但减速": 3.5,
        }
    ).fillna(0.0)
    priority += offset_status.eq("高分歧换仓").astype(float) * 6.0
    priority += structure.isin(
        ["头部产品集中流入", "头部产品集中流出", "金额较大但广度不足"]
    ).astype(float) * 3.0
    table["_structure_score"] = priority + acceleration.rank(pct=True) + gross.rank(pct=True) + flow.abs().rank(pct=True)

    chosen: list[int] = []

    def add_indices(frame: pd.DataFrame, count: int) -> None:
        for idx in frame.head(count).index:
            if idx not in chosen:
                chosen.append(idx)

    add_indices(table[status.eq("由流出转流入")].sort_values("flow_1d", ascending=False), 1)
    add_indices(table[status.eq("由流入转流出")].sort_values("flow_1d", ascending=True), 1)
    add_indices(table[offset_status.eq("高分歧换仓")].sort_values("gross_turnover_flow", ascending=False), 2)
    add_indices(
        table[status.isin(["持续流入且加速", "持续流出且加速"])]
        .assign(_abs_acceleration=acceleration)
        .sort_values("_abs_acceleration", ascending=False),
        1,
    )
    add_indices(
        table[status.isin(["持续流入但减速", "持续流出但减速"])]
        .assign(_abs_flow=flow.abs())
        .sort_values("_abs_flow", ascending=False),
        1,
    )
    add_indices(
        table[structure.isin(["头部产品集中流入", "头部产品集中流出", "金额较大但广度不足"])]
        .assign(_abs_flow=flow.abs())
        .sort_values("_abs_flow", ascending=False),
        1,
    )
    if len(chosen) < 3:
        add_indices(table.loc[~table.index.isin(chosen)].sort_values("_structure_score", ascending=False), 3 - len(chosen))
    return table.loc[chosen[:limit]]


def _flow_structure_lines(cluster_daily: pd.DataFrame) -> list[str]:
    table = _flow_structure_candidates(cluster_daily)
    if table.empty:
        return ["- 5日资金或集群内部明细不足，暂不作资金速度与内部结构判断。"]
    price_names = _cluster_names(cluster_daily, "weighted_pct_change", n=3)
    actual_buy = table[
        table["flow_speed_status"].isin(["持续流入且加速", "由流出转流入"])
    ]
    actual_buy_names = "、".join(actual_buy["cluster_name"].astype(str).head(3).tolist()) or "暂无明确加速或转入方向"
    lines = [
        f"- 结构结论：市场表面涨幅相对占优的是{price_names}；ETF资金实际加速买入或转为流入的是{actual_buy_names}。以下判断使用当日相对前4日均值、前4日方向和集群双向毛流量，不以Top20净额榜单代替结构判断。"
    ]
    for _, row in table.iterrows():
        speed_status = row.get("flow_speed_status", "方向不明确")
        acceleration_ratio = row.get("flow_acceleration_ratio_vs_prev4")
        acceleration_value = row.get("flow_acceleration_vs_prev4")
        if speed_status in {"由流出转流入", "由流入转流出"}:
            comparison_text = "，当日方向与前4日相反"
        elif pd.notna(acceleration_ratio):
            comparison_text = (
                f"，当日较前4日平均{'增加' if float(acceleration_value) >= 0 else '下降'}约{abs(float(acceleration_ratio)):.0%}"
            )
        else:
            comparison_text = (
                f"，前4日平均接近零，改用绝对差额{_fmt_signed_num(acceleration_value, 1)}亿元判断"
            )
        speed_text = (
            f"单日{_fmt_signed_num(row.get('flow_1d'), 1)}亿元，前4日平均{_fmt_signed_num(row.get('flow_prev4_daily_avg'), 1)}亿元，"
            f"前4日合计{_fmt_signed_num(row.get('flow_prev4_total'), 1)}亿元，判断为{speed_status}{comparison_text}"
        )
        offset_text = ""
        if row.get("flow_offset_status") in {"高分歧换仓", "中度分化"}:
            max_in = f"{_fmt_code(row.get('max_inflow_etf_code'))} {row.get('max_inflow_etf_name', '')}".strip()
            max_out = f"{_fmt_code(row.get('max_outflow_etf_code'))} {row.get('max_outflow_etf_name', '')}".strip()
            offset_text = (
                f"；内部{row.get('flow_offset_status')}：最大流入{max_in} {_fmt_signed_num(row.get('max_inflow_etf_flow'), 1)}亿元，"
                f"最大流出{max_out} {_fmt_signed_num(row.get('max_outflow_etf_flow'), 1)}亿元，"
                f"毛流量{_fmt_num(row.get('gross_turnover_flow'), 1)}亿元、净流量{_fmt_signed_num(row.get('net_flow'), 1)}亿元、"
                f"抵消率{_fmt_value(row.get('flow_offset_ratio'), 'flow_offset_ratio')}"
            )
        lines.append(
            f"- {row.get('cluster_name')}：{speed_text}{offset_text}；资金结构标签为{row.get('flow_structure_label', '-')}。"
        )
    return lines


def _total_judgement_lines(cluster_daily: pd.DataFrame, summary_sentence: str) -> list[str]:
    from .narrative import flow_layer, price_layer, temperature_layer

    price = price_layer(cluster_daily).replace("价格层：", "")
    flow = flow_layer(cluster_daily).replace("份额层：", "")
    temp = temperature_layer(cluster_daily).replace("温度层：", "")
    return [
        f"- 价格层：{price}",
        f"- 份额层：{flow}",
        f"- 温度层：{temp}",
        f"- 持续性：{_persistence_text(cluster_daily)}",
        f"- 综合判断：{summary_sentence} 价格强弱、ETF份额增减和二级市场成交活跃度分别解读；价跌份额增按逆向承接处理，价涨份额降按交易性上涨处理。",
    ]


def _core_style_table(cluster_daily: pd.DataFrame) -> str:
    specs = [
        ("大盘宽基", [("上证50", ["C01"]), ("沪深300", ["C02"]), ("中证A500", ["C03"])]),
        ("中小盘宽基", [("中证500", ["C04"]), ("中证1000", ["C05"]), ("中证2000", ["C06"])]),
        ("红利、资源与成长", [("红利防御", ["C11", "C12", "C13", "C15"]), ("资源周期", ["C29", "C30", "C31"]), ("科技成长", ["C16", "C17", "C18", "C19", "C20", "C25", "C26", "C27", "C35"])]),
        ("双创板块", [("创业板", ["C07", "C08"]), ("科创板", ["C09", "C10"])]),
    ]
    rows = []
    for title, groups in specs:
        items = [_aggregate_clusters(cluster_daily, ids, name) for name, ids in groups]
        threshold = float(load_temperature_config()["thresholds"].get("daily_min_cluster_flow_size_coverage", 0.90))
        eligible_items = [item for item in items if pd.notna(item.get("flow_coverage")) and item["flow_coverage"] >= threshold]
        flow_best = _best_by(eligible_items, "flow1", lighter_when_negative=True) if eligible_items else None
        price_best = _best_by(items, "pct", lighter_when_negative=True)
        rows.append(
            {
                "比较组": title,
                "明确结论": (
                    f"份额层{flow_best['name']}最强，价格层{price_best['name']}相对占优。"
                    if flow_best is not None
                    else f"份额层覆盖不足暂不判断，价格层{price_best['name']}相对占优。"
                ),
                "关键数据": "；".join(_metric_brief(item) for item in items),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _concentration_lines(cluster_daily: pd.DataFrame, limit: int = 5) -> list[str]:
    if cluster_daily.empty or "max_contributor_ratio_1d" not in cluster_daily:
        return ["- 暂无可用的资金集中度字段。"]
    table = cluster_daily.dropna(subset=["max_contributor_ratio_1d"]).copy()
    table = table[table.apply(_concentration_analysis_valid, axis=1)]
    if table.empty:
        return ["- 无达到最小成员数要求的集群，暂不作集中度判断。"]
    table["_ratio"] = pd.to_numeric(table["max_contributor_ratio_1d"], errors="coerce")
    table = table.sort_values("_ratio", ascending=False).head(limit)
    lines = []
    for _, row in table.iterrows():
        warning = " 集群同向资金主要由单一产品驱动，广度有限。" if float(row["_ratio"]) > 0.5 else ""
        lines.append(
            f"- {row.get('cluster_name')}：最大同向贡献{_contributor_text(row)}，"
            f"前3只ETF同向贡献比例{_top3_contributor_text(row)}。{warning}"
        )
    return lines


def _important_clusters(cluster_daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    specs = [
        ("价格最强", "weighted_pct_change", False),
        ("价格最弱", "weighted_pct_change", True),
        ("份额流入最强", "estimated_net_subscription_100m", False),
        ("份额流出最强", "estimated_net_subscription_100m", True),
        ("升温最大", "temperature_change", False),
        ("降温最大", "temperature_change", True),
    ]
    for label, col, asc in specs:
        if col in cluster_daily and not cluster_daily.empty:
            row = cluster_daily.sort_values(col, ascending=asc).head(1)
            if not row.empty:
                rows.append({"观察点": label, **row.iloc[0].to_dict()})
    return pd.DataFrame(rows)


def _compact_cluster_overview(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "无数据。"
    rows = []
    for _, row in cluster_daily.sort_values("cluster_id").iterrows():
        rows.append(
            {
                "集群": f"{row.get('cluster_id')} {row.get('cluster_name')}",
                "核心读数": (
                    f"规模{_fmt_num(row.get('total_size_100m'), 0)}亿；"
                    f"日{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿/"
                    f"5日{_fmt_signed_num(row.get('estimated_net_subscription_5d_100m'), 1)}亿；"
                    f"温度{_fmt_num(row.get('cluster_temperature'), 1)}，{row.get('temperature_direction', '-') }；"
                    f"{row.get('structure_judgement', '-') }"
                ),
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _important_cluster_lines(cluster_daily: pd.DataFrame) -> list[str]:
    if cluster_daily.empty:
        return ["- 无数据。"]
    day = pd.to_numeric(cluster_daily.get("estimated_net_subscription_100m"), errors="coerce")
    five = pd.to_numeric(cluster_daily.get("estimated_net_subscription_5d_100m"), errors="coerce")
    pct = pd.to_numeric(cluster_daily.get("weighted_pct_change"), errors="coerce")
    temp_change = pd.to_numeric(cluster_daily.get("temperature_change"), errors="coerce")
    lines = []
    used: set[str] = set()

    def add(key: str, text: str) -> None:
        if key not in used and len(lines) < 8:
            used.add(key)
            lines.append(text)

    def first(mask: pd.Series, sort_col: str, ascending: bool = False) -> pd.Series | None:
        table = cluster_daily[mask].copy()
        if table.empty or sort_col not in table:
            return None
        table["_sort"] = pd.to_numeric(table[sort_col], errors="coerce")
        table = table.dropna(subset=["_sort"]).sort_values("_sort", ascending=ascending)
        return None if table.empty else table.iloc[0]

    row = first((day > 0) & (five > 0), "estimated_net_subscription_5d_100m", False)
    if row is not None:
        add(
            "continuous_in",
            f"- 连续净申购：{row.get('cluster_name')}单日{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿、5日{_fmt_signed_num(row.get('estimated_net_subscription_5d_100m'), 1)}亿，说明份额扩张不只是单日噪声。",
        )
    row = first((day < 0) & (five < 0), "estimated_net_subscription_5d_100m", True)
    if row is not None:
        add(
            "continuous_out",
            f"- 连续净赎回：{row.get('cluster_name')}单日{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿、5日{_fmt_signed_num(row.get('estimated_net_subscription_5d_100m'), 1)}亿，价格修复前需要先观察份额收缩是否停止。",
        )
    row = first((pct > 0) & (day < 0), "weighted_pct_change", False)
    if row is not None:
        add(
            "price_up_share_down",
            f"- 价涨份额降：{row.get('cluster_name')}涨跌{_fmt_pct(row.get('weighted_pct_change'))}但单日净赎回{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿，上涨更偏二级市场交易，不宜解读为申购流入。",
        )
    row = first((pct < 0) & (day > 0), "estimated_net_subscription_100m", False)
    if row is not None:
        add(
            "price_down_share_up",
            f"- 价跌份额增：{row.get('cluster_name')}下跌{_fmt_pct(row.get('weighted_pct_change'))}但单日净申购{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿，代表下跌中的逆向承接，次日延续性比当天涨跌更重要。",
        )
    if "max_contributor_ratio_1d" in cluster_daily:
        ratio = pd.to_numeric(cluster_daily["max_contributor_ratio_1d"], errors="coerce")
        eligible = cluster_daily.apply(_concentration_analysis_valid, axis=1)
        row = first((ratio > 0.5) & eligible, "max_contributor_ratio_1d", False)
        if row is not None:
            add(
                "concentration",
                f"- 单一ETF主导：{row.get('cluster_name')}最大同向贡献{_contributor_text(row)}，前3只同向贡献{_top3_contributor_text(row)}，集群同向资金主要由单一产品驱动，广度有限。",
            )
    row = first(temp_change.abs() >= 5, "temperature_change", False)
    if row is not None:
        add(
            "temp_turn",
            f"- 温度明显转折：{row.get('cluster_name')}温度{_temperature_detail(row)}、阶段为{row.get('temperature_stage', row.get('temperature_phase', '-'))}，说明价格、份额和成交的组合状态已经变快。",
        )
    while len(lines) < 5:
        fallback = _important_clusters(cluster_daily)
        if fallback.empty or len(lines) >= 5:
            break
        row = fallback.iloc[min(len(lines), len(fallback) - 1)]
        add(
            f"fallback_{len(lines)}",
            f"- {row.get('观察点')}：{row.get('cluster_name')}，涨跌{_fmt_pct(row.get('weighted_pct_change'))}、日净申购{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿，是今天需要解释的边际变化。",
        )
        if len(used) >= len(fallback) + 6:
            break
    return lines[:8]


def _important_etf_lines(etf_daily: pd.DataFrame, room: int) -> list[str]:
    if room <= 0 or etf_daily.empty:
        return []
    lines = []
    if "premium_discount" in etf_daily:
        premium = pd.to_numeric(etf_daily["premium_discount"], errors="coerce")
        table = etf_daily[premium.abs() >= 2].copy()
        if not table.empty:
            table["_abs_premium"] = premium.loc[table.index].abs()
            row = table.sort_values("_abs_premium", ascending=False).iloc[0]
            lines.append(
                f"- 异常折溢价：{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}折溢价{_fmt_pct(row.get('premium_discount'))}，需要先排除净值滞后、停复牌或申赎清单差异后再解读。"
            )
    if len(lines) >= room:
        return lines[:room]
    if "share_daily_change_pct" in etf_daily:
        share = pd.to_numeric(etf_daily["share_daily_change_pct"], errors="coerce")
        table = etf_daily[share.abs() >= 5].copy()
        if not table.empty:
            table["_abs_share"] = share.loc[table.index].abs()
            row = table.sort_values("_abs_share", ascending=False).iloc[0]
            lines.append(
                f"- 份额变化异常：{_fmt_code(row.get('etf_code'))} {row.get('etf_name', '')}份额日变化率{_fmt_pct(row.get('share_daily_change_pct'))}，会显著影响所属集群净申购估算，需看是否由单一产品造成。"
            )
    return lines[:room]


def _quadrants(cluster_daily: pd.DataFrame) -> pd.DataFrame:
    if cluster_daily.empty:
        return pd.DataFrame()
    cluster_daily = _flow_eligible_clusters(cluster_daily, "one_day_flow_size_coverage")
    if cluster_daily.empty:
        return pd.DataFrame()
    pct = pd.to_numeric(cluster_daily.get("weighted_pct_change"), errors="coerce")
    flow = pd.to_numeric(cluster_daily.get("estimated_net_subscription_100m"), errors="coerce")
    labels = []
    for p, f in zip(pct, flow):
        if pd.isna(p) or pd.isna(f):
            labels.append("数据待确认")
        elif p >= 0 and f >= 0:
            labels.append("价涨份额增")
        elif p >= 0 and f < 0:
            labels.append("价涨份额降")
        elif p < 0 and f >= 0:
            labels.append("价跌份额增")
        else:
            labels.append("价跌份额降")
    out = cluster_daily.copy()
    out["象限"] = labels
    out["_importance"] = (
        pd.to_numeric(out.get("total_size_100m"), errors="coerce").fillna(0).rank(pct=True)
        + pd.to_numeric(out.get("estimated_net_subscription_100m"), errors="coerce").abs().fillna(0).rank(pct=True)
    )
    return (
        out.sort_values("_importance", ascending=False)
        .groupby("象限", as_index=False)
        .head(5)[["象限", "cluster_name", "weighted_pct_change", "estimated_net_subscription_100m", "structure_judgement"]]
    )


def _quadrant_reconciliation(etf_daily: pd.DataFrame) -> str:
    if etf_daily.empty or not {"pct_change", "share_daily_change"}.issubset(etf_daily.columns):
        return "- 四象限对账：ETF明细不足，无法统计纳入与未纳入数量。"
    price = pd.to_numeric(etf_daily["pct_change"], errors="coerce")
    share = pd.to_numeric(etf_daily["share_daily_change"], errors="coerce")
    missing = price.isna() | share.isna()
    share_flat = ~missing & share.eq(0)
    price_flat = ~missing & ~share_flat & price.eq(0)
    included = ~missing & ~share_flat & ~price_flat
    total = int(len(etf_daily))
    included_count = int(included.sum())
    excluded_count = total - included_count
    return (
        f"- 四象限对账：纳入ETF {included_count}只，未纳入 {excluded_count}只；"
        f"未纳入原因依次为份额不变 {int(share_flat.sum())}只、价格持平 {int(price_flat.sum())}只、"
        f"数据缺失或方向不可判定 {int(missing.sum())}只（总计 {total}只，数量可对账）。"
    )


def _quadrant_lines(cluster_daily: pd.DataFrame, etf_daily: pd.DataFrame | None = None) -> list[str]:
    table = _quadrants(cluster_daily)
    if table.empty:
        return ["- 无数据。"]
    title_map = {
        "价涨份额增": "价涨份额增：趋势确认",
        "价涨份额降": "价涨份额降：交易性上涨",
        "价跌份额增": "价跌份额增：逆向承接",
        "价跌份额降": "价跌份额降：同步降温",
        "数据待确认": "数据待确认",
    }
    lines = []
    for quadrant in ["价涨份额增", "价涨份额降", "价跌份额增", "价跌份额降", "数据待确认"]:
        group = table[table["象限"].eq(quadrant)]
        if group.empty:
            continue
        parts = []
        for _, row in group.iterrows():
            parts.append(
                f"{row.get('cluster_name')}（{_fmt_pct(row.get('weighted_pct_change'))}，"
                f"{_fmt_signed_num(row.get('estimated_net_subscription_100m'), 1)}亿，{row.get('structure_judgement', '-')}）"
            )
        lines.append(f"- {title_map.get(quadrant, quadrant)}：{'；'.join(parts)}。")
    if etf_daily is not None:
        lines.append(_quadrant_reconciliation(etf_daily))
    return lines


def _tomorrow_watch_lines(cluster_daily: pd.DataFrame) -> list[str]:
    if cluster_daily.empty:
        return [
            "1. 等待份额、净值和行情数据补齐后再验证价格与份额方向。",
            "2. 观察总成交额是否继续放大，但不把成交额当作申购流入。",
            "3. 观察全部固定集群是否恢复有效温度阶段。",
            "4. 观察折溢价和份额异常是否仍待复核。",
            "5. 观察核心宽基与成长主题是否出现方向分化。",
        ]
    day = pd.to_numeric(cluster_daily.get("estimated_net_subscription_100m"), errors="coerce")
    five = pd.to_numeric(cluster_daily.get("estimated_net_subscription_5d_100m"), errors="coerce")
    pct = pd.to_numeric(cluster_daily.get("weighted_pct_change"), errors="coerce")
    top_in = cluster_daily[(day > 0) & (five > 0)].sort_values("estimated_net_subscription_5d_100m", ascending=False).head(1)
    reverse = cluster_daily[(pct < 0) & (day > 0)].sort_values("estimated_net_subscription_100m", ascending=False).head(1)
    trade_up = cluster_daily[(pct > 0) & (day < 0)].sort_values("weighted_pct_change", ascending=False).head(1)
    if "max_contributor_ratio_1d" in cluster_daily:
        eligible = cluster_daily.apply(_concentration_analysis_valid, axis=1)
        concentration = (
            cluster_daily[
                eligible
                & pd.to_numeric(cluster_daily.get("max_contributor_ratio_1d"), errors="coerce").gt(0.5)
            ]
            .sort_values("max_contributor_ratio_1d", ascending=False)
            .head(1)
        )
    else:
        concentration = pd.DataFrame()
    turning = cluster_daily[
        cluster_daily.get("temperature_stage", pd.Series("", index=cluster_daily.index)).isin(["高温降温", "中温转强", "中温走弱", "冷启动"])
    ].head(1)

    def name_or(table: pd.DataFrame, fallback: str) -> str:
        return fallback if table.empty else str(table.iloc[0].get("cluster_name", fallback))

    return [
        f"1. 验证{name_or(top_in, '5日净申购领先集群')}的份额扩张是否继续，重点看成员广度而不是单日成交额。",
        f"2. 验证{name_or(reverse, '价跌份额增集群')}的逆向承接是否延续到价格止跌前后。",
        f"3. 验证{name_or(trade_up, '价涨份额降集群')}的上涨是否仍缺少申购确认。",
        f"4. 验证{name_or(concentration, '高集中度集群')}的同向资金是否从单一ETF扩散到更多产品。",
        f"5. 验证{name_or(turning, '温度转折集群')}的温度阶段是否继续变化，避免把一天的热度跳变当成趋势。",
    ]


def _tracking_temperature_table(etf_daily: pd.DataFrame, abnormal_pool: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    if abnormal_pool.empty or etf_daily.empty:
        return pd.DataFrame()
    tracking = abnormal_pool.head(top_n).copy()
    tracking["etf_code"] = tracking["etf_code"].astype(str)
    order_map = {code: order for order, code in enumerate(tracking["etf_code"].tolist())}
    extras = [
        column
        for column in ["etf_code", "abnormal_source", "abnormal_signal_count", "abnormal_score", "tracking_priority"]
        if column in tracking.columns
    ]
    score_extra = tracking[extras].drop_duplicates("etf_code") if extras else pd.DataFrame()
    daily = etf_daily.copy()
    daily["etf_code"] = daily["etf_code"].astype(str)
    daily = daily[daily["etf_code"].isin(order_map)].copy()
    if daily.empty:
        return tracking
    if not score_extra.empty:
        daily = daily.merge(score_extra, on="etf_code", how="left")
    daily["_tracking_order"] = daily["etf_code"].map(order_map)
    return daily.sort_values("_tracking_order").drop(columns=["_tracking_order"])


def _dedup_watchlist(abnormal_pool: pd.DataFrame, top_n: int = 30, max_per_cluster: int = 3) -> pd.DataFrame:
    if abnormal_pool.empty:
        return pd.DataFrame()
    pool = abnormal_pool.copy()
    if "abnormal_score" in pool:
        pool = pool.sort_values(["abnormal_score", "amount"], ascending=[False, False])
    rows = []
    cluster_counts: dict[str, int] = {}
    for _, row in pool.iterrows():
        cluster = str(row.get("primary_cluster_name") or row.get("primary_cluster") or "未分类")
        if cluster_counts.get(cluster, 0) >= max_per_cluster:
            continue
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        item = row.to_dict()
        item["tracking_priority"] = len(rows) + 1
        item["watch_reason"] = row.get("one_line_explanation") or row.get("abnormal_source") or "-"
        rows.append(item)
        if len(rows) >= top_n:
            break
    return pd.DataFrame(rows)


def _ranking_specs(etf_daily: pd.DataFrame, top_n: int) -> list[dict]:
    specs = [
        {"title": f"单日估算净申购Top{top_n}", "column": "estimated_net_subscription_100m", "ascending": False, "label": "净申购(亿元)", "sign_filter": "positive"},
        {"title": f"单日估算净赎回Top{top_n}", "column": "estimated_net_subscription_100m", "ascending": True, "label": "净赎回(亿元)", "sign_filter": "negative"},
        {"title": f"5日估算净申购Top{top_n}", "column": "estimated_net_subscription_5d_100m", "ascending": False, "label": "5日净申购(亿元)", "sign_filter": "positive"},
        {"title": f"5日估算净赎回Top{top_n}", "column": "estimated_net_subscription_5d_100m", "ascending": True, "label": "5日净赎回(亿元)", "sign_filter": "negative"},
        {"title": f"份额增长Top{top_n}", "column": "share_daily_change_pct", "ascending": False, "label": "份额增长率", "sign_filter": "positive"},
        {"title": f"份额减少Top{top_n}", "column": "share_daily_change_pct", "ascending": True, "label": "份额减少率", "sign_filter": "negative"},
        {"title": f"成交额Top{top_n}", "column": "amount", "ascending": False, "label": "成交额(亿元)"},
        {"title": f"成交额放大Top{top_n}", "column": "amount_vs_5d", "ascending": False, "label": "成交额/5日均值"},
        {"title": f"涨幅Top{top_n}", "column": "pct_change", "ascending": False, "label": "涨跌幅"},
        {"title": f"跌幅Top{top_n}", "column": "pct_change", "ascending": True, "label": "涨跌幅"},
        {"title": f"份额变化率绝对值Top{top_n}", "column": "share_daily_change_pct", "ascending": False, "abs_sort": True, "label": "份额变化率绝对值"},
        {"title": f"温度Top{top_n}", "column": "temperature", "ascending": False, "label": "温度"},
        {"title": f"升温Top{top_n}", "column": "temperature_change", "ascending": False, "label": "温度变化"},
        {"title": f"降温Top{top_n}", "column": "temperature_change", "ascending": True, "label": "温度变化"},
        {"title": f"价涨份额降Top{top_n}", "column": "pct_change", "ascending": False, "label": "涨跌幅", "price_share_filter": "up_share_down"},
        {"title": f"价跌份额增Top{top_n}", "column": "share_daily_change_pct", "ascending": False, "label": "份额变化率", "price_share_filter": "down_share_up"},
        {"title": f"异常折溢价Top{top_n}", "column": "premium_discount", "ascending": False, "abs_sort": True, "label": "折溢价率绝对值"},
    ]
    return [spec for spec in specs if spec["column"] in etf_daily.columns]


def _ranking_metric_value(row: pd.Series, column: str) -> str:
    value = row.get(column)
    if column in {"estimated_net_subscription_100m", "estimated_net_subscription_5d_100m"}:
        return _fmt_signed_num(value)
    if column == "amount":
        return _fmt_num(float(value) / 1e8) if pd.notna(value) else "-"
    if column == "amount_vs_5d":
        return _fmt_value(value, column)
    if column in {"pct_change", "share_daily_change_pct"}:
        return _fmt_pct(value)
    if column == "temperature":
        return _fmt_num(value)
    if column == "temperature_change":
        return _fmt_signed_num(value)
    if column == "premium_discount":
        return _fmt_pct(value)
    return _fmt_value(value, column)


def _single_etf_ranking_table(etf_daily: pd.DataFrame, spec: dict, top_n: int = 20) -> str:
    if etf_daily.empty or spec["column"] not in etf_daily:
        return "无数据。"
    table = etf_daily.copy()
    metric = pd.to_numeric(table[spec["column"]], errors="coerce")
    if spec.get("price_share_filter"):
        if "pct_change" not in table.columns:
            return "无数据。"
        share_column = "share_daily_change" if "share_daily_change" in table.columns else "share_daily_change_pct" if "share_daily_change_pct" in table.columns else None
        if share_column is None:
            return "无数据。"
        price = pd.to_numeric(table["pct_change"], errors="coerce")
        share = pd.to_numeric(table[share_column], errors="coerce")
        if spec["price_share_filter"] == "up_share_down":
            table = table[(price > 0) & (share < 0)]
            metric = metric.loc[table.index]
        elif spec["price_share_filter"] == "down_share_up":
            table = table[(price < 0) & (share > 0)]
            metric = metric.loc[table.index]
    table["_rank_metric"] = metric.abs() if spec.get("abs_sort") else metric
    if spec.get("sign_filter") == "positive":
        table = table[metric.gt(0)]
    elif spec.get("sign_filter") == "negative":
        table = table[metric.lt(0)]
    table = table.dropna(subset=["_rank_metric"]).sort_values("_rank_metric", ascending=spec["ascending"]).head(top_n)
    if table.empty:
        return "无符合该方向的ETF。"
    rows = []
    for rank, (_, row) in enumerate(table.iterrows(), start=1):
        metric_row = row
        if spec.get("abs_sort"):
            value = pd.to_numeric(pd.Series([row.get(spec["column"])]), errors="coerce").iloc[0]
            metric_row = pd.Series({spec["column"]: abs(value) if pd.notna(value) else value})
        item = {
            "排名": rank,
            "ETF代码": _fmt_code(row.get("etf_code")),
            "ETF名称": row.get("etf_name", "-"),
            "所属集群": row.get("primary_cluster_name", "-"),
            spec["label"]: _ranking_metric_value(metric_row, spec["column"]),
        }
        item["涨跌幅"] = _fmt_pct(row.get("pct_change")) if "pct_change" in row else "-"
        item["温度"] = _fmt_num(row.get("temperature")) if "temperature" in row else "-"
        rows.append(item)
    return pd.DataFrame(rows).to_markdown(index=False)


def _append_single_etf_rankings(lines: list[str], etf_daily: pd.DataFrame, top_n: int = 20, heading_level: int = 2) -> None:
    prefix = "#" * heading_level
    lines.append(f"{prefix} 单只ETF异动排行Top{top_n}")
    lines.append("")
    if etf_daily.empty:
        lines.append("无数据。")
        lines.append("")
        return
    lines.append(f"以下榜单均基于全市场扫描结果，各榜展示Top{top_n}；成交额只代表二级市场换手和活跃度，不写作资金流入。K线仅跟踪重点异动，排行榜数据不缩减。")
    lines.append("")
    for spec in _ranking_specs(etf_daily, top_n):
        lines.append(f"{prefix}# {spec['title']}")
        lines.append("")
        lines.append(_single_etf_ranking_table(etf_daily, spec, top_n=top_n))
        lines.append("")


def _write_technical_appendix(
    path: Path,
    report_date: str,
    metrics: dict,
    cluster_daily: pd.DataFrame,
    etf_daily: pd.DataFrame,
    abnormal_pool: pd.DataFrame,
    chart_paths: list[Path],
) -> None:
    report_config = get_report_config()
    appendix_top_n = int(report_config["appendix_top_n"])
    watchlist_top_n = int(report_config["watchlist_top_n"])
    watchlist_max_per_cluster = int(report_config["watchlist_max_per_cluster"])
    lines = [f"# A股ETF资金温度日报_技术附录_{report_date}", ""]
    lines.append("## 1. 验证指标")
    lines.append("")
    lines.append(pd.DataFrame([metrics]).T.reset_index().rename(columns={"index": "指标", 0: "值"}).to_markdown(index=False))
    lines.append("")
    lines.append("## 2. 观察集群完整字段")
    lines.append("")
    lines.append(_format_table(cluster_daily))
    lines.append("")
    lines.append("### 集群完整排行")
    lines.append("")
    _append_key_cluster_tables(lines, cluster_daily)
    lines.append("")
    lines.append(f"## 3. 异动ETF Top{appendix_top_n}温度字段")
    lines.append("")
    score_cols = [
        "tracking_priority",
        "etf_code",
        "etf_name",
        "primary_cluster_name",
        "abnormal_source",
        "abnormal_signal_count",
        "abnormal_score",
        "absolute_temperature",
        "market_percentile_temperature",
        "cluster_percentile_temperature",
        "final_temperature",
        "temperature_confidence",
        "temperature",
        "temperature_level",
        "temperature_direction",
        "temperature_phase",
        "temperature_stage",
        "flow_1d",
        "flow_5d",
        "flow_5d_valid_days",
        "flow_5d_daily_avg",
        "flow_prev4_valid_days",
        "flow_prev4_total",
        "flow_prev4_daily_avg",
        "flow_acceleration_vs_prev4",
        "flow_acceleration_ratio_vs_prev4",
        "flow_previous_4d",
        "flow_acceleration",
        "flow_acceleration_ratio",
        "flow_speed_status",
        "creation_redemption_unit",
        "estimated_creation_redemption_units_1d",
        "creation_unit_rounding_gap",
        "creation_unit_data_date",
    ]
    tracking_table = _tracking_temperature_table(etf_daily, abnormal_pool, top_n=appendix_top_n)
    lines.append(_format_table(tracking_table, score_cols) if not tracking_table.empty else "无。")
    lines.append("")
    lines.append(f"## 4. 异常池Top{appendix_top_n}")
    lines.append("")
    lines.append(_format_table(abnormal_pool.head(appendix_top_n)) if not abnormal_pool.empty else "无。")
    lines.append("")
    lines.append(f"## 5. 去重观察池Top{watchlist_top_n}")
    lines.append("")
    lines.append(f"每个集群最多保留{watchlist_max_per_cluster}只ETF，用于避免单一主题占满观察清单。")
    lines.append("")
    watchlist = _dedup_watchlist(abnormal_pool, top_n=watchlist_top_n, max_per_cluster=watchlist_max_per_cluster)
    watch_cols = [
        "tracking_priority",
        "etf_code",
        "etf_name",
        "primary_cluster_name",
        "abnormal_signal_count",
        "abnormal_score",
        "pct_change",
        "temperature",
        "watch_reason",
    ]
    lines.append(_format_table(watchlist, watch_cols) if not watchlist.empty else "无。")
    lines.append("")
    lines.append("## 6. K线图索引")
    lines.append("")
    if chart_paths:
        for path_item in chart_paths:
            lines.append(f"- [{path_item.name}]({_markdown_path(path_item, path.parent)})")
    else:
        lines.append("未生成。")
    lines.append("")
    lines.append(f"## 7. 单只ETF全量Top{appendix_top_n}排行")
    lines.append("")
    lines.append(f"以下榜单均基于全市场扫描结果，各榜只展示Top{appendix_top_n}；正文仅保留综合精选异动，附录承担检索。")
    lines.append("")
    _append_single_etf_rankings(lines, etf_daily, top_n=appendix_top_n, heading_level=3)
    atomic_write_text(path, "\n".join(lines) + "\n")


def generate_report(
    report_date: str,
    output_path: Path,
    metrics: dict,
    universe_summary: dict,
    cluster_daily: pd.DataFrame,
    etf_daily: pd.DataFrame,
    representatives: pd.DataFrame,
    abnormal_pool: pd.DataFrame,
    chart_paths: list[Path],
) -> Path:
    from .narrative import flow_layer, price_layer, temperature_layer, validate_narrative

    report_config = get_report_config()
    cluster_flow_top_n = int(report_config["main_cluster_flow_top_n"])
    main_top_n = int(report_config["main_abnormal_top_n"])
    tracking_chart_paths = chart_paths[: int(report_config["tracking_chart_pool_top_n"])]
    main_chart_paths = tracking_chart_paths[: min(int(report_config["main_chart_top_n"]), 5)]
    narrative_parts = [price_layer(cluster_daily), flow_layer(cluster_daily), temperature_layer(cluster_daily)]
    summary_sentence = _research_summary(cluster_daily)
    narrative_validation = validate_narrative("\n".join(narrative_parts + [summary_sentence]), cluster_daily)
    if not narrative_validation.get("narrative_consistency_status"):
        raise ValueError(f"report narrative validation failed: {narrative_validation.get('narrative_violations')}")
    report_status = "正式报告" if metrics.get("formal_report_allowed") else "预览/数据待补全"
    lines = [f"# A股ETF资金结构日报 {report_date}", ""]
    lines.append("> 估算净申购/赎回来自份额变化和净值估算，不等同基金精确现金流；成交额只表示二级市场换手、活跃度和分歧。")
    lines.append(
        f"> 报告状态：{report_status}；新鲜行情{metrics.get('quote_fresh_coverage', 0):.2%}，"
        f"新鲜份额{metrics.get('share_fresh_coverage', 0):.2%}，新鲜净值{metrics.get('nav_fresh_coverage', 0):.2%}，"
        f"资金流有效规模{metrics.get('flow_size_coverage', 0):.2%}。"
    )
    lines.append(f"> 正式报告阻断项：{_formal_blocker_line(metrics)}。")
    lines.append("")

    lines.append("## 1. 今日总判断")
    lines.append("")
    lines.extend(_total_judgement_lines(cluster_daily, summary_sentence))
    lines.append("")
    lines.append("### 资金速度与内部结构")
    lines.append("")
    lines.extend(_flow_structure_lines(cluster_daily))
    lines.append("")

    lines.append("## 2. 数据状态与全市场概览")
    lines.append("")
    lines.append(_market_overview_table(metrics, cluster_daily, etf_daily))
    lines.append("")

    lines.append(f"## 3. 资金迁移Top{cluster_flow_top_n}")
    lines.append("")
    lines.append("### 单日估算净申购")
    lines.append("")
    lines.append(_compact_cluster_flow_table(cluster_daily, ascending=False, sign_filter="positive", n=cluster_flow_top_n))
    lines.append("")
    lines.append("### 单日估算净赎回")
    lines.append("")
    lines.append(_compact_cluster_flow_table(cluster_daily, ascending=True, sign_filter="negative", n=cluster_flow_top_n))
    lines.append("")
    lines.append("### 核心风格")
    lines.append("")
    lines.append(_core_style_table(cluster_daily))

    lines.append("")
    lines.append("## 4. 背离、集中度与重点观察")
    lines.append("")
    important_lines = _important_cluster_lines(cluster_daily)
    important_lines.extend(_important_etf_lines(etf_daily, 8 - len(important_lines)))
    lines.extend(important_lines[:5])
    lines.append("")
    lines.extend(_quadrant_lines(cluster_daily, etf_daily))
    lines.append("")
    lines.append(f"### 重点ETF综合观察Top{main_top_n}")
    lines.append("")
    lines.append(_abnormal_pool_brief(abnormal_pool, top_n=main_top_n))
    lines.append("")
    lines.append("### 明日观察")
    lines.append("")
    lines.extend(_tomorrow_watch_lines(cluster_daily))

    lines.append("")
    lines.append("## 5. 方法、样本与使用边界")
    lines.append("")
    lines.extend(_methodology_lines())
    lines.extend(_history_evidence_lines(metrics))
    lines.extend(_investment_usage_lines())

    lines.append("")
    lines.append("附：温度只是摘要指标，不作为第一结论；正文不展示内部score字段；内部score字段、完整验证指标和原始异常池保留在技术附录或 parquet 原始数据中。")
    lines.append("")
    if main_chart_paths:
        lines.append("## 图表：关键ETF K线")
        lines.append("")
        chart_name_map = etf_daily.set_index("etf_code")["etf_name"].astype(str).to_dict() if not etf_daily.empty else {}
        for path in main_chart_paths:
            code = path.stem.replace("_kline", "")
            name = chart_name_map.get(code, "")
            title = f"{code} {name}".strip()
            lines.append(f"![{title} K线图]({_markdown_path(path, output_path.parent)})")
            lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, "\n".join(lines) + "\n")
    _write_technical_appendix(
        _appendix_path(output_path, report_date),
        report_date,
        metrics,
        cluster_daily,
        etf_daily,
        abnormal_pool,
        tracking_chart_paths,
    )
    return output_path
