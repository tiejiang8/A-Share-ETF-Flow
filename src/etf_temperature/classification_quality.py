from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


THEME_HINTS = [
    "创业板",
    "科创",
    "证券",
    "券商",
    "银行",
    "半导体",
    "芯片",
    "人工智能",
    "通信",
    "机器人",
    "消费",
    "白酒",
    "医药",
    "创新药",
    "光伏",
    "新能源",
    "电池",
    "储能",
    "军工",
    "有色",
    "煤炭",
    "化工",
    "电力",
    "传媒",
    "游戏",
    "软件",
]


def classify_unclassified_importance(etf_daily: pd.DataFrame) -> pd.DataFrame:
    """Split unclassified ETFs by economic materiality and explicit theme relevance."""
    if etf_daily.empty:
        return pd.DataFrame()
    df = etf_daily.copy()
    unclassified = df[df.get("primary_cluster", pd.Series("", index=df.index)).fillna("").eq("")].copy()
    if unclassified.empty:
        return unclassified

    metric_specs = [
        ("fund_size_100m", False),
        ("amount", False),
        ("estimated_net_subscription_100m", True),
        ("estimated_net_subscription_5d_100m", True),
    ]
    for column, _ in metric_specs:
        df[column] = pd.to_numeric(df.get(column, pd.Series(np.nan, index=df.index)), errors="coerce")
        unclassified[column] = pd.to_numeric(
            unclassified.get(column, pd.Series(np.nan, index=unclassified.index)), errors="coerce"
        )

    important_codes: set[str] = set()
    material_top_n = max(1, int(math.ceil(len(df) * 0.05)))
    for column, by_abs in metric_specs:
        values = df[column].abs() if by_abs else df[column]
        eligible = values[values.notna() & values.gt(0)]
        if eligible.empty:
            continue
        important_codes.update(
            df.loc[eligible.nlargest(material_top_n).index, "etf_code"].astype(str)
        )

    for column in ["etf_name", "expanded_name", "tracking_index_name"]:
        if column not in unclassified:
            unclassified[column] = ""
    text = unclassified[["etf_name", "expanded_name", "tracking_index_name"]].fillna("").agg(" ".join, axis=1)
    thematic = text.str.contains("|".join(THEME_HINTS), regex=True, na=False)
    important_codes.update(unclassified.loc[thematic, "etf_code"].astype(str))

    unclassified["unclassified_bucket"] = np.where(
        unclassified["etf_code"].astype(str).isin(important_codes),
        "重要未分类",
        "合理未分类",
    )
    unclassified.loc[
        unclassified["unclassified_bucket"].eq("合理未分类")
        & (
            unclassified["fund_size_100m"].rank(ascending=False, pct=True).le(0.25)
            | unclassified["amount"].rank(ascending=False, pct=True).le(0.25)
        ),
        "unclassified_bucket",
    ] = "疑似漏分类"
    return unclassified


def classification_coverage(etf_daily: pd.DataFrame) -> dict[str, Any]:
    if etf_daily.empty:
        return {
            "classified_count_coverage": 0.0,
            "classified_scale_coverage": 0.0,
            "classified_turnover_coverage": 0.0,
            "classified_flow_coverage": 0.0,
            "important_unclassified_count": 0,
            "unclassified_count": 0,
        }
    classified = etf_daily.get("primary_cluster", pd.Series("", index=etf_daily.index)).fillna("").ne("")
    size = pd.to_numeric(etf_daily.get("fund_size_100m"), errors="coerce").fillna(0)
    amount = pd.to_numeric(etf_daily.get("amount"), errors="coerce").fillna(0).abs()
    flow = pd.to_numeric(etf_daily.get("estimated_net_subscription_100m"), errors="coerce").fillna(0).abs()
    important = classify_unclassified_importance(etf_daily)
    return {
        "classified_count_coverage": float(classified.mean()) if len(classified) else 0.0,
        "classified_scale_coverage": float(size[classified].sum() / size.sum()) if size.sum() else 0.0,
        "classified_turnover_coverage": float(amount[classified].sum() / amount.sum()) if amount.sum() else 0.0,
        "classified_flow_coverage": float(flow[classified].sum() / flow.sum()) if flow.sum() else 0.0,
        "important_unclassified_count": int(important["unclassified_bucket"].eq("重要未分类").sum()) if not important.empty else 0,
        "unclassified_count": int((~classified).sum()),
    }
