from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd


MARKET_TIMEZONE = timezone(timedelta(hours=8))
PRECLOSE_QUOTE_STATUS = "preclose_quote_unsettled"


def quote_close_timestamp(date_text: str) -> float:
    return datetime.fromisoformat(date_text).replace(hour=15, tzinfo=MARKET_TIMEZONE).timestamp()


def unsettled_quote_mask(quotes: pd.DataFrame) -> pd.Series:
    """Identify bars observed before their trading date's Shanghai close."""
    if quotes.empty or not {"date", "retrieved_at"}.issubset(quotes.columns):
        return pd.Series(False, index=quotes.index)
    retrieved = quotes["retrieved_at"].astype("string")
    has_time = retrieved.str.contains(r"[T ]\d{2}:\d{2}", na=False)
    observed = pd.to_datetime(retrieved.where(has_time), format="mixed", errors="coerce", utc=True)
    # Older collectors wrote naive Shanghai wall time. Normalize explicit offsets
    # to the same wall time before comparing against the 15:00 market close.
    offset = retrieved.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False)
    observed = observed + pd.to_timedelta(offset.astype("int8") * 8, unit="h")
    dates = pd.to_datetime(quotes["date"], format="ISO8601", errors="coerce", utc=True)
    return observed.lt(dates.dt.normalize() + pd.DateOffset(hours=15))


def quote_cache_rank(quotes: pd.DataFrame) -> pd.Series:
    """Prefer usable close, then preserve pre-close evidence over a blank row."""
    unsettled = unsettled_quote_mask(quotes)
    has_close = quotes.get("close", pd.Series(None, index=quotes.index)).notna()
    ranks = pd.Series(2, index=quotes.index, dtype="int8")
    ranks.loc[unsettled] = 1
    ranks.loc[has_close & ~unsettled] = 0
    return ranks
