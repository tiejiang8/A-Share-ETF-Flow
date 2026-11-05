from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from ._atomic import atomic_replace
from .official_endpoints import DATA_DIR, normalize_date


CACHE_DIR = DATA_DIR / "cache"
CACHE_FILES = {
    "quotes": CACHE_DIR / "etf_quotes.csv",
    "shares": CACHE_DIR / "etf_shares.csv",
    "nav": CACHE_DIR / "etf_nav.csv",
    "aum": CACHE_DIR / "etf_aum.csv",
}


# These rows may be useful as source-discovery evidence, but their economic
# date is not proven.  They must never enter the flow calculation cache.
_NON_FLOW_SHARE_STATUSES = {
    "anchor_supplement_current_share_only",
    "current_share_only_no_history",
    "derived_same_date_overlap_pending",
    "derived_same_date_overlap_failed",
    "aum_nav_date_mismatch",
}


def _path(kind: str) -> Path:
    try:
        return CACHE_FILES[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported cache kind: {kind}") from exc


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    if "etf_code" in out:
        out["etf_code"] = out["etf_code"].astype(str).str.extract(r"(\d{1,6})", expand=False).fillna("").str.zfill(6)
        out = out[out["etf_code"].str.fullmatch(r"\d{6}", na=False)]
    if "date" in out:
        out["date"] = out["date"].map(lambda value: normalize_date(value) if pd.notna(value) else None)
        out = out[out["date"].notna()]
    return out


def _eligible_for_cache(kind: str, frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or kind != "shares" or "validation_status" not in frame:
        return frame
    status = frame["validation_status"].fillna("").astype(str)
    eligible = ~status.isin(_NON_FLOW_SHARE_STATUSES)
    if "derivation_method" in frame and "overlap_relation" in frame:
        derived = frame["derivation_method"].fillna("").astype(str).eq("aum_div_nav_same_date")
        same_date_proven = frame["overlap_relation"].fillna("").astype(str).eq("same_date")
        eligible &= ~derived | same_date_proven
    return frame.loc[eligible].copy()


def read_timeseries_cache(kind: str) -> pd.DataFrame:
    path = _path(kind)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"etf_code": "string", "date": "string"}, low_memory=False)
    return _eligible_for_cache(kind, _normalize_frame(frame))


def compact_timeseries_cache(kind: str) -> pd.DataFrame:
    """Rewrite a cache after removing rows that are not economically usable.

    This is primarily used for the shares cache after tightening validation
    rules.  An empty result keeps the original headers so later reads remain
    well-defined.
    """

    path = _path(kind)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    raw = pd.read_csv(path, dtype={"etf_code": "string", "date": "string"}, low_memory=False)
    eligible = _eligible_for_cache(kind, _normalize_frame(raw))
    output = eligible if not eligible.empty else raw.head(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    output.to_csv(tmp, index=False, encoding="utf-8-sig")
    atomic_replace(tmp, path)
    return eligible.reset_index(drop=True)


def upsert_timeseries_cache(
    kind: str,
    fresh: pd.DataFrame,
    *,
    key_cols: Iterable[str] = ("date", "etf_code"),
) -> pd.DataFrame:
    """Upsert a source cache keyed by date + ETF code.

    Lower source_priority wins. For equal priority, the newest retrieved_at wins.
    The CSV is written atomically so an interrupted rebuild cannot corrupt it.
    """

    fresh = _eligible_for_cache(kind, _normalize_frame(fresh))
    existing = read_timeseries_cache(kind)
    if existing.empty and fresh.empty:
        return pd.DataFrame()
    if fresh.empty:
        return existing

    fresh = fresh.copy()
    fresh["_cache_fresh"] = 1
    if not existing.empty:
        existing = existing.copy()
        existing["_cache_fresh"] = 0
        merged = pd.concat([existing, fresh], ignore_index=True, sort=False)
    else:
        merged = fresh

    keys = list(key_cols)
    missing_keys = [col for col in keys if col not in merged]
    if missing_keys:
        raise ValueError(f"cache {kind} missing key columns: {missing_keys}")

    merged["source_priority"] = pd.to_numeric(merged.get("source_priority", 999), errors="coerce").fillna(999)
    retrieved = pd.to_datetime(merged.get("retrieved_at"), errors="coerce")
    merged["_retrieved_sort"] = retrieved.fillna(pd.Timestamp("1970-01-01"))

    merged = (
        merged.sort_values(
            keys + ["source_priority", "_cache_fresh", "_retrieved_sort"],
            ascending=[True] * len(keys) + [True, False, False],
        )
        .drop_duplicates(keys, keep="first")
        .drop(columns=["_cache_fresh", "_retrieved_sort"], errors="ignore")
        .sort_values(keys)
        .reset_index(drop=True)
    )

    path = _path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    merged.to_csv(tmp, index=False, encoding="utf-8-sig")
    atomic_replace(tmp, path)
    return merged


def select_timeseries_cache(
    kind: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    frame = read_timeseries_cache(kind)
    if frame.empty:
        return frame
    out = frame
    if start_date:
        start = normalize_date(start_date)
        out = out[out["date"].ge(start)]
    if end_date:
        end = normalize_date(end_date)
        out = out[out["date"].le(end)]
    if codes is not None:
        normalized_codes = {str(code).zfill(6) for code in codes}
        out = out[out["etf_code"].isin(normalized_codes)]
    return out.copy()


def bootstrap_cache(kind: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Seed the CSV cache from an existing normalized parquet dataset."""

    if frame is None or frame.empty:
        return read_timeseries_cache(kind)
    return upsert_timeseries_cache(kind, frame)


def date_code_coverage(
    kind: str,
    *,
    date_text: str,
    expected_codes: Iterable[str],
) -> float:
    expected = {str(code).zfill(6) for code in expected_codes}
    if not expected:
        return 1.0
    rows = select_timeseries_cache(kind, start_date=date_text, end_date=date_text, codes=expected)
    if rows.empty:
        return 0.0
    available = set(rows["etf_code"].astype(str))
    return len(available & expected) / len(expected)


def unique_dates(kind: str, *, end_date: str | None = None) -> list[str]:
    frame = select_timeseries_cache(kind, end_date=end_date)
    if frame.empty:
        return []
    return sorted(frame["date"].dropna().astype(str).unique().tolist())
