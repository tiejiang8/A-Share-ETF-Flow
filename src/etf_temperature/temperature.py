from __future__ import annotations

import numpy as np
import pandas as pd

from .config import load_temperature_config


def _clip_temperature(series: pd.Series) -> pd.Series:
    config = load_temperature_config()
    thresholds = config["thresholds"]
    return pd.to_numeric(series, errors="coerce").clip(
        float(thresholds["min_temperature"]),
        float(thresholds["max_temperature"]),
    )


def _linear_score(series: pd.Series, low: float, high: float) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if high == low:
        return pd.Series(np.nan, index=series.index)
    return ((values - low) / (high - low) * 100).clip(0, 100)


def _risk_score(premium_discount: pd.Series) -> pd.Series:
    config = load_temperature_config()
    max_abs = float(config["scaling"]["premium_discount_abs"]["max"])
    values = pd.to_numeric(premium_discount, errors="coerce").abs()
    return (1 - values.clip(upper=max_abs) / max_abs) * 100


def _percentile_score(series: pd.Series, group: pd.Series | None = None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if group is None:
        if values.notna().sum() <= 1:
            return pd.Series(50.0, index=series.index)
        return (values.rank(pct=True) * 100).fillna(50.0)
    out = pd.Series(50.0, index=series.index)
    for _, idx in group.groupby(group).groups.items():
        subset = values.loc[idx]
        if subset.notna().sum() <= 1:
            out.loc[idx] = 50.0
        else:
            out.loc[idx] = subset.rank(pct=True) * 100
    return out.fillna(50.0)


def consistency_score(pct_change: pd.Series, share_daily_change: pd.Series) -> pd.Series:
    config = load_temperature_config()
    scores = config["consistency_scores"]
    pct = pd.to_numeric(pct_change, errors="coerce")
    share = pd.to_numeric(share_daily_change, errors="coerce")
    out = pd.Series(float(scores["flat_or_missing"]), index=pct.index)
    out.loc[(pct > 0) & (share > 0)] = float(scores["price_up_share_up"])
    out.loc[(pct < 0) & (share > 0)] = float(scores["price_down_share_up"])
    out.loc[(pct > 0) & (share < 0)] = float(scores["price_up_share_down"])
    out.loc[(pct < 0) & (share < 0)] = float(scores["price_down_share_down"])
    out.loc[pct.isna() | share.isna() | pct.eq(0) | share.eq(0)] = float(scores["flat_or_missing"])
    return out


def compute_product_temperature(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    config = load_temperature_config()
    weights = config["product_temperature_weights"]
    scaling = config["scaling"]
    final_weights = config["final_temperature_weights"]
    out = df.copy()

    price_trend_raw = pd.to_numeric(out["close"], errors="coerce") / pd.to_numeric(out["ma10"], errors="coerce") - 1
    momentum_raw = pd.concat(
        [
            pd.to_numeric(out.get("return_5d"), errors="coerce"),
            pd.to_numeric(out.get("return_10d"), errors="coerce"),
        ],
        axis=1,
    ).mean(axis=1, skipna=True)
    one_day_flow_ratio = pd.to_numeric(out.get("estimated_net_subscription_100m"), errors="coerce") / pd.to_numeric(
        out.get("fund_size_100m"), errors="coerce"
    ).replace(0, np.nan)
    five_day_flow_ratio = pd.to_numeric(out.get("estimated_net_subscription_5d_100m"), errors="coerce") / pd.to_numeric(
        out.get("fund_size_100m"), errors="coerce"
    ).replace(0, np.nan)

    score_map = {
        "score_price_trend": _linear_score(price_trend_raw, **scaling["price_trend"]),
        "score_short_momentum": _linear_score(momentum_raw, **scaling["momentum"]),
        "score_turnover_activity": _linear_score(
            pd.to_numeric(out.get("amount_vs_5d"), errors="coerce"),
            **scaling["turnover_activity"],
        ),
        "score_one_day_net_subscription_to_size": _linear_score(
            one_day_flow_ratio,
            **scaling["one_day_net_subscription_to_size"],
        ),
        "score_five_day_net_subscription_to_size": _linear_score(
            five_day_flow_ratio,
            **scaling["five_day_net_subscription_to_size"],
        ),
        "score_consistency": consistency_score(out.get("pct_change"), out.get("share_daily_change")),
        "score_premium_discount_risk": _risk_score(out.get("premium_discount")),
    }
    for column, values in score_map.items():
        out[column] = values

    component_columns = {
        "price_trend": "score_price_trend",
        "short_momentum": "score_short_momentum",
        "turnover_activity": "score_turnover_activity",
        "one_day_net_subscription_to_size": "score_one_day_net_subscription_to_size",
        "five_day_net_subscription_to_size": "score_five_day_net_subscription_to_size",
        "price_volume_share_consistency": "score_consistency",
        "premium_discount_risk": "score_premium_discount_risk",
    }
    absolute = pd.Series(0.0, index=out.index)
    valid_weight = pd.Series(0.0, index=out.index)
    for key, column in component_columns.items():
        weight = float(weights[key])
        score = pd.to_numeric(out[column], errors="coerce")
        absolute += score.fillna(50.0) * weight
        valid_weight += score.notna().astype(float) * weight

    out["absolute_temperature"] = _clip_temperature(absolute)
    out["market_percentile_temperature"] = _percentile_score(out["absolute_temperature"])
    out["cluster_percentile_temperature"] = _percentile_score(
        out["absolute_temperature"],
        out.get("primary_cluster", pd.Series("", index=out.index)).fillna(""),
    )
    out["final_temperature"] = _clip_temperature(
        out["absolute_temperature"] * float(final_weights["absolute_temperature"])
        + out["market_percentile_temperature"] * float(final_weights["market_percentile_temperature"])
        + out["cluster_percentile_temperature"] * float(final_weights["cluster_percentile_temperature"])
    )
    out["temperature_confidence"] = valid_weight.clip(0, 1)
    out["trusted_temperature"] = _clip_temperature(
        50 + (out["final_temperature"] - 50) * out["temperature_confidence"]
    )
    out["temperature"] = out["trusted_temperature"]
    out["temperature_change"] = np.nan
    out = add_temperature_state_columns(out)
    return out


def temperature_level(value: float | int | None) -> str:
    if pd.isna(value):
        return "数据缺失"
    number = float(value)
    if number < 30:
        return "低温"
    if number < 45:
        return "偏冷"
    if number < 60:
        return "中性"
    if number < 75:
        return "偏热"
    return "高温"


def temperature_band_definition() -> str:
    """Canonical temperature bands used by every report surface."""
    return "低温<30，偏冷30—45，中性45—60，偏热60—75，高温≥75"


def temperature_direction(change_1d: float | None, change_3d: float | None = None, slope_5d: float | None = None) -> str:
    values = [value for value in [change_1d, change_3d, slope_5d] if pd.notna(value)]
    if not values:
        return "稳定"
    score = np.nanmean(values)
    if score >= 10:
        return "快速升温"
    if score >= 3:
        return "温和升温"
    if score <= -10:
        return "快速降温"
    if score <= -3:
        return "温和降温"
    return "稳定"


def temperature_phase_from_values(
    temp: float | None,
    change_1d: float | None,
    change_3d: float | None = None,
    slope_5d: float | None = None,
    hot_days_5d: float | None = None,
    continuous_warming: bool | None = None,
    cooling_from_high: bool | None = None,
) -> str:
    if pd.isna(temp):
        return "数据缺失"
    level = temperature_level(temp)
    direction = temperature_direction(change_1d, change_3d, slope_5d)
    if bool(cooling_from_high) or (level == "高温" and "降温" in direction):
        return "高温降温"
    if level == "高温" and (bool(continuous_warming) or "升温" in direction or (pd.notna(hot_days_5d) and hot_days_5d >= 3)):
        return "高温延续"
    if bool(continuous_warming):
        return "持续升温"
    if level in {"低温", "偏冷"} and "升温" in direction:
        return "冷启动"
    if level == "中性" and "升温" in direction:
        return "中温转强"
    if level == "中性" and "降温" in direction:
        return "中温走弱"
    if level in {"低温", "偏冷"}:
        return "低温延续"
    if level == "偏热":
        return "偏热降温" if "降温" in direction else "持续偏热"
    if level == "高温":
        return "高温延续"
    if pd.notna(change_1d) and pd.notna(change_3d) and np.sign(change_1d) != np.sign(change_3d) and abs(change_1d) >= 8:
        return "剧烈分化"
    return "中温走弱" if "降温" in direction else "中温转强" if "升温" in direction else "剧烈分化"


def add_temperature_state_columns(df: pd.DataFrame, temperature_col: str = "temperature") -> pd.DataFrame:
    if df.empty or temperature_col not in df:
        return df
    out = df.copy()
    def numeric_series(column: str, fallback: str | None = None) -> pd.Series:
        if column in out:
            return pd.to_numeric(out[column], errors="coerce")
        if fallback and fallback in out:
            return pd.to_numeric(out[fallback], errors="coerce")
        return pd.Series(np.nan, index=out.index)

    change_1d = numeric_series("temperature_change_1d", "temperature_change")
    change_3d = numeric_series("temperature_change_3d")
    slope_5d = numeric_series("temperature_slope_5d")
    if temperature_col == "cluster_temperature":
        change_1d = numeric_series("cluster_temperature_change_1d", "temperature_change")
        change_3d = numeric_series("cluster_temperature_change_3d")
        slope_5d = numeric_series("cluster_temperature_slope_5d")
    temp = pd.to_numeric(out[temperature_col], errors="coerce")
    out["temperature_level"] = temp.map(temperature_level)
    out["temperature_direction"] = [
        temperature_direction(a, b, c) for a, b, c in zip(change_1d, change_3d, slope_5d)
    ]
    out["temperature_phase"] = [
        temperature_phase_from_values(
            t,
            a,
            b,
            c,
            h,
            w,
            cool,
        )
        for t, a, b, c, h, w, cool in zip(
            temp,
            change_1d,
            change_3d,
            slope_5d,
            pd.to_numeric(out.get("hot_days_5d"), errors="coerce") if "hot_days_5d" in out else pd.Series(np.nan, index=out.index),
            out.get("continuous_warming", pd.Series(False, index=out.index)).fillna(False) if "continuous_warming" in out else pd.Series(False, index=out.index),
            out.get("cooling_from_high", pd.Series(False, index=out.index)).fillna(False) if "cooling_from_high" in out else pd.Series(False, index=out.index),
        )
    ]
    out["temperature_stage"] = out["temperature_phase"]
    return out


def add_product_temperature_history_features(current: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if current.empty:
        return current
    config = load_temperature_config()
    thresholds = config["thresholds"]
    history_cfg = config["history"]
    out = current.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    prior = history.copy() if history is not None and not history.empty else pd.DataFrame()
    if not prior.empty:
        prior["date"] = pd.to_datetime(prior["date"]).dt.strftime("%Y-%m-%d")
        prior = prior[~prior.set_index(["date", "etf_code"]).index.isin(out.set_index(["date", "etf_code"]).index)]
    combined = pd.concat([prior, out], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["etf_code", "date"])
    window = int(history_cfg["signal_window"])
    slope_window = int(history_cfg["temperature_slope_window"])
    change_3d_lag = int(history_cfg["change_3d_lag"])
    high_temperature = float(thresholds["high_temperature"])
    high_cooling_anchor = float(thresholds["cooling_from_high_min_previous_temperature"])
    continuous_days = int(history_cfg["continuous_warming_days"])

    rows = []
    current_keys = set(out.set_index(["date", "etf_code"]).index)
    for _, group in combined.groupby("etf_code"):
        group = group.sort_values("date").copy()
        temp = pd.to_numeric(group["temperature"], errors="coerce")
        flow = pd.to_numeric(group.get("estimated_net_subscription_100m"), errors="coerce")
        group["temperature_change_1d"] = temp.diff(1)
        group["temperature_change_3d"] = temp - temp.shift(change_3d_lag)
        group["hot_days_5d"] = temp.ge(high_temperature).rolling(window, min_periods=1).sum()
        group["flow_positive_days_5d"] = flow.gt(0).rolling(window, min_periods=1).sum()
        group["temperature_slope_5d"] = temp - temp.shift(slope_window - 1)
        warming = temp.diff().gt(float(thresholds["warming_min_delta"]))
        group["continuous_warming"] = warming.rolling(continuous_days - 1, min_periods=continuous_days - 1).sum().ge(continuous_days - 1)
        group["cooling_from_high"] = temp.shift(1).ge(high_cooling_anchor) & temp.lt(temp.shift(1))
        keys = group.assign(date_str=group["date"].dt.strftime("%Y-%m-%d")).set_index(["date_str", "etf_code"]).index
        rows.append(group.loc[keys.isin(current_keys)])
    result = pd.concat(rows, ignore_index=True, sort=False) if rows else out
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    result["temperature_change"] = result["temperature_change_1d"]
    return add_temperature_state_columns(result)


def cluster_temperature_from_raw(cluster_daily: pd.DataFrame) -> pd.DataFrame:
    if cluster_daily.empty:
        return cluster_daily
    config = load_temperature_config()
    weights = config["cluster_temperature_weights"]
    scaling = config["scaling"]
    out = cluster_daily.copy()
    total_size = pd.to_numeric(out.get("total_size_100m"), errors="coerce").replace(0, np.nan)
    one_day_ratio = pd.to_numeric(out.get("estimated_net_subscription_100m"), errors="coerce") / total_size
    five_day_ratio = pd.to_numeric(out.get("estimated_net_subscription_5d_100m"), errors="coerce") / total_size
    score_columns = {
        "weighted_price_trend": _linear_score(
            pd.to_numeric(out.get("weighted_pct_change"), errors="coerce"),
            **scaling["cluster_weighted_price_trend"],
        ),
        "cluster_momentum": _linear_score(
            pd.to_numeric(out.get("weighted_return_5d"), errors="coerce"),
            **scaling["cluster_momentum"],
        ),
        "turnover_relative_level": _linear_score(
            pd.to_numeric(out.get("amount_vs_5d"), errors="coerce"),
            **scaling["cluster_turnover_relative_level"],
        ),
        "one_day_net_subscription_to_size": _linear_score(
            one_day_ratio,
            **scaling["cluster_net_subscription_to_size"],
        ),
        "five_day_net_subscription_to_size": _linear_score(
            five_day_ratio,
            **scaling["cluster_five_day_net_subscription_to_size"],
        ),
        "member_breadth": pd.to_numeric(out.get("share_increase_member_ratio"), errors="coerce") * 100,
        "premium_discount_risk": _risk_score(out.get("median_premium_discount")),
    }
    temperature = pd.Series(0.0, index=out.index)
    valid_weight = pd.Series(0.0, index=out.index)
    for key, score in score_columns.items():
        weight = float(weights[key])
        out[f"score_{key}"] = score
        coverage = pd.Series(1.0, index=out.index)
        if key == "one_day_net_subscription_to_size":
            coverage = pd.to_numeric(
                out.get("one_day_flow_size_coverage", pd.Series(1.0, index=out.index)), errors="coerce"
            ).fillna(0).clip(0, 1)
        elif key == "five_day_net_subscription_to_size":
            coverage = pd.to_numeric(
                out.get("five_day_flow_size_coverage", pd.Series(1.0, index=out.index)), errors="coerce"
            ).fillna(0).clip(0, 1)
        coverage = coverage.where(score.notna(), 0.0)
        coverage_adjusted_score = 50.0 + (score.fillna(50.0) - 50.0) * coverage
        temperature += coverage_adjusted_score * weight
        valid_weight += coverage * weight
    out["cluster_temperature"] = _clip_temperature(temperature)
    out["cluster_temperature_confidence"] = valid_weight.clip(0, 1)
    return add_temperature_state_columns(out, "cluster_temperature")


def add_cluster_temperature_history_features(current: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if current.empty:
        return current
    out = current.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    prior = history.copy() if history is not None and not history.empty else pd.DataFrame()
    if not prior.empty:
        prior["date"] = pd.to_datetime(prior["date"]).dt.strftime("%Y-%m-%d")
        prior = prior[~prior.set_index(["date", "cluster_id"]).index.isin(out.set_index(["date", "cluster_id"]).index)]
    combined = pd.concat([prior, out], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["cluster_id", "date"])
    current_keys = set(out.set_index(["date", "cluster_id"]).index)
    rows = []
    for _, group in combined.groupby("cluster_id"):
        group = group.sort_values("date").copy()
        temp = pd.to_numeric(group["cluster_temperature"], errors="coerce")
        group["temperature_change"] = temp.diff(1)
        group["cluster_temperature_change_1d"] = temp.diff(1)
        group["cluster_temperature_change_3d"] = temp - temp.shift(3)
        group["cluster_temperature_slope_5d"] = temp - temp.shift(4)
        keys = group.assign(date_str=group["date"].dt.strftime("%Y-%m-%d")).set_index(["date_str", "cluster_id"]).index
        rows.append(group.loc[keys.isin(current_keys)])
    result = pd.concat(rows, ignore_index=True, sort=False) if rows else out
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    return add_temperature_state_columns(result, "cluster_temperature")
