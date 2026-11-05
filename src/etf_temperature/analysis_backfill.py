from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import logging
import math
from pathlib import Path
from typing import Iterable

import pandas as pd

from ._atomic import atomic_replace
from . import cninfo_nsdk, csrc_eid, fund_company_official, sse_official, szse_official
from .aggregation import build_cluster_daily
from .classification import classify_universe
from .corporate_actions import apply_adjusted_history_features
from .csv_cache import select_timeseries_cache, upsert_timeseries_cache
from .flow_structure import add_flow_speed_columns
from .indicators import build_etf_daily
from .official_endpoints import (
    DATA_DIR,
    NORMALIZED_DIR,
    OUTPUTS_DIR,
    OfficialClient,
    ensure_dirs,
    normalize_date,
    read_dataframe,
    write_dataframe,
)
from .pipeline import (
    _a_share_universe_as_of,
    _apply_flow_audit_status,
    apply_anchor_supplements,
    load_or_rebuild_universe,
)
from .temperature import (
    add_cluster_temperature_history_features,
    add_product_temperature_history_features,
    compute_product_temperature,
)
from .universe import apply_a_share_filter
from .validation import coverage_metrics


LOGGER = logging.getLogger(__name__)
ANALYSIS_DIR = DATA_DIR / "analysis"


def _as_date(value: str) -> date:
    normalized = normalize_date(value)
    if normalized is None:
        raise ValueError(f"invalid date: {value}")
    return datetime.strptime(normalized, "%Y-%m-%d").date()


def _date_minus(value: str, days: int) -> str:
    return (_as_date(value) - timedelta(days=days)).strftime("%Y-%m-%d")


def _weekday_dates(start: str, end: str) -> list[str]:
    current = _as_date(start)
    end_date = _as_date(end)
    rows: list[str] = []
    while current <= end_date:
        if current.weekday() < 5:
            rows.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return rows


def _chunks(values: list[str], size: int) -> list[list[str]]:
    size = max(int(size), 1)
    return [values[index : index + size] for index in range(0, len(values), size)]


def _code_batches(values: Iterable[str], size: int) -> list[list[str]]:
    normalized = sorted({str(value).strip().zfill(6) for value in values if str(value).strip()})
    return _chunks(normalized, size)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    atomic_replace(temp, path)


def _active_universe(universe: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if universe.empty:
        return universe.copy()
    target = normalize_date(target_date) or target_date
    flags = (
        universe["is_a_share_equity_etf"].fillna(False)
        if "is_a_share_equity_etf" in universe
        else pd.Series(False, index=universe.index)
    )
    active = universe[flags].copy()
    listing = active.get("listing_date", pd.Series(None, index=active.index)).map(normalize_date)
    return active.loc[listing.isna() | listing.le(target)].copy()


def _with_inferred_listing_dates(universe: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    """Infer missing SZSE listing dates once from each code's first official quote."""

    out = universe.copy()
    if out.empty or quotes.empty or not {"date", "etf_code"}.issubset(quotes.columns):
        return out
    quote_dates = quotes[["etf_code", "date"]].copy()
    quote_dates["etf_code"] = quote_dates["etf_code"].astype(str).str.zfill(6)
    quote_dates["date"] = quote_dates["date"].map(normalize_date)
    first_quote = quote_dates.dropna(subset=["date"]).groupby("etf_code")["date"].min()
    codes = out["etf_code"].astype(str).str.zfill(6)
    listing = out.get("listing_date", pd.Series(None, index=out.index)).map(normalize_date)
    out["listing_date"] = listing.where(listing.notna(), codes.map(first_quote))
    return out


def discover_trading_calendar(
    quotes: pd.DataFrame,
    universe: pd.DataFrame,
    start_date: str,
    end_date: str,
    *,
    minimum_quote_coverage: float = 0.50,
) -> pd.DataFrame:
    """Infer real trading dates from official quote rows, excluding weekday holidays."""

    if quotes.empty or not {"date", "etf_code"}.issubset(quotes.columns):
        return pd.DataFrame(columns=["date", "expected_etf_count", "quote_count", "quote_coverage"])
    start = normalize_date(start_date) or start_date
    end = normalize_date(end_date) or end_date
    frame = quotes.copy()
    frame["date"] = frame["date"].map(normalize_date)
    frame["etf_code"] = frame["etf_code"].astype(str).str.zfill(6)
    frame = frame[frame["date"].between(start, end)]
    if "close" in frame:
        frame = frame[pd.to_numeric(frame["close"], errors="coerce").notna()]

    effective_universe = _with_inferred_listing_dates(universe, frame)
    rows: list[dict] = []
    for date_text, group in frame.groupby("date", sort=True):
        active = _active_universe(effective_universe, date_text)
        expected_codes = set(active.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6))
        available = set(group["etf_code"].astype(str))
        expected_count = len(expected_codes)
        quote_count = len(available & expected_codes) if expected_codes else len(available)
        coverage = quote_count / expected_count if expected_count else 0.0
        if coverage >= float(minimum_quote_coverage):
            rows.append(
                {
                    "date": date_text,
                    "expected_etf_count": expected_count,
                    "quote_count": quote_count,
                    "quote_coverage": coverage,
                }
            )
    return pd.DataFrame(rows)


def _codes_needing_quote_window(
    codes: list[str],
    listing_dates: dict[str, str | None],
    cache: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> list[str]:
    cache = cache.copy()
    if not cache.empty:
        cache["etf_code"] = cache["etf_code"].astype(str).str.zfill(6)
        cache["date"] = cache["date"].map(normalize_date)
        if "close" in cache:
            cache = cache[pd.to_numeric(cache["close"], errors="coerce").notna()]
    grouped = {
        str(code): group["date"].dropna().astype(str)
        for code, group in cache.groupby("etf_code")
    } if not cache.empty else {}
    end_floor = (_as_date(end_date) - timedelta(days=7)).strftime("%Y-%m-%d")
    missing: list[str] = []
    for code in codes:
        code = str(code).zfill(6)
        expected_start = max(start_date, listing_dates.get(code) or start_date)
        expected_weekdays = len(_weekday_dates(expected_start, end_date))
        minimum_rows = max(1, int(expected_weekdays * 0.55))
        values = grouped.get(code, pd.Series(dtype=str))
        if values.empty:
            missing.append(code)
            continue
        first = values.min()
        last = values.max()
        first_ceiling = (_as_date(expected_start) + timedelta(days=14)).strftime("%Y-%m-%d")
        if first > first_ceiling or last < end_floor or values.nunique() < minimum_rows:
            missing.append(code)
    return missing


def _coverage_for_date(cache: pd.DataFrame, date_text: str, codes: set[str]) -> float:
    if not codes:
        return 1.0
    if cache.empty:
        return 0.0
    available = set(
        cache.loc[cache["date"].eq(date_text), "etf_code"].astype(str).str.zfill(6)
    )
    return len(available & codes) / len(codes)


def _available_codes_for_date(cache: pd.DataFrame, date_text: str) -> set[str]:
    if cache.empty or not {"date", "etf_code"}.issubset(cache.columns):
        return set()
    return set(
        cache.loc[cache["date"].eq(date_text), "etf_code"].astype(str).str.zfill(6)
    )


def _valid_timeseries_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop collector failure markers before they reach the keyed CSV cache."""

    if frame.empty or not {"date", "etf_code"}.issubset(frame.columns):
        return pd.DataFrame()
    valid = frame.copy()
    valid["date"] = valid["date"].map(normalize_date)
    valid["etf_code"] = valid["etf_code"].astype("string").str.strip().str.zfill(6)
    valid = valid[valid["date"].notna() & valid["etf_code"].notna()]
    valid = valid[valid["etf_code"].str.fullmatch(r"\d{6}", na=False)]
    return valid.reset_index(drop=True)


def backfill_market_data(
    start_date: str,
    end_date: str,
    client: OfficialClient | None = None,
    *,
    warmup_days: int = 45,
    quote_batch_size: int = 100,
    date_chunk_size: int = 20,
    skip_szse_shares: bool = False,
    skip_fund_company_aum: bool = False,
) -> dict:
    """Fetch a resumable official ETF market-data window without computing indicators."""

    ensure_dirs()
    client = client or OfficialClient()
    start = normalize_date(start_date) or start_date
    end = normalize_date(end_date) or end_date
    warmup_start = _date_minus(start, warmup_days)
    checkpoint_path = OUTPUTS_DIR / f"market_data_backfill_checkpoint_{start}_{end}.json"

    universe, _ = load_or_rebuild_universe(client)
    universe = apply_a_share_filter(apply_anchor_supplements(universe))
    current = universe[universe["is_a_share_equity_etf"].fillna(False)].copy()
    current["etf_code"] = current["etf_code"].astype(str).str.zfill(6)
    listing_dates = {
        row.etf_code: normalize_date(getattr(row, "listing_date", None))
        for row in current[["etf_code", "listing_date"]].itertuples(index=False)
    } if "listing_date" in current else {}
    sse_codes = current.loc[current["exchange"].eq("SSE"), "etf_code"].tolist()
    szse_codes = current.loc[current["exchange"].eq("SZSE"), "etf_code"].tolist()
    all_codes = current["etf_code"].tolist()

    summary: dict = {
        "start_date": start,
        "end_date": end,
        "warmup_start": warmup_start,
        "universe_count": len(all_codes),
        "quote_batches_completed": 0,
        "quote_batches_attempted": 0,
        "quote_batches_failed": 0,
        "share_chunks_completed": 0,
        "share_chunks_attempted": 0,
        "share_chunks_failed": 0,
        "nav_chunks_completed": 0,
        "nav_chunks_attempted": 0,
        "nav_chunks_failed": 0,
        "nav_gap_passes_completed": 0,
        "nav_gap_rows_added": 0,
        "fund_company_aum_skipped": bool(skip_fund_company_aum),
        "fund_company_aum_rows_fetched": 0,
        "fund_company_implied_share_rows_flow_eligible": 0,
        "warnings": [],
        "status": "running",
    }
    _write_json_atomic(checkpoint_path, summary)

    quote_cache = select_timeseries_cache("quotes", start_date=warmup_start, end_date=end)
    # A high aggregate coverage ratio can hide a newly listed ETF with zero
    # cached rows. Infer missing SZSE listing dates from the first cached quote,
    # then still run the cheap per-code cache check on every resumable run.
    quote_effective_current = _with_inferred_listing_dates(current, quote_cache)
    listing_dates = {
        row.etf_code: normalize_date(getattr(row, "listing_date", None))
        for row in quote_effective_current[["etf_code", "listing_date"]].itertuples(index=False)
    } if "listing_date" in quote_effective_current else listing_dates
    cached_calendar = discover_trading_calendar(quote_cache, universe, warmup_start, end)
    cached_trading_dates = cached_calendar.get("date", pd.Series(dtype=str)).astype(str).tolist()
    quote_window_ready = bool(
        _span_ready(cached_trading_dates, warmup_start, end)
        and not cached_calendar.empty
        and cached_calendar["quote_coverage"].ge(0.98).all()
    )
    summary["quote_cache_window_ready"] = quote_window_ready
    for exchange, codes in (("SSE", sse_codes), ("SZSE", szse_codes)):
        missing = _codes_needing_quote_window(
            codes, listing_dates, quote_cache, warmup_start, end
        )
        for batch in _code_batches(missing, quote_batch_size):
            summary["quote_batches_attempted"] += 1
            if exchange == "SSE":
                points = max(len(_weekday_dates(warmup_start, end)) + 10, 22)
                fetched = sse_official.fetch_quote_history(client, batch, points=points)
            else:
                target_rows = current.loc[
                    current["etf_code"].isin(batch), ["etf_code", "exchange"]
                ].copy()
                fetched = cninfo_nsdk.fetch_quote_history(
                    target_rows,
                    warmup_start,
                    end,
                    per_code_timeout_ms=5000,
                )
                if cninfo_nsdk.LAST_WARNINGS:
                    summary["warnings"].extend(cninfo_nsdk.LAST_WARNINGS[-5:])
            fetched = _valid_timeseries_rows(fetched)
            if not fetched.empty and "date" in fetched:
                fetched = fetched[fetched["date"].map(normalize_date).between(warmup_start, end)]
            if fetched.empty:
                summary["quote_batches_failed"] += 1
                summary["warnings"].append(f"{exchange} quote batch returned no valid rows: {batch[:3]}...")
            else:
                quote_cache = upsert_timeseries_cache("quotes", fetched)
                summary["quote_batches_completed"] += 1
            summary["last_quote_exchange"] = exchange
            summary["last_quote_batch"] = batch
            _write_json_atomic(checkpoint_path, summary)

    quote_cache = select_timeseries_cache("quotes", start_date=warmup_start, end_date=end)
    effective_current = _with_inferred_listing_dates(current, quote_cache)
    calendar = discover_trading_calendar(quote_cache, universe, warmup_start, end)
    trading_dates = calendar["date"].astype(str).tolist() if not calendar.empty else []
    if not trading_dates:
        raise RuntimeError("official quote cache contains no sufficiently covered trading dates")

    share_cache = select_timeseries_cache("shares", start_date=warmup_start, end_date=end)
    if not share_cache.empty:
        share_cache["etf_code"] = share_cache["etf_code"].astype(str).str.zfill(6)
        share_cache["date"] = share_cache["date"].map(normalize_date)
    missing_sse_dates = [
        day for day in trading_dates if _coverage_for_date(share_cache, day, set(_active_universe(effective_current[effective_current["exchange"].eq("SSE")], day)["etf_code"])) < 0.98
    ]
    missing_szse_dates = [
        day for day in trading_dates if _coverage_for_date(share_cache, day, set(_active_universe(effective_current[effective_current["exchange"].eq("SZSE")], day)["etf_code"])) < 0.98
    ]
    for dates in _chunks(missing_sse_dates, date_chunk_size):
        summary["share_chunks_attempted"] += 1
        try:
            fetched = _valid_timeseries_rows(sse_official.fetch_share_history(client, dates))
        except Exception as exc:
            fetched = pd.DataFrame()
            summary["share_chunks_failed"] += 1
            summary["warnings"].append(f"SSE share chunk failed {dates[0]}..{dates[-1]}: {exc}")
        if fetched.empty:
            if not summary["share_chunks_failed"]:
                summary["share_chunks_failed"] += 1
                summary["warnings"].append(f"SSE share chunk returned no valid rows {dates[0]}..{dates[-1]}")
        else:
            share_cache = upsert_timeseries_cache("shares", fetched)
            summary["share_chunks_completed"] += 1
        summary["last_share_exchange"] = "SSE"
        summary["last_share_dates"] = dates
        _write_json_atomic(checkpoint_path, summary)
        if fetched.empty:
            break
    if skip_szse_shares and missing_szse_dates:
        summary["warnings"].append("SZSE share fetch skipped by --skip-szse-shares")
        summary["szse_share_fetch_skipped"] = True
        _write_json_atomic(checkpoint_path, summary)
    # SZSE exposes a complete official XLSX export for a date range. Fetch the
    # whole missing span once; fetch_share_history then splits it into calendar
    # quarters, reducing a YTD repair from thousands of JSON pages to 3 files.
    szse_date_ranges = []
    if missing_szse_dates and not skip_szse_shares:
        szse_date_ranges = [[missing_szse_dates[0], missing_szse_dates[-1]]]
    for dates in szse_date_ranges:
        summary["share_chunks_attempted"] += 1
        try:
            fetched = _valid_timeseries_rows(szse_official.fetch_share_history(client, dates[0], dates[-1]))
        except Exception as exc:
            fetched = pd.DataFrame()
            summary["share_chunks_failed"] += 1
            summary["warnings"].append(f"SZSE share chunk failed {dates[0]}..{dates[-1]}: {exc}")
        if fetched.empty:
            if not summary["share_chunks_failed"]:
                summary["share_chunks_failed"] += 1
                summary["warnings"].append(f"SZSE share chunk returned no valid rows {dates[0]}..{dates[-1]}")
        else:
            share_cache = upsert_timeseries_cache("shares", fetched)
            summary["share_chunks_completed"] += 1
        summary["last_share_exchange"] = "SZSE"
        summary["last_share_dates"] = dates
        _write_json_atomic(checkpoint_path, summary)
        if fetched.empty:
            break

    nav_cache = select_timeseries_cache("nav", start_date=warmup_start, end_date=end)
    if not nav_cache.empty:
        nav_cache["etf_code"] = nav_cache["etf_code"].astype(str).str.zfill(6)
        nav_cache["date"] = nav_cache["date"].map(normalize_date)
    nav_coverage_by_date = {
        day: _coverage_for_date(
            nav_cache,
            day,
            set(_active_universe(effective_current, day)["etf_code"]),
        )
        for day in trading_dates
    }
    summary["nav_dates_below_target"] = sum(
        coverage < 0.95 for coverage in nav_coverage_by_date.values()
    )
    # A mostly populated date is cheaper to repair code-by-code. Full ETF bulk
    # pagination is reserved for genuinely empty or severely incomplete dates.
    nav_bulk_missing_dates = [
        day for day, coverage in nav_coverage_by_date.items() if coverage < 0.80
    ]
    for dates in _chunks(nav_bulk_missing_dates, date_chunk_size):
        summary["nav_chunks_attempted"] += 1
        fetched = _valid_timeseries_rows(
            csrc_eid.fetch_nav_history(
                client,
                all_codes,
                dates[0],
                dates[-1],
                fallback_per_code=False,
                valuation_dates=dates,
                fund_name_filter="ETF",
            )
        )
        if fetched.empty:
            summary["nav_chunks_failed"] += 1
            summary["warnings"].append(f"CSRC NAV chunk returned no valid rows {dates[0]}..{dates[-1]}")
        else:
            nav_cache = upsert_timeseries_cache("nav", fetched)
            summary["nav_chunks_completed"] += 1
        summary["last_nav_dates"] = dates
        _write_json_atomic(checkpoint_path, summary)
        if fetched.empty:
            break

    nav_cache = select_timeseries_cache("nav", start_date=warmup_start, end_date=end)
    if not nav_cache.empty:
        nav_cache["etf_code"] = nav_cache["etf_code"].astype(str).str.zfill(6)
        nav_cache["date"] = nav_cache["date"].map(normalize_date)
    attempted_nav_gaps: set[tuple[str, str]] = set()
    for _ in range(2):
        gaps_by_date: dict[str, list[str]] = {}
        for day in trading_dates:
            active_codes = set(
                _active_universe(effective_current, day)["etf_code"].astype(str).str.zfill(6)
            )
            available = set(
                nav_cache.loc[nav_cache["date"].eq(day), "etf_code"].astype(str).str.zfill(6)
            ) if not nav_cache.empty else set()
            required = math.ceil(len(active_codes) * 0.95)
            shortfall = max(required - len(active_codes & available), 0)
            if shortfall <= 0:
                continue
            candidates = sorted(
                code for code in (active_codes - available) if (day, code) not in attempted_nav_gaps
            )
            selected = candidates[: shortfall + 5]
            if selected:
                gaps_by_date[day] = selected
                attempted_nav_gaps.update((day, code) for code in selected)
        if not gaps_by_date:
            break
        targeted = _valid_timeseries_rows(csrc_eid.fetch_nav_gaps(client, gaps_by_date))
        if targeted.empty:
            if csrc_eid.LAST_WARNINGS:
                summary["warnings"].extend(csrc_eid.LAST_WARNINGS[-5:])
            break
        nav_cache = upsert_timeseries_cache("nav", targeted)
        nav_cache = select_timeseries_cache("nav", start_date=warmup_start, end_date=end)
        nav_cache["etf_code"] = nav_cache["etf_code"].astype(str).str.zfill(6)
        nav_cache["date"] = nav_cache["date"].map(normalize_date)
        summary["nav_gap_passes_completed"] += 1
        summary["nav_gap_rows_added"] += int(len(targeted))
        _write_json_atomic(checkpoint_path, summary)

    if not skip_fund_company_aum:
        try:
            fund_company_summary = fund_company_official.backfill_official_aum(
                client,
                effective_current,
                warmup_start,
                end,
                artifact_suffix=_analysis_suffix(start, end),
            )
            summary["fund_company_aum"] = fund_company_summary
            summary["fund_company_aum_rows_fetched"] = int(
                fund_company_summary.get("aum_rows_fetched", 0)
            )
            summary["fund_company_implied_share_rows_flow_eligible"] = int(
                fund_company_summary.get("implied_share_rows_flow_eligible", 0)
            )
        except Exception as exc:
            summary["warnings"].append(f"fund-company AUM fallback failed: {exc}")
        _write_json_atomic(checkpoint_path, summary)

    quote_cache = select_timeseries_cache("quotes", start_date=warmup_start, end_date=end)
    share_cache = select_timeseries_cache("shares", start_date=warmup_start, end_date=end)
    nav_cache = select_timeseries_cache("nav", start_date=warmup_start, end_date=end)
    aum_cache = select_timeseries_cache("aum", start_date=warmup_start, end_date=end)
    write_dataframe(select_timeseries_cache("quotes", end_date=end), NORMALIZED_DIR / "etf_daily_quotes_history.parquet")
    write_dataframe(select_timeseries_cache("shares", end_date=end), NORMALIZED_DIR / "etf_share.parquet")
    write_dataframe(select_timeseries_cache("nav", end_date=end), NORMALIZED_DIR / "etf_nav.parquet")
    write_dataframe(select_timeseries_cache("aum", end_date=end), NORMALIZED_DIR / "etf_aum.parquet")

    final_calendar = discover_trading_calendar(quote_cache, universe, start, end)
    coverage_rows: list[dict] = []
    for day in final_calendar.get("date", pd.Series(dtype=str)).astype(str):
        active = _active_universe(effective_current, day)
        active_codes = set(active["etf_code"].astype(str).str.zfill(6))
        quote_coverage = _coverage_for_date(quote_cache, day, active_codes)
        share_coverage = _coverage_for_date(share_cache, day, active_codes)
        nav_coverage = _coverage_for_date(nav_cache, day, active_codes)
        aum_coverage = _coverage_for_date(aum_cache, day, active_codes)
        share_codes = _available_codes_for_date(share_cache, day)
        nav_codes = _available_codes_for_date(nav_cache, day)
        aum_codes = _available_codes_for_date(aum_cache, day)
        scale_codes = (aum_codes | (share_codes & nav_codes)) & active_codes
        scale_coverage = len(scale_codes) / len(active_codes) if active_codes else 1.0
        coverage_rows.append(
            {
                "date": day,
                "expected_etf_count": len(active_codes),
                "quote_coverage": quote_coverage,
                "share_coverage": share_coverage,
                "nav_coverage": nav_coverage,
                "aum_coverage": aum_coverage,
                "scale_coverage": scale_coverage,
                "scale_data_ready": quote_coverage >= 0.98 and scale_coverage >= 0.95,
                "market_data_ready": quote_coverage >= 0.98 and share_coverage >= 0.98 and nav_coverage >= 0.95,
            }
        )
    coverage = pd.DataFrame(coverage_rows)
    suffix = _analysis_suffix(start, end)
    coverage_path = NORMALIZED_DIR / f"market_data_coverage_{suffix}.parquet"
    calendar_path = NORMALIZED_DIR / f"trading_calendar_{suffix}.parquet"
    summary_path = OUTPUTS_DIR / f"market_data_backfill_{suffix}.json"
    write_dataframe(coverage, coverage_path)
    write_dataframe(final_calendar, calendar_path)

    requested_dates = final_calendar.get("date", pd.Series(dtype=str)).astype(str).tolist()
    span_ready = _span_ready(requested_dates, start, end)
    ready_dates = int(coverage["market_data_ready"].sum()) if not coverage.empty else 0
    scale_ready_dates = int(coverage["scale_data_ready"].sum()) if not coverage.empty else 0
    market_data_ready = bool(span_ready and ready_dates == len(requested_dates))
    scale_data_ready = bool(span_ready and scale_ready_dates == len(requested_dates))
    blocked_dates = (
        coverage.loc[
            ~coverage["market_data_ready"],
            ["date", "quote_coverage", "share_coverage", "nav_coverage", "aum_coverage", "scale_coverage"],
        ].to_dict("records")
        if not coverage.empty
        else []
    )

    summary.update(
        {
            "status": "completed",
            "market_data_ready": market_data_ready,
            "scale_data_ready": scale_data_ready,
            "calendar_span_ready": span_ready,
            "trading_date_count": len(requested_dates),
            "ready_date_count": ready_dates,
            "scale_ready_date_count": scale_ready_dates,
            "first_trading_date": requested_dates[0] if requested_dates else None,
            "last_trading_date": requested_dates[-1] if requested_dates else None,
            "quote_cache_rows": int(len(quote_cache)),
            "share_cache_rows": int(len(share_cache)),
            "nav_cache_rows": int(len(nav_cache)),
            "aum_cache_rows": int(len(aum_cache)),
            "blocked_dates": blocked_dates,
            "coverage_path": str(coverage_path),
            "calendar_path": str(calendar_path),
            "summary_path": str(summary_path),
            "checkpoint_path": str(checkpoint_path),
            "point_in_time_delisting_supported": False,
        }
    )
    _write_json_atomic(checkpoint_path, summary)
    _write_json_atomic(summary_path, summary)
    return summary


def _analysis_suffix(start: str, end: str) -> str:
    start_date = _as_date(start)
    end_date = _as_date(end)
    if start_date.month == 1 and start_date.day == 1 and start_date.year == end_date.year:
        return f"{start_date.year}_ytd"
    return f"{start}_{end}"


def _span_ready(trading_dates: list[str], start: str, end: str) -> bool:
    if not trading_dates:
        return False
    first_limit = (_as_date(start) + timedelta(days=14)).strftime("%Y-%m-%d")
    last_limit = (_as_date(end) - timedelta(days=7)).strftime("%Y-%m-%d")
    # Mainland exchange holidays account for a modest share of weekdays. Requiring
    # 80% prevents a recent-only or heavily gapped cache from masquerading as a
    # complete analysis window while still allowing the official holiday calendar.
    minimum_dates = max(1, int(len(_weekday_dates(start, end)) * 0.80))
    return trading_dates[0] <= first_limit and trading_dates[-1] >= last_limit and len(trading_dates) >= minimum_dates


def build_analysis_from_cache(
    start_date: str,
    end_date: str,
    *,
    warmup_days: int = 45,
    context_trading_days: int = 65,
) -> dict:
    """Build analysis-ready ETF and cluster histories without reports or charts."""

    ensure_dirs()
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    start = normalize_date(start_date) or start_date
    end = normalize_date(end_date) or end_date
    warmup_start = _date_minus(start, warmup_days)
    universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
    if universe.empty:
        raise RuntimeError("ETF universe is missing; run analysis backfill with official fetching first")
    universe = apply_a_share_filter(apply_anchor_supplements(universe))
    membership = classify_universe(universe)
    quotes = select_timeseries_cache("quotes", start_date=warmup_start, end_date=end)
    shares = select_timeseries_cache("shares", start_date=warmup_start, end_date=end)
    nav = select_timeseries_cache("nav", start_date=warmup_start, end_date=end)
    aum = select_timeseries_cache("aum", start_date=warmup_start, end_date=end)
    calendar = discover_trading_calendar(quotes, universe, warmup_start, end)
    all_dates = calendar["date"].astype(str).tolist() if not calendar.empty else []
    requested_dates = [day for day in all_dates if start <= day <= end]
    if not requested_dates:
        raise RuntimeError("no covered trading dates are available in the requested analysis window")

    etf_frames: list[pd.DataFrame] = []
    cluster_frames: list[pd.DataFrame] = []
    validation_rows: list[dict] = []
    for index, day in enumerate(all_dates, start=1):
        active = _a_share_universe_as_of(universe, day, quotes)
        active_codes = set(active["etf_code"].astype(str).str.zfill(6))
        active_membership = membership[membership["etf_code"].astype(str).str.zfill(6).isin(active_codes)].copy()
        etf_context = pd.concat(etf_frames[-context_trading_days:], ignore_index=True, sort=False) if etf_frames else pd.DataFrame()
        cluster_context = pd.concat(cluster_frames[-context_trading_days:], ignore_index=True, sort=False) if cluster_frames else pd.DataFrame()

        etf_daily = build_etf_daily(active, active_membership, quotes, shares, nav, day, aum=aum)
        etf_daily = apply_adjusted_history_features(etf_daily, etf_context)
        etf_daily, flow_audit = _apply_flow_audit_status(etf_daily)
        etf_daily = add_flow_speed_columns(etf_daily)
        etf_daily = compute_product_temperature(etf_daily)
        etf_daily = add_product_temperature_history_features(etf_daily, etf_context)
        etf_frames.append(etf_daily)

        cluster_daily = build_cluster_daily(etf_daily)
        cluster_daily["date"] = day
        cluster_daily = add_cluster_temperature_history_features(cluster_daily, cluster_context)
        cluster_frames.append(cluster_daily)

        if day >= start:
            metrics = coverage_metrics(
                active,
                etf_daily,
                active_membership,
                cluster_daily=cluster_daily,
                flow_audit=flow_audit,
            )
            validation_rows.append(
                {
                    "date": day,
                    "expected_etf_count": metrics.get("a_share_equity_etf_count", 0),
                    "quote_coverage": metrics.get("quote_coverage", 0.0),
                    "share_coverage": metrics.get("share_coverage", 0.0),
                    "nav_coverage": metrics.get("nav_coverage", 0.0),
                    "flow_size_coverage": metrics.get("flow_size_coverage", 0.0),
                    "important_unclassified_count": metrics.get("important_unclassified_count", 0),
                    "extreme_flow_unresolved_count": metrics.get("extreme_flow_unresolved_count", 0),
                    "formal_report_allowed": bool(metrics.get("formal_report_allowed", False)),
                    "formal_blockers": metrics.get("formal_blockers", []),
                }
            )
        if index == len(all_dates) or index % 10 == 0:
            LOGGER.info("analysis build progress %s/%s date=%s", index, len(all_dates), day)

    etf_history = pd.concat(etf_frames, ignore_index=True, sort=False)
    cluster_history = pd.concat(cluster_frames, ignore_index=True, sort=False)
    etf_history = etf_history[etf_history["date"].astype(str).between(start, end)].copy()
    cluster_history = cluster_history[cluster_history["date"].astype(str).between(start, end)].copy()
    validation = pd.DataFrame(validation_rows)
    requested_calendar = calendar[calendar["date"].astype(str).between(start, end)].copy()

    suffix = _analysis_suffix(start, end)
    etf_path = ANALYSIS_DIR / f"etf_daily_{suffix}.parquet"
    cluster_path = ANALYSIS_DIR / f"cluster_daily_{suffix}.parquet"
    calendar_path = ANALYSIS_DIR / f"trading_calendar_{suffix}.parquet"
    validation_path = ANALYSIS_DIR / f"validation_{suffix}.parquet"
    summary_path = OUTPUTS_DIR / f"analysis_backfill_{suffix}.json"
    write_dataframe(etf_history, etf_path)
    write_dataframe(cluster_history, cluster_path)
    write_dataframe(requested_calendar, calendar_path)
    write_dataframe(validation, validation_path)

    formal_dates = validation.loc[validation["formal_report_allowed"], "date"].astype(str).tolist() if not validation.empty else []
    blocked_dates = validation.loc[~validation["formal_report_allowed"], ["date", "formal_blockers"]].to_dict("records") if not validation.empty else []
    span_ready = _span_ready(requested_dates, start, end)
    analysis_ready = bool(span_ready and len(formal_dates) == len(requested_dates))
    summary = {
        "start_date": start,
        "end_date": end,
        "warmup_start": warmup_start,
        "analysis_ready": analysis_ready,
        "calendar_span_ready": span_ready,
        "trading_date_count": len(requested_dates),
        "formal_date_count": len(formal_dates),
        "formal_date_ratio": len(formal_dates) / len(requested_dates) if requested_dates else 0.0,
        "first_trading_date": requested_dates[0],
        "last_trading_date": requested_dates[-1],
        "etf_rows": int(len(etf_history)),
        "cluster_rows": int(len(cluster_history)),
        "blocked_dates": blocked_dates,
        "universe_scope": "current official ETF universe filtered by listing_date",
        "point_in_time_delisting_supported": False,
        "etf_path": str(etf_path),
        "cluster_path": str(cluster_path),
        "calendar_path": str(calendar_path),
        "validation_path": str(validation_path),
        "summary_path": str(summary_path),
    }
    _write_json_atomic(summary_path, summary)
    return summary


def backfill_analysis(
    start_date: str,
    end_date: str,
    *,
    cache_only: bool = False,
    warmup_days: int = 45,
    quote_batch_size: int = 100,
    date_chunk_size: int = 20,
) -> dict:
    prefetch = {
        "status": "skipped",
        "skip_reason": "cache_only requested",
    }
    if not cache_only:
        prefetch = backfill_market_data(
            start_date,
            end_date,
            warmup_days=warmup_days,
            quote_batch_size=quote_batch_size,
            date_chunk_size=date_chunk_size,
        )
    result = build_analysis_from_cache(start_date, end_date, warmup_days=warmup_days)
    result["prefetch"] = prefetch
    _write_json_atomic(Path(result["summary_path"]), result)
    return result
