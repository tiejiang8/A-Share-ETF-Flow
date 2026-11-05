from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from .anchors import apply_anchor_supplements


EXCLUDE_KEYWORDS = [
    "港股",
    "恒生",
    "香港",
    "H股",
    "跨境",
    "沪港深",
    "港美",
    "纳斯达克",
    "纳指",
    "标普",
    "美国",
    "德国",
    "法国",
    "日经",
    "日本",
    "韩国",
    "东南亚",
    "新兴亚洲",
    "亚洲精选",
    "巴西",
    "越南",
    "东盟",
    "新加坡",
    "欧洲",
    "英国",
    "加拿大",
    "澳洲",
    "墨西哥",
    "拉美",
    "沙特",
    "印度",
    "全球",
    "海外",
    "QDII",
    "中概",
    "中韩",
    "韩交所",
    "债",
    "国债",
    "信用债",
    "政金债",
    "公司债",
    "城投债",
    "可转债",
    "短融",
    "货币",
    "现金",
    "保证金",
    "快钱",
    "快线",
    "添富快钱",
    "银华日利",
    "黄金",
    "上海金",
    "金ETF",
    "贵金属",
    "原油",
    "油气",
    "豆粕",
    "商品",
    "期货",
    "REIT",
    "REITS",
    "不动产",
]

INCLUDE_HINTS = [
    "上证",
    "沪深",
    "中证",
    "创业板",
    "科创",
    "红利",
    "证券",
    "券商",
    "银行",
    "半导体",
    "芯片",
    "人工智能",
    "云计算",
    "通信",
    "机器人",
    "电子",
    "消费电子",
    "消费",
    "养殖",
    "畜牧",
    "粮食",
    "白酒",
    "医药",
    "医疗",
    "中药",
    "生物医药",
    "软件",
    "传媒",
    "游戏",
    "电力",
    "绿电",
    "绿色电力",
    "新能源",
    "光伏",
    "电池",
    "储能",
    "军工",
    "有色",
    "钢铁",
    "资源",
    "煤炭",
    "化工",
    "稀土",
    "农业",
    "环保",
    "地产",
    "房地产",
    "旅游",
    "计算机",
    "信创",
    "数字经济",
    "科创创业",
    "双创",
    "机械",
    "工程机械",
    "材料",
    "金融科技",
    "物联网",
    "碳中和",
    "汽车",
]


EXCLUDE_PREFIXES_BY_EXCHANGE = {
    "SSE": ["511", "513", "518"],
}


def _text(row: pd.Series) -> str:
    return " ".join(
        str(row.get(col) or "")
        for col in ["etf_name", "expanded_name", "tracking_index_name", "tracking_index_code", "etf_type"]
    )


def classify_a_share_equity(row: pd.Series) -> tuple[bool, str]:
    text = _text(row)
    upper_text = text.upper()
    code = str(row.get("etf_code") or "").zfill(6)
    exchange = str(row.get("exchange") or "")
    for prefix in EXCLUDE_PREFIXES_BY_EXCHANGE.get(exchange, []):
        if code.startswith(prefix):
            return False, f"excluded_code_prefix:{prefix}"
    for keyword in EXCLUDE_KEYWORDS:
        if keyword.upper() in upper_text:
            return False, f"excluded_keyword:{keyword}"
    if exchange == "SSE" and row.get("etf_type") == "stock_etf":
        return True, ""
    if any(keyword.upper() in upper_text for keyword in INCLUDE_HINTS):
        return True, ""
    if re.search(r"\b(000|399|93|98)\d{3}\b", upper_text):
        return True, ""
    return False, "not_confirmed_a_share_equity"


def apply_a_share_filter(universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return universe
    out = universe.copy()
    flags = out.apply(classify_a_share_equity, axis=1, result_type="expand")
    out["is_a_share_equity_etf"] = flags[0].astype(bool)
    out["exclusion_reason"] = flags[1]
    out["universe_version"] = datetime.now().strftime("%Y%m%d")
    return out


def build_universe(sse_df: pd.DataFrame, szse_df: pd.DataFrame) -> pd.DataFrame:
    universe = pd.concat([sse_df, szse_df], ignore_index=True, sort=False)
    if universe.empty:
        return universe
    universe = universe.drop_duplicates(["exchange", "etf_code"], keep="first").copy()
    universe = apply_a_share_filter(universe)
    has_real_source = "source" in universe.columns and universe["source"].notna().any()
    if universe["is_a_share_equity_etf"].any() and (has_real_source or len(universe) > 100):
        universe = apply_anchor_supplements(universe)
        universe = apply_a_share_filter(universe)
    universe["retrieved_at"] = universe.get("retrieved_at", datetime.now().isoformat(timespec="seconds"))
    return universe.sort_values(["exchange", "etf_code"]).reset_index(drop=True)


def universe_summary(universe: pd.DataFrame, membership: pd.DataFrame | None = None) -> dict:
    a_share = universe[universe["is_a_share_equity_etf"]] if "is_a_share_equity_etf" in universe else universe.iloc[0:0]
    unclassified = 0
    if membership is not None and not membership.empty:
        unclassified = int(membership["primary_cluster"].isna().sum() + (membership["primary_cluster"].eq("")).sum())
    return {
        "etf_total": int(len(universe)),
        "a_share_equity_etf_count": int(len(a_share)),
        "excluded_etf_count": int(len(universe) - len(a_share)),
        "unclassified_count": unclassified,
    }
