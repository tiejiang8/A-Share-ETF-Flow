from __future__ import annotations

from typing import Any

import pandas as pd

from .config import load_temperature_config


BANNED_PHRASES = ["真实申购", "真实资金流入", "资金全面买入科技", "成交额显示资金流入"]

DIVIDEND_DEFENSE_CLUSTERS = {"C11", "C12", "C13", "C15"}
RESOURCE_CYCLE_CLUSTERS = {"C29", "C30", "C31"}
GROWTH_CLUSTERS = {"C16", "C17", "C18", "C19", "C20", "C25", "C26", "C27", "C35"}
BIG_CLUSTERS = {"C01", "C02", "C03"}
SMALL_CLUSTERS = {"C04", "C05", "C06"}
CHINEXT = {"C07", "C08"}
STAR = {"C09", "C10"}


def _group_mean(df: pd.DataFrame, cluster_ids: set[str], column: str) -> float:
    subset = df[df["cluster_id"].isin(cluster_ids)] if not df.empty else df
    values = pd.to_numeric(subset.get(column), errors="coerce")
    weights = pd.to_numeric(subset.get("total_size_100m"), errors="coerce").fillna(0)
    if values.notna().any() and weights.sum() > 0:
        return float((values.fillna(0) * weights).sum() / weights.sum())
    return float(values.mean()) if values.notna().any() else float("nan")


def _group_sum(df: pd.DataFrame, cluster_ids: set[str], column: str) -> float:
    subset = df[df["cluster_id"].isin(cluster_ids)] if not df.empty else df
    values = pd.to_numeric(subset.get(column), errors="coerce")
    return float(values.sum()) if values.notna().any() else float("nan")


def _names(df: pd.DataFrame, n: int = 3) -> str:
    if df.empty:
        return "无"
    return "、".join(df["cluster_name"].astype(str).head(n).tolist())


def _fmt_flow(value: float) -> str:
    if pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}亿"


def _strongest_group(values: dict[str, float]) -> str:
    valid = {name: value for name, value in values.items() if pd.notna(value)}
    return max(valid, key=valid.get) if valid else "数据不足"


def _flow_eligible(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "flow_size_coverage" not in df:
        return df
    threshold = float(
        load_temperature_config()["thresholds"].get("daily_min_cluster_flow_size_coverage", 0.90)
    )
    coverage = pd.to_numeric(df["flow_size_coverage"], errors="coerce")
    return df[coverage.ge(threshold)].copy()


def price_layer(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "价格层：数据不足，不能判断风格方向。"
    df = cluster_daily.copy()
    defense = _group_mean(df, DIVIDEND_DEFENSE_CLUSTERS, "weighted_pct_change")
    resource = _group_mean(df, RESOURCE_CYCLE_CLUSTERS, "weighted_pct_change")
    growth = _group_mean(df, GROWTH_CLUSTERS, "weighted_pct_change")
    big = _group_mean(df, BIG_CLUSTERS, "weighted_pct_change")
    small = _group_mean(df, SMALL_CLUSTERS, "weighted_pct_change")
    chinext = _group_mean(df, CHINEXT, "weighted_pct_change")
    star = _group_mean(df, STAR, "weighted_pct_change")
    up = df.sort_values("weighted_pct_change", ascending=False)
    down = df.sort_values("weighted_pct_change", ascending=True)
    style_values = {"红利防御": defense, "资源周期": resource, "科技成长": growth}
    strongest_style = _strongest_group(style_values)
    style = f"{strongest_style}相对占优" if strongest_style != "数据不足" else "风格方向数据不足"
    market_returns = pd.to_numeric(df.get("weighted_pct_change"), errors="coerce").dropna()
    broad_market_up = bool(not market_returns.empty and (market_returns > 0).mean() >= 0.60)
    cap_side = "大盘" if big >= small else "小盘"
    cap = f"{cap_side}涨幅相对占优" if broad_market_up else f"{cap_side}相对抗跌"
    board = "创业板强于科创板" if chinext >= star else "科创板强于创业板"
    return f"价格层：{style}，{cap}，{board}；相对较强为{_names(up)}，相对较弱为{_names(down)}。"


def flow_layer(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "份额层：数据不足，不能判断估算净申购方向。"
    df = _flow_eligible(cluster_daily)
    if df.empty:
        return "份额层：资金流规模覆盖不足，不能判断估算净申购方向。"
    flow1 = pd.to_numeric(df.get("estimated_net_subscription_100m"), errors="coerce")
    pct = pd.to_numeric(df.get("weighted_pct_change"), errors="coerce")
    inflow = df.assign(_flow=flow1).sort_values("_flow", ascending=False)
    outflow = df.assign(_flow=flow1).sort_values("_flow", ascending=True)
    reverse = df[(flow1 > 0) & (pct < 0)].sort_values("estimated_net_subscription_100m", ascending=False)
    trend = df[(flow1 > 0) & (pct > 0)].sort_values("estimated_net_subscription_100m", ascending=False)
    concentration = ""
    if "max_contributor_ratio_1d" in df:
        minimum = int(load_temperature_config().get("report", {}).get("min_concentration_member_count", 4))
        member_valid = (
            df["concentration_sample_valid"].map(lambda value: bool(value) if pd.notna(value) else False)
            if "concentration_sample_valid" in df
            else pd.to_numeric(df.get("member_count"), errors="coerce").ge(minimum)
        )
        thresholds = load_temperature_config().get("thresholds", {})
        min_abs_flow = float(thresholds.get("min_concentration_abs_flow_100m", 1.0))
        min_flow_to_size = float(thresholds.get("min_concentration_flow_to_size", 0.005))
        material = (
            pd.to_numeric(df.get("estimated_net_subscription_100m"), errors="coerce").abs().ge(min_abs_flow)
            | pd.to_numeric(df.get("one_day_flow_to_size"), errors="coerce").abs().ge(min_flow_to_size)
        )
        high = df[member_valid & material & pd.to_numeric(df["max_contributor_ratio_1d"], errors="coerce").gt(0.5)]
        if not high.empty:
            concentration = f"；{_names(high, 2)}的同向单日资金由少数ETF主导"
    return (
        f"份额层：估算净申购较强为{_names(inflow)}，估算净赎回较强为{_names(outflow)}；"
        f"{_names(reverse)}体现逆向申购，{_names(trend)}体现顺势申购{concentration}。"
    )


def temperature_layer(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "温度层：数据不足，不能判断升降温阶段。"
    df = cluster_daily.copy()
    hot_warming = df[
        df.get("temperature_level", pd.Series("", index=df.index)).eq("高温")
        & df.get("temperature_direction", pd.Series("", index=df.index)).astype(str).str.contains("升温", na=False)
    ].sort_values("cluster_temperature", ascending=False)
    hot_cooling = df[
        df.get("temperature_level", pd.Series("", index=df.index)).eq("高温")
        & df.get("temperature_direction", pd.Series("", index=df.index)).astype(str).str.contains("降温", na=False)
    ].sort_values("cluster_temperature", ascending=False)
    stages = df.get("temperature_stage", pd.Series("", index=df.index))
    mid_up = df[stages.eq("中温转强")]
    low_down = df[stages.eq("低温延续")]
    warm = df[df.get("temperature_level", pd.Series("", index=df.index)).eq("偏热")].sort_values(
        "cluster_temperature", ascending=False
    )
    top_phase = df.sort_values("cluster_temperature", ascending=False).head(1)
    return (
        f"温度层：高温且升温的是{_names(hot_warming)}，高温但降温的是{_names(hot_cooling)}，持续偏热的是{_names(warm)}，"
        f"中温转强的是{_names(mid_up)}，低温延续的是{_names(low_down)}；当前最高温集群为{_names(top_phase, 1)}。"
    )


def style_migration(cluster_daily: pd.DataFrame) -> list[str]:
    if cluster_daily.empty:
        return []
    cluster_daily = _flow_eligible(cluster_daily)
    if cluster_daily.empty:
        return ["资金流规模覆盖不足，暂不判断风格迁移。"]
    big_flow = _group_sum(cluster_daily, BIG_CLUSTERS, "estimated_net_subscription_100m")
    small_flow = _group_sum(cluster_daily, SMALL_CLUSTERS, "estimated_net_subscription_100m")
    defense_flow = _group_sum(cluster_daily, DIVIDEND_DEFENSE_CLUSTERS, "estimated_net_subscription_100m")
    resource_flow = _group_sum(cluster_daily, RESOURCE_CYCLE_CLUSTERS, "estimated_net_subscription_100m")
    growth_flow = _group_sum(cluster_daily, GROWTH_CLUSTERS, "estimated_net_subscription_100m")
    chinext_flow = _group_sum(cluster_daily, CHINEXT, "estimated_net_subscription_100m")
    star_flow = _group_sum(cluster_daily, STAR, "estimated_net_subscription_100m")
    reverse_count = int(
        (
            (pd.to_numeric(cluster_daily.get("estimated_net_subscription_100m"), errors="coerce") > 0)
            & (pd.to_numeric(cluster_daily.get("weighted_pct_change"), errors="coerce") < 0)
        ).sum()
    )
    return [
        f"大盘 {_fmt_flow(big_flow)} vs 小盘 {_fmt_flow(small_flow)}：{'大盘承接更强' if big_flow >= small_flow else '小盘承接更强'}。",
        (
            f"红利防御 {_fmt_flow(defense_flow)}、资源周期 {_fmt_flow(resource_flow)}、科技成长 {_fmt_flow(growth_flow)}："
            f"{_strongest_group({'红利防御': defense_flow, '资源周期': resource_flow, '科技成长': growth_flow})}承接更强。"
        ),
        f"创业板 {_fmt_flow(chinext_flow)} vs 科创板 {_fmt_flow(star_flow)}：{'创业板承接更强' if chinext_flow >= star_flow else '科创板承接更强'}。",
        f"顺势申购 vs 逆向申购：逆向申购集群 {reverse_count} 个，需结合价格层判断风险偏好。",
    ]


def composite_one_sentence(cluster_daily: pd.DataFrame) -> str:
    if cluster_daily.empty:
        return "数据不足，不能给出正式市场判断。"
    price = price_layer(cluster_daily).replace("价格层：", "").rstrip("。")
    flow = flow_layer(cluster_daily).replace("份额层：", "").rstrip("。")
    temp = temperature_layer(cluster_daily).replace("温度层：", "").rstrip("。")
    return f"{price}；{flow}；{temp}。"


def validate_narrative(text: str, cluster_daily: pd.DataFrame | None = None) -> dict[str, Any]:
    banned = [phrase for phrase in BANNED_PHRASES if phrase in text]
    status = not banned
    if cluster_daily is not None and not cluster_daily.empty:
        growth_price = _group_mean(cluster_daily, GROWTH_CLUSTERS, "weighted_pct_change")
        if pd.notna(growth_price) and growth_price < 0 and "价格偏科技成长" in text:
            status = False
            banned.append("price_growth_contradiction")
    return {"narrative_consistency_status": bool(status), "narrative_violations": banned}
