from __future__ import annotations

import numpy as np
import pandas as pd

from .corporate_actions import apply_corporate_action_adjustments
from .flow_structure import add_flow_speed_columns
from .official_endpoints import normalize_date, safe_div


def _target_slice(df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df.empty or "date" not in df:
        return pd.DataFrame()
    target = normalize_date(target_date)
    return df[df["date"].eq(target)].copy()


def compute_share_features(shares: pd.DataFrame, target_date: str) -> pd.DataFrame:
    columns = [
        "etf_code",
        "date",
        "latest_shares",
        "previous_shares",
        "shares_5d_ago",
        "share_daily_change",
        "share_daily_change_pct",
        "share_5d_change",
        "share_5d_change_pct",
        "flow_5d_valid_days",
        "share_source",
        "share_data_date",
        "share_validation_status",
    ]
    if shares.empty:
        return pd.DataFrame(columns=columns)
    shares = shares.dropna(subset=["date"]).copy()
    shares["date"] = shares["date"].map(normalize_date)
    shares = shares.sort_values(["etf_code", "date"])
    records = []
    for code, group in shares.groupby("etf_code"):
        group = group[group["date"].le(normalize_date(target_date) or "9999-99-99")].tail(8)
        current = group[group["date"].eq(normalize_date(target_date))]
        if current.empty:
            continue
        cur = current.iloc[-1]
        prev = group.iloc[-2] if len(group) >= 2 else None
        prev5 = group.iloc[-6] if len(group) >= 6 else None
        shares_now = cur.get("shares")
        shares_prev = None if prev is None else prev.get("shares")
        shares_prev5 = None if prev5 is None else prev5.get("shares")
        valid_days = min(max(len(group) - 1, 0), 5)
        daily_change = None if shares_prev is None else shares_now - shares_prev
        change_5d = None if shares_prev5 is None else shares_now - shares_prev5
        records.append(
            {
                "etf_code": code,
                "date": cur["date"],
                "latest_shares": shares_now,
                "previous_shares": shares_prev,
                "shares_5d_ago": shares_prev5,
                "share_daily_change": daily_change,
                "share_daily_change_pct": safe_div(daily_change, shares_prev),
                "share_5d_change": change_5d,
                "share_5d_change_pct": safe_div(change_5d, shares_prev5),
                "flow_5d_valid_days": valid_days,
                "share_source": cur.get("source"),
                "share_data_date": cur.get("data_date"),
                "share_validation_status": cur.get("validation_status", "ok"),
            }
        )
    return pd.DataFrame(records, columns=columns)


def compute_quote_features(quotes: pd.DataFrame, target_date: str) -> pd.DataFrame:
    columns = [
        "etf_code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "close_5d_ago",
        "prev_close",
        "price_adjustment_factor_5d",
        "pct_change",
        "volume",
        "amount",
        "amount_5d_mean",
        "amount_vs_5d",
        "ma5",
        "ma10",
        "return_5d",
        "return_10d",
        "quote_source",
        "quote_data_date",
        "quote_validation_status",
    ]
    if quotes.empty:
        return pd.DataFrame(columns=columns)
    quotes = quotes.dropna(subset=["date"]).copy()
    quotes["date"] = quotes["date"].map(normalize_date)
    quotes = quotes.sort_values(["etf_code", "date"])
    records = []
    target = normalize_date(target_date) or "9999-99-99"
    for code, group in quotes.groupby("etf_code"):
        group = group[group["date"].le(target)].tail(30)
        current = group[group["date"].eq(target)]
        if current.empty:
            continue
        cur = current.iloc[-1]
        history_before_current = group[group["date"].lt(target)]
        close = cur.get("close")
        raw_closes = pd.to_numeric(group["close"], errors="coerce")
        # Company-action detection is performed later with quote, NAV and share
        # fields together.  Do not infer a split from price alone here.
        adjusted_closes = raw_closes.copy()
        prev_close = cur.get("prev_close")
        if pd.isna(prev_close) and len(group) >= 2:
            prev_close = raw_closes.iloc[-2]
        adjusted_prev_close = adjusted_closes.iloc[-2] if len(group) >= 2 else np.nan
        ma5 = adjusted_closes.tail(5).mean() if adjusted_closes.notna().sum() >= 1 else np.nan
        ma10 = adjusted_closes.tail(10).mean() if adjusted_closes.notna().sum() >= 1 else np.nan
        amount_5d_mean = history_before_current["amount"].tail(5).mean() if "amount" in group else np.nan
        close_5d_ago = adjusted_closes.iloc[-6] if len(group) >= 6 else np.nan
        close_10d_ago = adjusted_closes.iloc[-11] if len(group) >= 11 else np.nan
        pct_change = None if pd.isna(adjusted_prev_close) or adjusted_prev_close == 0 else (close / adjusted_prev_close - 1) * 100
        price_adjustment_factor_5d = 1.0
        records.append(
            {
                "etf_code": code,
                "date": cur["date"],
                "open": cur.get("open"),
                "high": cur.get("high"),
                "low": cur.get("low"),
                "close": close,
                "close_5d_ago": close_5d_ago,
                "prev_close": prev_close,
                "price_adjustment_factor_5d": price_adjustment_factor_5d,
                "pct_change": pct_change,
                "volume": cur.get("volume"),
                "amount": cur.get("amount"),
                "amount_5d_mean": amount_5d_mean,
                "amount_vs_5d": safe_div(cur.get("amount"), amount_5d_mean),
                "ma5": ma5,
                "ma10": ma10,
                "return_5d": safe_div(close - close_5d_ago, close_5d_ago) if pd.notna(close_5d_ago) else None,
                "return_10d": safe_div(close - close_10d_ago, close_10d_ago) if pd.notna(close_10d_ago) else None,
                "quote_source": cur.get("source"),
                "quote_data_date": cur.get("data_date"),
                "quote_validation_status": cur.get("validation_status", "ok"),
            }
        )
    return pd.DataFrame(records, columns=columns)


def compute_aum_features(aum: pd.DataFrame, target_date: str) -> pd.DataFrame:
    columns = [
        "etf_code",
        "date",
        "official_aum_yuan",
        "aum_source",
        "aum_data_date",
        "aum_validation_status",
        "aum_date_convention",
    ]
    if aum is None or aum.empty or not {"etf_code", "date", "aum"}.issubset(aum.columns):
        return pd.DataFrame(columns=columns)
    target = normalize_date(target_date)
    frame = aum.copy()
    frame["date"] = frame["date"].map(normalize_date)
    frame["etf_code"] = frame["etf_code"].astype(str).str.zfill(6)
    frame["aum"] = pd.to_numeric(frame["aum"], errors="coerce")
    frame = frame[frame["date"].eq(target) & frame["aum"].gt(0)].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["source_priority"] = pd.to_numeric(
        frame.get("source_priority", pd.Series(999, index=frame.index)), errors="coerce"
    ).fillna(999)
    frame = frame.sort_values(["etf_code", "source_priority"]).drop_duplicates("etf_code", keep="first")
    return pd.DataFrame(
        {
            "etf_code": frame["etf_code"],
            "date": frame["date"],
            "official_aum_yuan": frame["aum"],
            "aum_source": frame.get("source"),
            "aum_data_date": frame.get("data_date", frame["date"]),
            "aum_validation_status": frame.get("validation_status", "official_aum_only"),
            "aum_date_convention": frame.get("date_convention", "issuer_disclosed_as_of_date"),
        }
    )


def build_etf_daily(
    universe: pd.DataFrame,
    membership: pd.DataFrame,
    quotes: pd.DataFrame,
    shares: pd.DataFrame,
    nav: pd.DataFrame,
    target_date: str,
    aum: pd.DataFrame | None = None,
) -> pd.DataFrame:
    base = universe[universe["is_a_share_equity_etf"]].copy()
    share_features = compute_share_features(shares, target_date)
    quote_features = compute_quote_features(quotes, target_date)
    aum_features = compute_aum_features(aum if aum is not None else pd.DataFrame(), target_date)
    nav_previous = pd.DataFrame(columns=["etf_code", "previous_nav"])
    creation_latest = pd.DataFrame(
        columns=[
            "etf_code",
            "creation_redemption_unit",
            "nav_per_creation_unit",
            "creation_unit_data_date",
            "creation_unit_source",
        ]
    )
    if not nav.empty:
        nav_latest = nav.dropna(subset=["etf_code"]).copy()
        target = pd.to_datetime(normalize_date(target_date), errors="coerce")
        if "date" in nav_latest:
            nav_dates = pd.to_datetime(nav_latest["date"], errors="coerce")
            nav_latest = nav_latest[nav_dates.isna() | nav_dates.le(target)].copy()
        nav_latest["_has_nav"] = nav_latest["nav"].notna().astype(int) if "nav" in nav_latest else 0
        date_values = nav_latest["date"] if "date" in nav_latest else pd.Series(pd.NaT, index=nav_latest.index)
        priority_values = (
            nav_latest["source_priority"] if "source_priority" in nav_latest else pd.Series(999, index=nav_latest.index)
        )
        nav_latest["_date_sort"] = pd.to_datetime(date_values, errors="coerce")
        nav_latest["_priority_sort"] = -pd.to_numeric(priority_values, errors="coerce").fillna(999)
        nav_latest = (
            nav_latest.sort_values(["etf_code", "_has_nav", "_date_sort", "_priority_sort"])
            .drop_duplicates("etf_code", keep="last")
            .drop(columns=["_has_nav", "_date_sort", "_priority_sort"])
        )
        nav_previous = nav.dropna(subset=["etf_code", "nav"]).copy()
        nav_previous["_date_sort"] = pd.to_datetime(nav_previous.get("date"), errors="coerce")
        nav_previous = nav_previous[nav_previous["_date_sort"].lt(target)].copy()
        nav_previous = (
            nav_previous.sort_values(["etf_code", "_date_sort"])
            .drop_duplicates("etf_code", keep="last")[["etf_code", "nav"]]
            .rename(columns={"nav": "previous_nav"})
        )
        if "creation_redemption_unit" in nav:
            creation_latest = nav.dropna(subset=["etf_code", "creation_redemption_unit"]).copy()
            creation_dates = pd.to_datetime(creation_latest.get("date"), errors="coerce")
            creation_latest = creation_latest[creation_dates.isna() | creation_dates.le(target)].copy()
            creation_latest["_date_sort"] = creation_dates.loc[creation_latest.index]
            creation_latest = creation_latest.sort_values(["etf_code", "_date_sort"]).drop_duplicates(
                "etf_code", keep="last"
            )
            creation_latest = creation_latest.rename(
                columns={"data_date": "creation_unit_data_date", "source": "creation_unit_source"}
            )
            creation_columns = [
                "etf_code",
                "creation_redemption_unit",
                "nav_per_creation_unit",
                "creation_unit_data_date",
                "creation_unit_source",
            ]
            creation_latest = creation_latest[
                [column for column in creation_columns if column in creation_latest.columns]
            ]
    else:
        nav_latest = pd.DataFrame(columns=["etf_code"])
    df = base.merge(membership[["etf_code", "primary_cluster", "primary_cluster_name", "secondary_tags", "classification_basis", "confidence"]], on="etf_code", how="left")
    df = df.merge(quote_features, on="etf_code", how="left")
    df = df.merge(share_features, on="etf_code", how="left", suffixes=("", "_share"))
    df = df.merge(aum_features.drop(columns=["date"], errors="ignore"), on="etf_code", how="left")
    nav_cols = ["etf_code", "nav", "data_date", "source", "validation_status"]
    available_nav_cols = [col for col in nav_cols if col in nav_latest.columns]
    df = df.merge(nav_latest[available_nav_cols].rename(columns={"data_date": "nav_data_date", "source": "nav_source", "validation_status": "nav_validation_status"}), on="etf_code", how="left")
    df = df.merge(nav_previous, on="etf_code", how="left")
    df = df.merge(creation_latest, on="etf_code", how="left")
    df["date"] = normalize_date(target_date)
    df["fund_size_yuan"] = pd.to_numeric(df.get("official_aum_yuan"), errors="coerce")
    share_nav_size = df["latest_shares"] * df["nav"]
    df["fund_size_yuan"] = df["fund_size_yuan"].where(df["fund_size_yuan"].notna(), share_nav_size)
    close_fallback = df["latest_shares"] * df["close"]
    df["fund_size_yuan"] = df["fund_size_yuan"].where(df["fund_size_yuan"].notna(), close_fallback)
    df["fund_size_basis"] = np.select(
        [
            pd.to_numeric(df.get("official_aum_yuan"), errors="coerce").notna(),
            share_nav_size.notna(),
            close_fallback.notna(),
        ],
        ["official_aum", "shares_times_nav", "shares_times_close"],
        default="missing",
    )
    df["fund_size_100m"] = df["fund_size_yuan"] / 1e8
    df["estimated_net_subscription_100m"] = df["share_daily_change"] * df["nav"] / 1e8
    df["estimated_net_subscription_5d_100m"] = df["share_5d_change"] * df["nav"] / 1e8
    df["premium_discount"] = df["close"] / df["nav"] - 1
    df = apply_corporate_action_adjustments(df)
    creation_unit = pd.to_numeric(df.get("creation_redemption_unit"), errors="coerce").where(lambda value: value.gt(0))
    adjusted_share_change = pd.to_numeric(
        df.get("adjusted_share_change", df.get("share_daily_change")), errors="coerce"
    )
    df["estimated_creation_redemption_units_1d"] = adjusted_share_change / creation_unit
    df["creation_unit_rounding_gap"] = (
        df["estimated_creation_redemption_units_1d"] - df["estimated_creation_redemption_units_1d"].round()
    ).abs()
    df["flow_validation_status"] = np.where(
        df.get("share_flow_excluded", pd.Series(False, index=df.index)).fillna(False),
        "excluded_corporate_action",
        "ok",
    )
    df["price_share_signal"] = np.select(
        [
            (df["pct_change"] > 0) & (df["share_daily_change"] < 0),
            (df["pct_change"] < 0) & (df["share_daily_change"] > 0),
            (df["pct_change"] > 0) & (df["share_daily_change"] > 0),
            (df["pct_change"] < 0) & (df["share_daily_change"] < 0),
        ],
        ["价涨份额降", "价跌份额增", "价涨份额增", "价跌份额降"],
        default="",
    )
    complete = df["close"].notna() & df["latest_shares"].notna() & df["nav"].notna()
    scale_only = df["close"].notna() & df["fund_size_yuan"].notna() & ~df["latest_shares"].notna()
    df["data_quality_status"] = np.select(
        [complete, scale_only],
        ["ok", "scale_only_no_flow"],
        default="partial",
    )
    for source_col in ["quote_source", "share_source", "nav_source", "aum_source"]:
        if source_col not in df.columns:
            df[source_col] = ""
    df["source"] = df[["quote_source", "share_source", "nav_source", "aum_source"]].fillna("").agg(";".join, axis=1)
    df["source_priority"] = 1
    df["is_official"] = True
    df["validation_status"] = df["data_quality_status"]
    return add_flow_speed_columns(df)
