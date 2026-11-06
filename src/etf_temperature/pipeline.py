from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from time import perf_counter

import pandas as pd

from . import cninfo_nsdk, csrc_eid, fund_company_official, sse_official, szse_official
from .aggregation import build_abnormal_pool, build_cluster_daily
from .audit import audit_extreme_flows, write_audit_outputs
from .anchors import (
    apply_anchor_supplements,
    anchor_required_codes,
    ensure_anchor_reference_data,
    validate_core_anchors,
    write_core_anchor_audit,
)
from .browser_probe import probe_official_pages
from .charts import generate_charts
from .classification import classify_universe, membership_review_markdown, validate_membership
from .config import load_temperature_config
from .corporate_actions import flag_share_jumps
from .csv_cache import (
    bootstrap_cache,
    date_code_coverage,
    select_timeseries_cache,
    unique_dates,
    upsert_timeseries_cache,
)
from .history import upsert_history_file
from .indicators import build_etf_daily
from .corporate_actions import apply_adjusted_history_features
from .flow_structure import add_flow_speed_columns
from .official_endpoints import (
    DOCS_DIR,
    NORMALIZED_DIR,
    OUTPUTS_DIR,
    RAW_DIR,
    OfficialClient,
    ensure_dirs,
    last_calendar_days,
    normalize_date,
    output_date_dir,
    read_dataframe,
    write_dataframe,
    write_probe_outputs,
)
from .quote_freshness import quote_cache_rank, unsettled_quote_mask
from .report import generate_report
from .representative import choose_representatives
from .snapshot import SnapshotStore, build_daily_snapshot, write_snapshot_json
from .summary import write_summary_artifacts
from .temperature import (
    add_cluster_temperature_history_features,
    add_product_temperature_history_features,
    add_temperature_state_columns,
    compute_product_temperature,
)
from .universe import apply_a_share_filter, build_universe, universe_summary
from .validation import coverage_metrics, validation_markdown


LOGGER = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    report_date: str
    report_path: Path | None
    chart_dir: Path | None
    metrics: dict
    summary: dict
    representatives: pd.DataFrame
    cluster_daily: pd.DataFrame
    unresolved_issues: list[str]
    used_cache: bool = False
    cache_reason: str = ""
    summary_paths: dict = field(default_factory=dict)


def _date_minus(report_date: str, days: int) -> str:
    dt = datetime.strptime(normalize_date(report_date) or "", "%Y-%m-%d").date()
    return (dt - timedelta(days=days)).strftime("%Y-%m-%d")


def _safe_normalize_date(value) -> str | None:
    if pd.isna(value):
        return None
    try:
        return normalize_date(value)
    except Exception:
        return None


def _normalize_frame_date_column(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "date" not in frame:
        return frame.copy()
    out = frame.copy()
    out["date"] = out["date"].map(_safe_normalize_date)
    return out


def _history_dates() -> list[str]:
    history = _normalize_frame_date_column(read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet"))
    if history.empty or "date" not in history:
        return []
    return sorted(date_text for date_text in history["date"].dropna().astype(str).unique() if date_text)


def _source_dates(path: Path, report_date: str | None = None) -> list[str]:
    frame = _normalize_frame_date_column(read_dataframe(path))
    if frame.empty or "date" not in frame:
        return []
    dates = frame["date"].dropna().astype(str)
    if report_date:
        dates = dates[dates.le(report_date)]
    return sorted(date_text for date_text in dates.unique() if date_text)


def _has_rows_for_date(path: Path, report_date: str) -> bool:
    frame = _normalize_frame_date_column(read_dataframe(path))
    if frame.empty or "date" not in frame:
        return False
    return bool(frame["date"].eq(report_date).any())


def _local_snapshot_available(report_date: str) -> bool:
    universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    if universe.empty or membership.empty:
        return False
    return _has_rows_for_date(NORMALIZED_DIR / "etf_daily_history.parquet", report_date) and _has_rows_for_date(
        NORMALIZED_DIR / "cluster_daily_history.parquet",
        report_date,
    )


def should_refresh_report_date(
    report_date: str,
    *,
    force: bool = False,
    recent_days: int = 2,
) -> tuple[bool, str]:
    target = normalize_date(report_date) or report_date
    if force:
        return True, f"已指定 --refresh，将刷新最近 {max(int(recent_days), 1)} 个交易日的官方数据。"
    if not _local_snapshot_available(target):
        return True, f"本地缓存缺少 {target} 的完整日报快照，需要刷新官方数据。"
    quotes = read_dataframe(NORMALIZED_DIR / "etf_daily_quotes_history.parquet")
    current = quotes.loc[quotes["date"].eq(target)] if "date" in quotes else quotes.head(0)
    unsettled = current.loc[unsettled_quote_mask(current)]
    if not unsettled.empty:
        universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
        active = _a_share_universe_as_of(universe, target, quotes)
        if not active.empty and unsettled["etf_code"].astype(str).isin(active["etf_code"].astype(str)).any():
            return True, f"{target} 存在收盘前采集的行情，需要复采后重算日报。"
    return False, f"本地已有 {target} 完整快照；默认跳过官方采集，仅重渲染报告。"


def _reuse_formal_daily_report(report_date: str) -> PipelineResult | None:
    output_dir = OUTPUTS_DIR / report_date
    report_path = output_dir / f"A股ETF资金温度日报_{report_date}.md"
    appendix_path = output_dir / f"A股ETF资金温度日报_技术附录_{report_date}.md"
    chart_dir = output_dir / "charts"
    snapshot_path = output_dir / "daily_snapshot.json"
    render_outputs = (report_path, appendix_path, chart_dir / "index.html")
    required = (*render_outputs, output_dir / "validation.md", output_dir / "daily_etf.parquet",
                output_dir / "daily_cluster.parquet", output_dir / "summary.json", snapshot_path)
    if not all(path.is_file() for path in required):
        return None
    try:
        # The snapshot is saved before rendering. An interrupted rerun may
        # leave the previous run's render outputs at the same paths.
        snapshot_time = snapshot_path.stat().st_mtime_ns
        if any(path.stat().st_mtime_ns < snapshot_time for path in render_outputs):
            return None
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(snapshot, dict) or snapshot.get("date") != report_date:
        return None
    if (
        not isinstance(summary, dict)
        or summary.get("asof") != report_date
        or summary.get("formal_report_allowed") is not True
        or summary.get("formal_blockers")
    ):
        return None
    metrics = snapshot.get("validation")
    if not isinstance(metrics, dict) or metrics.get("formal_report_allowed") is not True or metrics.get("formal_blockers"):
        return None
    return PipelineResult(
        report_date=report_date,
        report_path=report_path,
        chart_dir=chart_dir,
        metrics=metrics,
        summary=snapshot.get("market", {}),
        representatives=pd.DataFrame(),
        cluster_daily=pd.DataFrame(snapshot.get("cluster_daily", [])),
        unresolved_issues=[],
        used_cache=True,
        cache_reason=f"{report_date} 的正式日报已生成，直接复用。",
    )


def _cached_rows_for_date(path: Path, report_date: str, label: str) -> pd.DataFrame:
    frame = _normalize_frame_date_column(read_dataframe(path))
    if frame.empty or "date" not in frame:
        raise RuntimeError(f"本地缓存缺少 {label}: {path}")
    rows = frame[frame["date"].eq(report_date)].copy()
    if rows.empty:
        raise RuntimeError(f"本地缓存缺少 {report_date} 的 {label}。请使用 --refresh 重新采集。")
    return rows


def _cached_rows_until_date(path: Path, report_date: str) -> pd.DataFrame:
    frame = _normalize_frame_date_column(read_dataframe(path))
    if frame.empty or "date" not in frame:
        return pd.DataFrame()
    return frame[frame["date"].le(report_date)].copy()


def _source_cache_ready(report_date: str) -> bool:
    share_dates = _source_dates(NORMALIZED_DIR / "etf_share.parquet", report_date)
    quote_dates = _source_dates(NORMALIZED_DIR / "etf_daily_quotes_history.parquet", report_date)
    nav = read_dataframe(NORMALIZED_DIR / "etf_nav.parquet")
    return len(share_dates) >= 5 and len(quote_dates) >= 5 and not nav.empty


def _recent_refresh_dates(report_date: str, recent_days: int) -> list[str]:
    target = normalize_date(report_date) or report_date
    dates = [date_text for date_text in _history_dates() if date_text <= target]
    dates.append(target)
    unique = sorted(set(dates))
    window = max(int(recent_days), 1)
    return unique[-window:]


def _merge_cached_timeseries(
    fresh: pd.DataFrame,
    cache_path: Path,
    report_date: str,
    key_cols: list[str],
    *,
    history_days: int = 90,
    quote_data: bool = False,
) -> pd.DataFrame:
    cached = _cached_rows_until_date(cache_path, report_date)
    if not cached.empty and "date" in cached:
        start = _date_minus(report_date, history_days)
        cached = cached[cached["date"].ge(start)].copy()
    frames = []
    if not cached.empty:
        cached = cached.copy()
        cached["_fresh_order"] = 0
        frames.append(cached)
    if fresh is not None and not fresh.empty:
        fresh = _normalize_frame_date_column(fresh)
        fresh = fresh[fresh["date"].le(report_date)].copy() if "date" in fresh else fresh.copy()
        fresh["_fresh_order"] = 1
        frames.append(fresh)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True, sort=False)
    if not all(col in merged.columns for col in key_cols):
        return merged.drop(columns=["_fresh_order"], errors="ignore")
    if "source_priority" not in merged:
        merged["source_priority"] = 999
    sort_cols = key_cols + ["source_priority", "_fresh_order"]
    ascending = [True] * len(key_cols) + [True, False]
    if quote_data:
        merged["_quote_rank"] = quote_cache_rank(merged)
        sort_cols.insert(len(key_cols), "_quote_rank")
        ascending.insert(len(key_cols), True)
    merged = (
        merged.sort_values(sort_cols, ascending=ascending)
        .drop_duplicates(key_cols, keep="first")
        .drop(columns=["_fresh_order", "_quote_rank"], errors="ignore")
    )
    return merged



def _weekday_dates(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(normalize_date(start_date) or "", "%Y-%m-%d").date()
    end = datetime.strptime(normalize_date(end_date) or "", "%Y-%m-%d").date()
    dates: list[str] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def _cache_window_ready(
    kind: str,
    *,
    target_dates: list[str],
    expected_codes: list[str],
    end_date: str,
    min_target_coverage: float,
    min_history_dates: int,
) -> bool:
    return bool(
        _cache_window_status(
            kind,
            target_dates=target_dates,
            expected_codes=expected_codes,
            end_date=end_date,
            min_target_coverage=min_target_coverage,
            min_history_dates=min_history_dates,
        )["ready"]
    )


def _cache_window_status(
    kind: str,
    *,
    target_dates: list[str],
    expected_codes: list[str],
    end_date: str,
    min_target_coverage: float,
    min_history_dates: int,
) -> dict:
    expected = {str(code).zfill(6) for code in expected_codes if str(code).strip()}
    targets = [normalize_date(date_text) or date_text for date_text in target_dates if date_text]
    status = {
        "ready": False,
        "reason": "",
        "target_dates": targets,
        "target_coverage": [],
        "history_date_count": 0,
        "min_history_dates": int(min_history_dates),
        "min_target_coverage": float(min_target_coverage),
        "expected_code_count": len(expected),
    }
    if not targets:
        status["reason"] = "no target dates"
        return status
    if not expected:
        status["reason"] = "no expected codes"
        return status

    target_ok = True
    for date_text in targets:
        rows = select_timeseries_cache(kind, start_date=date_text, end_date=date_text, codes=expected)
        available = set(rows.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6)) if not rows.empty else set()
        coverage = len(available & expected) / len(expected)
        target_coverage = {
            "date": date_text,
            "coverage": float(coverage),
            "rows": int(len(rows)),
            "missing_codes": int(max(len(expected) - len(available & expected), 0)),
        }
        status["target_coverage"].append(target_coverage)
        if coverage < min_target_coverage:
            target_ok = False

    history_dates = unique_dates(kind, end_date=end_date)
    status["history_date_count"] = len(history_dates)
    history_ok = len(history_dates) >= min_history_dates
    status["ready"] = bool(target_ok and history_ok)
    if not target_ok:
        status["reason"] = "target date coverage below threshold"
    elif not history_ok:
        status["reason"] = "history date count below threshold"
    else:
        status["reason"] = "cache window ready"
    return status


def _format_cache_status(status: dict) -> str:
    coverage = ", ".join(
        f"{item['date']}={item['coverage']:.2%}({item['rows']} rows, missing {item['missing_codes']})"
        for item in status.get("target_coverage", [])
    )
    return (
        f"ready={status.get('ready')} reason={status.get('reason')} "
        f"targets=[{coverage}] history_dates={status.get('history_date_count')}/"
        f"{status.get('min_history_dates')} min_target={status.get('min_target_coverage'):.0%}"
    )


def prefetch_rebuild_market_data(
    start_date: str,
    end_date: str,
    client: OfficialClient | None = None,
) -> dict:
    """Fetch one shared market-data window and cache rows by date + ETF code."""

    ensure_dirs()
    client = client or OfficialClient()
    start = normalize_date(start_date) or start_date
    end = normalize_date(end_date) or end_date
    warmup_start = _date_minus(start, 25)
    target_dates = _weekday_dates(start, end)

    universe, _ = load_or_rebuild_universe(client)
    a_share = universe[universe["is_a_share_equity_etf"]].copy()
    sse_codes = a_share.loc[a_share["exchange"].eq("SSE"), "etf_code"].astype(str).tolist()
    szse_codes = a_share.loc[a_share["exchange"].eq("SZSE"), "etf_code"].astype(str).tolist()
    all_codes = a_share["etf_code"].astype(str).tolist()

    bootstrap_cache("quotes", read_dataframe(NORMALIZED_DIR / "etf_daily_quotes_history.parquet"))
    bootstrap_cache("shares", read_dataframe(NORMALIZED_DIR / "etf_share.parquet"))
    bootstrap_cache("nav", read_dataframe(NORMALIZED_DIR / "etf_nav.parquet"))

    quote_ready = _cache_window_ready(
        "quotes",
        target_dates=target_dates,
        expected_codes=all_codes,
        end_date=end,
        min_target_coverage=0.98,
        min_history_dates=12,
    )
    share_ready = _cache_window_ready(
        "shares",
        target_dates=target_dates,
        expected_codes=all_codes,
        end_date=end,
        min_target_coverage=0.95,
        min_history_dates=6,
    )
    nav_ready = _cache_window_ready(
        "nav",
        target_dates=target_dates,
        expected_codes=all_codes,
        end_date=end,
        min_target_coverage=0.95,
        min_history_dates=max(len(target_dates), 1),
    )

    fetched_quotes = pd.DataFrame()
    fetched_shares = pd.DataFrame()
    fetched_nav = pd.DataFrame()

    if not quote_ready:
        points = max(len(_weekday_dates(warmup_start, end)) + 5, 22)
        sse_quotes = sse_official.fetch_quote_history(client, sse_codes, points=points)
        try:
            szse_quotes = szse_official.fetch_quote_history(client, warmup_start, end, codes=szse_codes)
        except Exception as exc:
            szse_official.LAST_WARNINGS.append(f"SZSE 批量行情预取失败: {exc}")
            szse_quotes = pd.DataFrame()
        if not szse_quotes.empty:
            szse_quotes = szse_quotes[szse_quotes["etf_code"].isin(set(szse_codes))]
        fetched_quotes = pd.concat([sse_quotes, szse_quotes], ignore_index=True, sort=False)
        quote_cache = upsert_timeseries_cache("quotes", fetched_quotes)
        merged_quotes = _merge_cached_timeseries(
            quote_cache,
            NORMALIZED_DIR / "etf_daily_quotes_history.parquet",
            end,
            ["etf_code", "date"],
            history_days=3650,
        )
        write_dataframe(merged_quotes, NORMALIZED_DIR / "etf_daily_quotes_history.parquet")

    if not share_ready:
        sse_shares = sse_official.fetch_share_history(client, _weekday_dates(warmup_start, end))
        try:
            szse_shares = szse_official.fetch_share_history(client, warmup_start, end)
        except Exception as exc:
            szse_official.LAST_WARNINGS.append(f"SZSE 批量份额预取失败: {exc}")
            szse_shares = pd.DataFrame()
        fetched_shares = pd.concat([sse_shares, szse_shares], ignore_index=True, sort=False)
        share_cache = upsert_timeseries_cache("shares", fetched_shares)
        merged_shares = _merge_cached_timeseries(
            share_cache,
            NORMALIZED_DIR / "etf_share.parquet",
            end,
            ["etf_code", "date"],
            history_days=3650,
        )
        write_dataframe(merged_shares, NORMALIZED_DIR / "etf_share.parquet")

    if not nav_ready:
        fetched_nav = csrc_eid.fetch_nav_history(client, all_codes, start, end)
        nav_cache = upsert_timeseries_cache("nav", fetched_nav)
        write_dataframe(nav_cache, NORMALIZED_DIR / "etf_nav.parquet")

    return {
        "warmup_start": warmup_start,
        "start_date": start,
        "end_date": end,
        "target_dates": target_dates,
        "quote_cache_hit": quote_ready,
        "share_cache_hit": share_ready,
        "nav_cache_hit": nav_ready,
        "fetched_quote_rows": int(len(fetched_quotes)),
        "fetched_share_rows": int(len(fetched_shares)),
        "fetched_nav_rows": int(len(fetched_nav)),
        "quote_cache_rows": int(len(select_timeseries_cache("quotes", end_date=end))),
        "share_cache_rows": int(len(select_timeseries_cache("shares", end_date=end))),
        "nav_cache_rows": int(len(select_timeseries_cache("nav", end_date=end))),
    }

def backfill_nav_history(
    start_date: str,
    end_date: str,
    client: OfficialClient | None = None,
) -> dict:
    """Fetch and persist official historical NAV for the local A-share ETF universe."""

    ensure_dirs()
    client = client or OfficialClient()
    start = normalize_date(start_date) or start_date
    end = normalize_date(end_date) or end_date
    dates = _weekday_dates(start, end)

    universe, _ = load_or_rebuild_universe(client)
    a_share = universe[universe["is_a_share_equity_etf"]].copy()
    codes = a_share["etf_code"].astype(str).tolist()

    bootstrap_cache("nav", read_dataframe(NORMALIZED_DIR / "etf_nav.parquet"))
    before = _cache_window_status(
        "nav",
        target_dates=dates,
        expected_codes=codes,
        end_date=end,
        min_target_coverage=0.95,
        min_history_dates=max(len(dates), 1),
    )

    fetched = csrc_eid.fetch_nav_history(client, codes, start, end)
    cache = upsert_timeseries_cache("nav", fetched)
    write_dataframe(cache, NORMALIZED_DIR / "etf_nav.parquet")

    after = _cache_window_status(
        "nav",
        target_dates=dates,
        expected_codes=codes,
        end_date=end,
        min_target_coverage=0.95,
        min_history_dates=max(len(dates), 1),
    )
    result = {
        "start_date": start,
        "end_date": end,
        "target_dates": dates,
        "expected_etf_count": len(codes),
        "fetched_nav_rows": int(len(fetched)),
        "cache_rows": int(len(cache)),
        "before": before,
        "after": after,
        "warnings": csrc_eid.LAST_WARNINGS[:50],
    }
    (OUTPUTS_DIR / f"nav_backfill_{start}_{end}.json").write_text(
        pd.Series(result).to_json(force_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def probe_official() -> dict:
    ensure_dirs()
    result = probe_official_pages()
    return result


def rebuild_universe(client: OfficialClient | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_dirs()
    client = client or OfficialClient()
    sse_df = sse_official.fetch_stock_etf_universe(client)
    szse_df, szse_nav = szse_official.fetch_etf_list(client)
    universe = build_universe(sse_df, szse_df)
    membership = classify_universe(universe)
    write_dataframe(universe, NORMALIZED_DIR / "etf_universe.parquet")
    write_dataframe(membership, NORMALIZED_DIR / "etf_cluster_membership.parquet")
    summary = universe_summary(universe, membership)
    lines = ["# ETF总池摘要", ""]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    if szse_official.LAST_WARNINGS:
        lines.append("")
        lines.append("## 深交所分页警告")
        for warning in szse_official.LAST_WARNINGS:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("## 数据源")
    lines.append("- SSE: FUND_LIST fundType=00")
    lines.append("- SZSE: ShowReport CATALOGID=fund_etf/1945 官方XLSX整表")
    (OUTPUTS_DIR / "etf_universe_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUTPUTS_DIR / "cluster_membership_review.md").write_text(membership_review_markdown(membership), encoding="utf-8")
    if not szse_nav.empty:
        write_dataframe(szse_nav, NORMALIZED_DIR / "szse_nav_latest.parquet")
    return universe, membership


def validate_clusters() -> dict:
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    result = validate_membership(membership)
    (OUTPUTS_DIR / "cluster_membership_review.md").write_text(membership_review_markdown(membership), encoding="utf-8")
    return result


def discover_report_date(client: OfficialClient) -> str:
    sse_date = sse_official.latest_share_date(client)
    today = datetime.now().strftime("%Y-%m-%d")
    szse_start = _date_minus(today, 20)
    try:
        szse_shares = szse_official.fetch_share_history(client, szse_start, today)
        szse_date = sorted(szse_shares["date"].dropna().unique())[-1] if not szse_shares.empty else None
    except Exception as exc:
        szse_official.LAST_WARNINGS.append(f"SZSE 自动日期探测失败: {exc}")
        szse_date = None
    dates = [d for d in [sse_date, szse_date] if d]
    if not dates:
        raise RuntimeError("无法从官方份额接口识别最新统计日期。")
    return min(dates)


def load_or_rebuild_universe(client: OfficialClient) -> tuple[pd.DataFrame, pd.DataFrame]:
    universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    if universe.empty or membership.empty:
        return rebuild_universe(client)
    universe = apply_anchor_supplements(universe)
    universe = apply_a_share_filter(universe)
    membership = classify_universe(universe)
    write_dataframe(universe, NORMALIZED_DIR / "etf_universe.parquet")
    write_dataframe(membership, NORMALIZED_DIR / "etf_cluster_membership.parquet")
    (OUTPUTS_DIR / "cluster_membership_review.md").write_text(membership_review_markdown(membership), encoding="utf-8")
    return universe, membership


def _target_quote_codes(quotes: pd.DataFrame, report_date: str) -> set[str]:
    if quotes.empty or "date" not in quotes or "close" not in quotes:
        return set()
    target = normalize_date(report_date)
    current = quotes[quotes["date"].map(normalize_date).eq(target) & quotes["close"].notna()]
    return set(current.loc[~unsettled_quote_mask(current), "etf_code"].astype(str))


def _a_share_universe_as_of(
    universe: pd.DataFrame,
    report_date: str,
    quote_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return only A-share ETFs that can exist on the requested date."""

    if universe.empty:
        return universe.copy()
    target = normalize_date(report_date) or report_date
    active = universe[universe["is_a_share_equity_etf"]].copy()
    listing_date = active.get("listing_date", pd.Series(None, index=active.index)).map(normalize_date)
    active = active.loc[listing_date.isna() | listing_date.le(target)].copy()

    quotes = quote_history if quote_history is not None else read_dataframe(
        NORMALIZED_DIR / "etf_daily_quotes_history.parquet"
    )
    if quotes is not None and not quotes.empty and {"etf_code", "date"}.issubset(quotes.columns):
        first_quote = (
            quotes.assign(
                etf_code=quotes["etf_code"].astype(str).str.zfill(6),
                _date=quotes["date"].map(normalize_date),
            )
            .dropna(subset=["_date"])
            .groupby("etf_code")["_date"]
            .min()
        )
        codes = active["etf_code"].astype(str).str.zfill(6)
        inferred_first_quote = codes.map(first_quote)
        active = active.loc[inferred_first_quote.isna() | inferred_first_quote.le(target)].copy()
    return active.reset_index(drop=True)


def _dedupe_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return quotes
    return (
        quotes.assign(_quote_rank=quote_cache_rank(quotes))
        .sort_values(["etf_code", "date", "_quote_rank", "source_priority"])
        .drop_duplicates(["etf_code", "date"], keep="first")
        .drop(columns="_quote_rank")
    )


def _quote_refresh_gaps(
    quotes: pd.DataFrame, expected_by_date: dict[str, list[str]], report_date: str,
) -> tuple[list[str], list[str]]:
    settled_keys: set[tuple[str, str]] = set()
    unsettled_keys: set[tuple[str, str]] = set()
    if not quotes.empty and {"etf_code", "date"}.issubset(quotes.columns):
        key_columns = quotes[["etf_code", "date"]].assign(
            etf_code=quotes["etf_code"].astype(str).str.zfill(6),
        )
        ranks = quote_cache_rank(quotes)
        settled_keys = set(key_columns.loc[ranks.eq(0)].itertuples(index=False, name=None))
        unsettled_keys = set(key_columns.loc[ranks.eq(1)].itertuples(index=False, name=None))
    missing_codes: set[str] = set()
    date_status = []
    for day, expected in expected_by_date.items():
        missing = [code for code in expected if (code, day) not in settled_keys]
        coverage = 1.0 if not expected else 1 - len(missing) / len(expected)
        date_status.append(f"{day}={coverage:.2%}({len(expected) - len(missing)}/{len(expected)})")
        missing_codes.update(
            code for code in missing
            if day == report_date or coverage < 0.98 or (code, day) in unsettled_keys
        )
    return sorted(missing_codes), date_status


def _apply_flow_audit_status(etf_daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if etf_daily.empty:
        return etf_daily, pd.DataFrame()
    audit = audit_extreme_flows(etf_daily)
    out = etf_daily.copy()
    if not audit.empty and {"etf_code", "flow_validation_status"}.issubset(audit.columns):
        status_by_code = audit.drop_duplicates("etf_code", keep="last").set_index("etf_code")["flow_validation_status"]
        codes = out["etf_code"].astype(str)
        audited = codes.map(status_by_code)
        out["flow_validation_status"] = audited.where(audited.notna(), out.get("flow_validation_status", "ok"))

    target = out.get("date", pd.Series(None, index=out.index)).map(normalize_date)
    quote_date = out.get("quote_data_date", pd.Series(None, index=out.index)).map(normalize_date)
    share_date = out.get("share_data_date", pd.Series(None, index=out.index)).map(normalize_date)
    nav_date = out.get("nav_data_date", pd.Series(None, index=out.index)).map(normalize_date)
    nav_lag = (pd.to_datetime(target, errors="coerce") - pd.to_datetime(nav_date, errors="coerce")).dt.days
    max_nav_lag = int(load_temperature_config()["thresholds"].get("daily_max_nav_lag_days", 0))
    # 恒等式：当日份额变动为 0 时净申购 = Δ份额 × 净值 = 0，净值取哪一天都不改变读数。
    # 仅放宽到「净值晚一个交易日发布」；净值长期不更新（已终止/休眠标的）不属发布滞后，不放宽。
    if "share_daily_change" in out:
        share_change = pd.to_numeric(out["share_daily_change"], errors="coerce")
    else:
        share_change = pd.to_numeric(pd.Series(pd.NA, index=out.index), errors="coerce")
    zero_share_change = (
        share_change.notna()
        & share_change.eq(0)
        & share_date.eq(target)
        & nav_lag.ge(0)
        & nav_lag.le(max_nav_lag + 1)
    )
    share_nav_fresh = (
        out.get("latest_shares", pd.Series(pd.NA, index=out.index)).notna()
        & out.get("nav", pd.Series(pd.NA, index=out.index)).notna()
        & share_date.eq(target)
        & nav_lag.ge(0)
        & (nav_lag.le(max_nav_lag) | zero_share_change)
    )
    quote_fresh = (
        out.get("close", pd.Series(pd.NA, index=out.index)).notna()
        & quote_date.eq(target)
    )
    status = out.get("flow_validation_status", pd.Series("ok", index=out.index)).fillna("ok")
    input_fresh = (
        (quote_fresh & share_nav_fresh)
        | (status.eq("resolved_official_suspension") & share_nav_fresh)
        # 恒等式救援：份额零变动时净申购只由份额与净值决定，当日无成交行情也可保留。
        | (status.eq("resolved_zero_share_change") & share_nav_fresh)
    )
    stale = ~input_fresh
    out.loc[stale & status.eq("ok"), "flow_validation_status"] = "stale_source_excluded"
    excluded = stale | out["flow_validation_status"].eq("unresolved")
    for column in ["estimated_net_subscription_100m", "estimated_net_subscription_5d_100m"]:
        if column in out:
            raw_column = f"raw_{column}"
            if raw_column not in out:
                out[raw_column] = out[column]
            out.loc[excluded, column] = pd.NA
    return out, audit


def _write_dated_daily_outputs(
    output_dir: Path,
    report_date: str,
    etf_daily: pd.DataFrame,
    cluster_daily: pd.DataFrame,
    metrics: dict,
) -> dict:
    write_dataframe(etf_daily, output_dir / "daily_etf.parquet")
    write_dataframe(cluster_daily, output_dir / "daily_cluster.parquet")
    snapshot = build_daily_snapshot(report_date, etf_daily, cluster_daily, metrics)
    write_snapshot_json(snapshot, output_dir / "daily_snapshot.json")
    # AI-readable summary artifacts (summary.json / llms.txt / snapshot.md).
    # Derived from the same official-data outputs as the human report, so the
    # two can never disagree about the numbers.
    return write_summary_artifacts(output_dir, report_date, etf_daily, cluster_daily, metrics)


def _refresh_membership_fields(etf_daily: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    """Apply current classification semantics to cached daily rows without refetching data."""
    if etf_daily.empty or membership.empty or "etf_code" not in etf_daily:
        return etf_daily
    out = etf_daily.copy()
    current = membership.copy()
    current["etf_code"] = current["etf_code"].astype(str).str.zfill(6)
    current = current.drop_duplicates("etf_code", keep="last").set_index("etf_code")
    codes = out["etf_code"].astype(str).str.zfill(6)
    for column in [
        "primary_cluster",
        "primary_cluster_name",
        "secondary_tags",
        "classification_basis",
        "confidence",
        "is_in_31_clusters",
    ]:
        if column in current:
            mapped = codes.map(current[column])
            out[column] = mapped.where(mapped.notna(), out[column] if column in out else pd.NA)
    return out


def render_report_from_cache(report_date: str, *, cache_reason: str = "") -> PipelineResult:
    LOGGER.info("cache render start date=%s reason=%s", report_date, cache_reason)
    ensure_dirs()
    report_date = normalize_date(report_date) or report_date
    output_dir = output_date_dir(report_date)

    universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    if universe.empty or membership.empty:
        raise RuntimeError("本地缓存缺少 ETF 总池或集群归类，请使用 --refresh 重新采集。")
    universe = apply_anchor_supplements(universe)
    universe = apply_a_share_filter(universe)
    membership = classify_universe(universe)
    write_dataframe(universe, NORMALIZED_DIR / "etf_universe.parquet")
    write_dataframe(membership, NORMALIZED_DIR / "etf_cluster_membership.parquet")
    (OUTPUTS_DIR / "cluster_membership_review.md").write_text(membership_review_markdown(membership), encoding="utf-8")

    etf_daily = _cached_rows_for_date(NORMALIZED_DIR / "etf_daily_history.parquet", report_date, "ETF 日频历史")
    etf_daily = _refresh_membership_fields(etf_daily, membership)
    etf_daily = add_flow_speed_columns(etf_daily)
    etf_daily = add_temperature_state_columns(etf_daily)
    cluster_history = read_dataframe(NORMALIZED_DIR / "cluster_daily_history.parquet")
    cluster_daily = build_cluster_daily(etf_daily)
    cluster_daily["date"] = report_date
    cluster_daily = add_cluster_temperature_history_features(cluster_daily, cluster_history)
    write_dataframe(etf_daily, NORMALIZED_DIR / "etf_daily.parquet")
    upsert_history_file(NORMALIZED_DIR / "etf_daily_history.parquet", etf_daily, ["date", "etf_code"])
    write_dataframe(cluster_daily, NORMALIZED_DIR / "cluster_daily.parquet")
    upsert_history_file(NORMALIZED_DIR / "cluster_daily_history.parquet", cluster_daily, ["date", "cluster_id"])
    cluster_temperature_cols = [
        "date",
        "cluster_id",
        "cluster_name",
        "cluster_temperature",
        "cluster_temperature_confidence",
        "temperature_change",
        "cluster_temperature_change_1d",
        "cluster_temperature_change_3d",
        "cluster_temperature_slope_5d",
        "temperature_level",
        "temperature_direction",
        "temperature_phase",
        "temperature_stage",
    ]
    cluster_temperature_cols = [col for col in cluster_temperature_cols if col in cluster_daily.columns]
    upsert_history_file(
        NORMALIZED_DIR / "cluster_temperature_history.parquet",
        cluster_daily[cluster_temperature_cols],
        ["date", "cluster_id"],
    )

    temperature_cols = [
        "etf_code",
        "date",
        "absolute_temperature",
        "market_percentile_temperature",
        "cluster_percentile_temperature",
        "final_temperature",
        "temperature_confidence",
        "trusted_temperature",
        "temperature",
        "temperature_change",
        "temperature_change_1d",
        "temperature_change_3d",
        "temperature_slope_5d",
        "hot_days_5d",
        "flow_positive_days_5d",
        "continuous_warming",
        "cooling_from_high",
        "temperature_level",
        "temperature_direction",
        "temperature_phase",
        "temperature_stage",
    ]
    temperature_cols = [col for col in temperature_cols if col in etf_daily.columns]
    if temperature_cols:
        write_dataframe(etf_daily[temperature_cols], NORMALIZED_DIR / "etf_temperature.parquet")

    representatives = choose_representatives(etf_daily)
    write_dataframe(representatives, NORMALIZED_DIR / "cluster_representatives.parquet")
    abnormal_pool = build_abnormal_pool(etf_daily)
    write_dataframe(abnormal_pool, NORMALIZED_DIR / "abnormal_pool.parquet")
    corporate_flags = flag_share_jumps(etf_daily)
    write_dataframe(corporate_flags, NORMALIZED_DIR / "corporate_action_flags.parquet")

    snapshot_membership = SnapshotStore._slice(etf_daily, report_date)
    membership_columns = [column for column in membership.columns if column in snapshot_membership.columns]
    if "etf_code" in membership_columns and "primary_cluster" in membership_columns:
        snapshot_membership = snapshot_membership[membership_columns].drop_duplicates("etf_code", keep="last")
    else:
        snapshot_membership = membership
    snapshot_universe = etf_daily.copy()
    snapshot_universe["is_a_share_equity_etf"] = True
    summary = universe_summary(snapshot_universe, snapshot_membership)
    anchor_audit, anchor_metrics = validate_core_anchors(snapshot_universe, snapshot_membership, etf_daily)
    write_core_anchor_audit(anchor_audit)
    flow_audit = audit_extreme_flows(etf_daily, cluster_daily)
    metrics = coverage_metrics(
        snapshot_universe,
        etf_daily,
        snapshot_membership,
        cluster_daily=cluster_daily,
        flow_audit=flow_audit,
    )
    metrics.update(anchor_metrics)
    history_snapshot = _cached_rows_until_date(NORMALIZED_DIR / "etf_daily_history.parquet", report_date)
    if not history_snapshot.empty and "date" in history_snapshot:
        metrics["history_trading_days"] = int(pd.to_datetime(history_snapshot["date"], errors="coerce").dropna().nunique())
    else:
        metrics["history_trading_days"] = 0
    metrics["render_mode"] = "cache"
    metrics["official_refresh_performed"] = False
    metrics["cache_reason"] = cache_reason
    metrics["source_refresh_mode"] = "cache"
    membership_validation = validate_membership(snapshot_membership)
    (output_dir / "validation.md").write_text(validation_markdown(metrics, membership_validation), encoding="utf-8")
    summary_paths = _write_dated_daily_outputs(output_dir, report_date, etf_daily, cluster_daily, metrics)

    quotes = _cached_rows_until_date(NORMALIZED_DIR / "etf_daily_quotes_history.parquet", report_date)
    chart_dir = output_dir / "charts"
    chart_paths = generate_charts(quotes, etf_daily, representatives, abnormal_pool, chart_dir)

    report_path = output_dir / f"A股ETF资金温度日报_{report_date}.md"
    generate_report(
        report_date,
        report_path,
        metrics,
        summary,
        cluster_daily,
        etf_daily,
        representatives,
        abnormal_pool,
        chart_paths,
    )
    write_audit_outputs(report_date)
    LOGGER.info("cache render finished date=%s report=%s", report_date, report_path)

    unresolved = []
    if not metrics["formal_report_allowed"]:
        unresolved.append("部分覆盖率或观察集群门槛未达正式报告阈值，报告已显式降级为预览/故障口径。")
    if corporate_flags is not None and not corporate_flags.empty:
        unresolved.append("存在份额单日变化超过20%的ETF，需后续核查公司行为公告。")
    if membership_validation["missing_clusters"]:
        unresolved.append(f"缺少成员集群: {membership_validation['missing_clusters']}")

    return PipelineResult(
        report_date=report_date,
        report_path=report_path,
        chart_dir=chart_dir,
        metrics=metrics,
        summary=summary,
        representatives=representatives,
        cluster_daily=cluster_daily,
        unresolved_issues=unresolved,
        used_cache=True,
        cache_reason=cache_reason,
        summary_paths=summary_paths,
    )


def run_pipeline(
    report_date: str = "auto",
    client: OfficialClient | None = None,
    *,
    refresh: bool = False,
    refresh_recent_days: int = 2,
    full_refresh: bool = False,
    market_data_cache_only: bool = False,
    reuse_formal: bool = False,
) -> PipelineResult:
    run_started = perf_counter()
    ensure_dirs()
    LOGGER.info(
        "daily run start date=%s refresh=%s refresh_recent_days=%s full_refresh=%s market_data_cache_only=%s",
        report_date,
        refresh,
        refresh_recent_days,
        full_refresh,
        market_data_cache_only,
    )
    if str(report_date).lower() == "auto":
        client = client or OfficialClient()
        LOGGER.info("daily discover report date")
        report_date = discover_report_date(client)
    report_date = normalize_date(report_date) or report_date
    should_refresh, cache_reason = should_refresh_report_date(
        report_date, force=refresh, recent_days=refresh_recent_days,
    )
    if not should_refresh and reuse_formal:
        existing = _reuse_formal_daily_report(report_date)
        if existing is not None:
            LOGGER.info("daily formal report reused date=%s elapsed_seconds=%.2f", report_date, perf_counter() - run_started)
            return existing
        should_refresh = True
        cache_reason = "已有快照尚未通过正式交付校验，需要复跑。"
    if not should_refresh:
        LOGGER.info("daily cache hit date=%s reason=%s", report_date, cache_reason)
        result = render_report_from_cache(report_date, cache_reason=cache_reason)
        LOGGER.info("daily cache render finished date=%s elapsed_seconds=%.2f", report_date, perf_counter() - run_started)
        return result

    LOGGER.info("daily refresh date=%s reason=%s", report_date, cache_reason)
    LOGGER.info("daily official path probing sources")
    write_probe_outputs(update_reference=False)
    client = client or OfficialClient()
    LOGGER.info("daily load or rebuild universe")
    universe, membership = load_or_rebuild_universe(client)
    LOGGER.info("daily ensure anchor reference data date=%s fetch_missing=%s", report_date, not market_data_cache_only)
    ensure_anchor_reference_data(report_date, client, fetch_missing=not market_data_cache_only)
    output_dir = output_date_dir(report_date)
    incremental_source_refresh = (not full_refresh) and _source_cache_ready(report_date)
    if market_data_cache_only:
        start_date = _date_minus(report_date, 90)
        date_candidates = last_calendar_days(report_date, 100)
        sse_quote_points = 0
        source_refresh_mode = "csv_date_code_cache"
    elif incremental_source_refresh:
        date_candidates = _recent_refresh_dates(report_date, refresh_recent_days)
        start_date = date_candidates[0]
        sse_quote_points = max(len(date_candidates), 2)
        source_refresh_mode = "incremental_recent_days"
    else:
        start_date = _date_minus(report_date, 18)
        date_candidates = last_calendar_days(report_date, 22)
        sse_quote_points = 22
        source_refresh_mode = "full_window"
    LOGGER.info(
        "daily source mode=%s start_date=%s target_date=%s date_candidates=%s",
        source_refresh_mode,
        start_date,
        report_date,
        len(date_candidates),
    )

    a_share = _a_share_universe_as_of(universe, report_date)
    active_codes = set(a_share["etf_code"].astype(str))
    active_membership = membership[membership["etf_code"].astype(str).isin(active_codes)].copy()
    sse_codes = a_share.loc[a_share["exchange"].eq("SSE"), "etf_code"].astype(str).tolist()
    szse_codes = set(a_share.loc[a_share["exchange"].eq("SZSE"), "etf_code"].astype(str).tolist())
    all_codes = a_share["etf_code"].astype(str).tolist()

    stage_started = perf_counter()
    LOGGER.info("daily setup ready elapsed_seconds=%.2f", stage_started - run_started)
    if not market_data_cache_only:
        bootstrap_cache("shares", read_dataframe(NORMALIZED_DIR / "etf_share.parquet"))
    share_status_dates = [report_date] if market_data_cache_only else date_candidates
    share_cache_status = _cache_window_status(
        "shares",
        target_dates=share_status_dates,
        expected_codes=all_codes,
        end_date=report_date,
        min_target_coverage=0.90,
        min_history_dates=5,
    )
    LOGGER.info("daily shares cache check %s", _format_cache_status(share_cache_status))
    if market_data_cache_only:
        LOGGER.info("daily shares load from csv cache date=%s", report_date)
        fresh_shares = select_timeseries_cache(
            "shares",
            start_date=start_date,
            end_date=report_date,
            codes=all_codes,
        )
    elif incremental_source_refresh and share_cache_status["ready"]:
        LOGGER.info("daily shares cache window ready; skip official shares fetch")
        fresh_shares = select_timeseries_cache(
            "shares",
            start_date=start_date,
            end_date=report_date,
            codes=all_codes,
        )
    else:
        LOGGER.info("daily shares fetch official date=%s cache_status=%s", report_date, _format_cache_status(share_cache_status))
        LOGGER.info("daily shares fetch SSE dates=%s", len(date_candidates))
        sse_shares = sse_official.fetch_share_history(client, date_candidates)
        try:
            LOGGER.info("daily shares fetch SZSE window=%s..%s", start_date, report_date)
            szse_shares = szse_official.fetch_share_history(client, start_date, report_date)
        except Exception as exc:
            szse_official.LAST_WARNINGS.append(f"SZSE 规模接口整体失败: {exc}")
            szse_shares = pd.DataFrame()
        if szse_shares.empty and "latest_share_10k_from_list" in universe.columns:
            szse_official.LAST_WARNINGS.append(
                "SZSE 规模专页不可用；ETF列表当前规模仅作为无日期重叠校验证据，不写入每日份额。"
            )
        fresh_shares = pd.concat([sse_shares, szse_shares], ignore_index=True, sort=False)
        upsert_timeseries_cache("shares", fresh_shares)
    shares = _merge_cached_timeseries(
        fresh_shares,
        NORMALIZED_DIR / "etf_share.parquet",
        report_date,
        ["etf_code", "date"],
    )
    shares = _normalize_frame_date_column(shares)
    shares = shares[shares["etf_code"].isin(set(a_share["etf_code"].astype(str)))]
    shares = shares[shares["date"].le(report_date)]
    upsert_timeseries_cache("shares", shares)
    write_dataframe(shares, NORMALIZED_DIR / "etf_share.parquet")
    LOGGER.info("daily shares ready rows=%s elapsed_seconds=%.2f", len(shares), perf_counter() - stage_started)

    stage_started = perf_counter()
    if not market_data_cache_only:
        bootstrap_cache("quotes", read_dataframe(NORMALIZED_DIR / "etf_daily_quotes_history.parquet"))
    if market_data_cache_only:
        LOGGER.info("daily quotes load from csv cache date=%s", report_date)
        fresh_quotes = select_timeseries_cache(
            "quotes",
            start_date=start_date,
            end_date=report_date,
            codes=all_codes,
        )
    elif incremental_source_refresh:
        cached_quote_window = select_timeseries_cache(
            "quotes",
            start_date=start_date,
            end_date=report_date,
            codes=all_codes,
        )
        listing_by_code = {
            str(row.etf_code).zfill(6): normalize_date(getattr(row, "listing_date", None))
            for row in a_share[["etf_code", "listing_date"]].itertuples(index=False)
        } if "listing_date" in a_share.columns else {}
        expected_by_date = {
            day: [
                code for code in all_codes
                if not listing_by_code.get(code) or listing_by_code[code] <= day
            ]
            for day in sorted(set(date_candidates))
        }
        missing_quote_codes, quote_cache_date_status = _quote_refresh_gaps(
            cached_quote_window, expected_by_date, report_date,
        )
        LOGGER.info(
            "daily quotes incremental missing_codes=%s cached_rows=%s date_status=%s",
            len(missing_quote_codes),
            len(cached_quote_window),
            ", ".join(quote_cache_date_status),
        )
        if missing_quote_codes:
            missing_sse_codes = [code for code in missing_quote_codes if code in set(sse_codes)]
            missing_szse_codes = set(missing_quote_codes) & set(szse_codes)
            raw_szse_quotes = szse_official.load_quote_history_from_raw_cache(
                RAW_DIR,
                start_date,
                report_date,
                missing_szse_codes,
            )
            if not raw_szse_quotes.empty:
                LOGGER.info("daily quotes recovered SZSE raw cache rows=%s", len(raw_szse_quotes))
                cached_quote_window = pd.concat([cached_quote_window, raw_szse_quotes], ignore_index=True, sort=False)
                upsert_timeseries_cache("quotes", raw_szse_quotes)
                missing_quote_codes, quote_cache_date_status = _quote_refresh_gaps(
                    cached_quote_window, expected_by_date, report_date,
                )
                missing_sse_codes = [code for code in missing_quote_codes if code in set(sse_codes)]
                missing_szse_codes = set(missing_quote_codes) & set(szse_codes)
                LOGGER.info(
                    "daily quotes missing after raw recovery=%s szse=%s date_status=%s",
                    len(missing_quote_codes),
                    len(missing_szse_codes),
                    ", ".join(quote_cache_date_status),
                )
            if missing_sse_codes:
                LOGGER.info("daily quotes fetch SSE missing codes=%s points=%s", len(missing_sse_codes), sse_quote_points)
                sse_quotes = sse_official.fetch_quote_history(
                    client, missing_sse_codes, points=sse_quote_points, settled_for=report_date,
                )
            else:
                LOGGER.info("daily quotes no missing SSE codes; skip SSE official quote fetch")
                sse_quotes = pd.DataFrame()
            if not missing_szse_codes:
                LOGGER.info("daily quotes no missing SZSE codes; skip SZSE official quote fetch")
                szse_quotes = pd.DataFrame()
            elif len(missing_szse_codes) > 100:
                LOGGER.info(
                    "daily quotes fetch CNINFO fallback for large SZSE gap codes=%s window=%s..%s",
                    len(missing_szse_codes),
                    start_date,
                    report_date,
                )
                szse_missing_rows = a_share.loc[
                    a_share["etf_code"].astype(str).isin(missing_szse_codes),
                    ["etf_code", "exchange"],
                ].copy()
                szse_quotes = cninfo_nsdk.fetch_quote_history(
                    szse_missing_rows,
                    start_date,
                    report_date,
                    per_code_timeout_ms=3000,
                )
            else:
                try:
                    LOGGER.info("daily quotes fetch SZSE missing codes=%s window=%s..%s", len(missing_szse_codes), start_date, report_date)
                    szse_quotes = szse_official.fetch_quote_history(
                        client, start_date, report_date, codes=missing_szse_codes, settled_for=report_date,
                    )
                except Exception as exc:
                    szse_official.LAST_WARNINGS.append(f"SZSE 行情接口整体失败: {exc}")
                    szse_quotes = pd.DataFrame()
            if not szse_quotes.empty:
                szse_quotes = szse_quotes[szse_quotes["etf_code"].isin(missing_szse_codes)]
            fresh_quotes = pd.concat([cached_quote_window, sse_quotes, szse_quotes], ignore_index=True, sort=False)
        else:
            LOGGER.info("daily quotes cache window ready; skip official quotes fetch")
            fresh_quotes = cached_quote_window
    else:
        LOGGER.info("daily quotes fetch SSE codes=%s points=%s", len(sse_codes), sse_quote_points)
        sse_quotes = sse_official.fetch_quote_history(
            client, sse_codes, points=sse_quote_points, settled_for=report_date,
        )
        try:
            LOGGER.info("daily quotes fetch SZSE codes=%s window=%s..%s", len(szse_codes), start_date, report_date)
            szse_quotes = szse_official.fetch_quote_history(
                client, start_date, report_date, codes=szse_codes, settled_for=report_date,
            )
        except Exception as exc:
            szse_official.LAST_WARNINGS.append(f"SZSE 行情接口整体失败: {exc}")
            szse_quotes = pd.DataFrame()
        if not szse_quotes.empty:
            szse_quotes = szse_quotes[szse_quotes["etf_code"].isin(szse_codes)]
        fresh_quotes = pd.concat([sse_quotes, szse_quotes], ignore_index=True, sort=False)
    quotes = _merge_cached_timeseries(
        fresh_quotes,
        NORMALIZED_DIR / "etf_daily_quotes_history.parquet",
        report_date,
        ["etf_code", "date"],
        quote_data=True,
    )
    available_quote_codes = _target_quote_codes(quotes, report_date)
    missing_quote_rows = a_share.loc[
        ~a_share["etf_code"].astype(str).isin(available_quote_codes),
        ["etf_code", "exchange"],
    ].copy()
    if not missing_quote_rows.empty:
        cached_quotes = read_dataframe(NORMALIZED_DIR / "etf_daily_quotes_history.parquet")
        if not cached_quotes.empty and "source" in cached_quotes:
            cached_quotes = cached_quotes[
                cached_quotes["source"].astype(str).str.contains("cninfo_nsdk_official", case=False, na=False)
                & cached_quotes["date"].le(report_date)
                & cached_quotes["etf_code"].astype(str).isin(set(missing_quote_rows["etf_code"].astype(str)))
            ]
            if not cached_quotes.empty:
                quotes = _dedupe_quotes(pd.concat([quotes, cached_quotes], ignore_index=True, sort=False))
                available_quote_codes = _target_quote_codes(quotes, report_date)
                missing_quote_rows = a_share.loc[
                    ~a_share["etf_code"].astype(str).isin(available_quote_codes),
                    ["etf_code", "exchange"],
                ].copy()
    if not missing_quote_rows.empty and market_data_cache_only:
        LOGGER.info("daily quote cache-only missing_codes=%s; skip CNINFO quote fallback", len(missing_quote_rows))
    elif not missing_quote_rows.empty:
        LOGGER.info("daily quote fallback cninfo missing_codes=%s", len(missing_quote_rows))
        cninfo_start = _date_minus(report_date, 45)
        cninfo_quotes = cninfo_nsdk.fetch_quote_history(missing_quote_rows, cninfo_start, report_date)
        if not cninfo_quotes.empty:
            quotes = pd.concat([quotes, cninfo_quotes], ignore_index=True, sort=False)
            quotes = _dedupe_quotes(quotes)
    upsert_timeseries_cache("quotes", quotes)
    write_dataframe(quotes, NORMALIZED_DIR / "etf_daily_quotes_history.parquet")
    LOGGER.info(
        "daily quotes ready rows=%s target_available=%s elapsed_seconds=%.2f",
        len(quotes),
        len(_target_quote_codes(quotes, report_date)),
        perf_counter() - stage_started,
    )

    stage_started = perf_counter()
    if not market_data_cache_only:
        bootstrap_cache("nav", read_dataframe(NORMALIZED_DIR / "etf_nav.parquet"))
    cached_nav = select_timeseries_cache("nav", end_date=report_date, codes=a_share["etf_code"].astype(str))
    if market_data_cache_only:
        LOGGER.info("daily nav cache-only date=%s", report_date)
        nav = cached_nav
    else:
        current_cached_nav = cached_nav[
            cached_nav.get("date", pd.Series(None, index=cached_nav.index)).map(normalize_date).eq(report_date)
            & pd.to_numeric(cached_nav.get("nav"), errors="coerce").notna()
        ] if not cached_nav.empty else pd.DataFrame()
        cached_current_codes = set(current_cached_nav.get("etf_code", pd.Series(dtype=str)).astype(str))
        stale_sse_codes = [code for code in sse_codes if code not in cached_current_codes]
        stale_szse_codes = set(szse_codes) - cached_current_codes
        LOGGER.info(
            "daily nav refresh target=%s stale_sse=%s stale_szse=%s",
            report_date,
            len(stale_sse_codes),
            len(stale_szse_codes),
        )
        fetch_started = perf_counter()
        sse_nav = sse_official.fetch_nav_latest(client, stale_sse_codes) if stale_sse_codes else pd.DataFrame()
        LOGGER.info(
            "daily SSE nav ready requested=%s rows=%s elapsed_seconds=%.2f",
            len(stale_sse_codes),
            len(sse_nav),
            perf_counter() - fetch_started,
        )
        fetch_started = perf_counter()
        if stale_szse_codes:
            szse_nav = szse_official.fetch_nav_latest(client, stale_szse_codes)
        else:
            szse_nav = pd.DataFrame()
        LOGGER.info(
            "daily SZSE nav ready requested=%s rows=%s elapsed_seconds=%.2f",
            len(stale_szse_codes),
            len(szse_nav),
            perf_counter() - fetch_started,
        )
        nav = pd.concat([cached_nav, sse_nav, szse_nav], ignore_index=True, sort=False)
    nav = _normalize_frame_date_column(nav)
    if "etf_code" not in nav:
        nav = pd.DataFrame(columns=["etf_code", "date", "nav"])
    if "date" not in nav:
        nav["date"] = None
    nav = nav[nav["etf_code"].isin(set(a_share["etf_code"].astype(str)))]
    nav = nav[(nav["date"].isna()) | (nav["date"].le(report_date))]
    available_nav = nav[nav["nav"].notna() & nav["date"].notna()].copy() if not nav.empty else pd.DataFrame()
    current_nav_codes = set(
        available_nav.loc[available_nav["date"].map(normalize_date).eq(report_date), "etf_code"].astype(str)
    )
    missing_nav_codes = sorted(set(a_share["etf_code"].astype(str)) - current_nav_codes)
    if missing_nav_codes and market_data_cache_only:
        LOGGER.info("daily nav cache-only missing_codes=%s; skip official CSRC nav fallback", len(missing_nav_codes))
    elif missing_nav_codes:
        fetch_started = perf_counter()
        LOGGER.info("daily CSRC nav fallback requested=%s", len(missing_nav_codes))
        csrc_nav = csrc_eid.fetch_nav_latest(client, missing_nav_codes, report_date, prefer_bulk=True)
        LOGGER.info("daily CSRC nav fallback ready rows=%s elapsed_seconds=%.2f", len(csrc_nav), perf_counter() - fetch_started)
        if not csrc_nav.empty:
            nav = pd.concat([nav, csrc_nav], ignore_index=True, sort=False)
    nav_cache = upsert_timeseries_cache("nav", nav)
    write_dataframe(nav_cache, NORMALIZED_DIR / "etf_nav.parquet")
    nav = select_timeseries_cache("nav", end_date=report_date, codes=a_share["etf_code"].astype(str))
    LOGGER.info(
        "daily nav ready rows=%s target_slice_rows=%s elapsed_seconds=%.2f",
        len(nav_cache),
        len(nav),
        perf_counter() - stage_started,
    )

    stage_started = perf_counter()
    if not market_data_cache_only:
        try:
            aum_summary = fund_company_official.backfill_official_aum(
                client,
                a_share,
                start_date,
                report_date,
                artifact_suffix=report_date,
            )
            LOGGER.info(
                "daily official AUM ready fetched=%s flow_eligible=%s",
                aum_summary.get("aum_rows_fetched", 0),
                aum_summary.get("implied_share_rows_flow_eligible", 0),
            )
        except Exception as exc:
            LOGGER.warning("daily fund-company AUM fallback failed: %s", exc)
    shares = select_timeseries_cache("shares", end_date=report_date, codes=all_codes)
    nav = select_timeseries_cache("nav", end_date=report_date, codes=all_codes)
    aum = select_timeseries_cache("aum", end_date=report_date, codes=all_codes)
    write_dataframe(shares, NORMALIZED_DIR / "etf_share.parquet")
    write_dataframe(aum, NORMALIZED_DIR / "etf_aum.parquet")
    LOGGER.info("daily AUM and normalized cache ready elapsed_seconds=%.2f", perf_counter() - stage_started)

    stage_started = perf_counter()
    LOGGER.info("daily build indicators date=%s", report_date)
    etf_daily = build_etf_daily(a_share, active_membership, quotes, shares, nav, report_date, aum=aum)
    etf_daily = apply_adjusted_history_features(
        etf_daily,
        read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet"),
    )
    etf_daily, flow_audit = _apply_flow_audit_status(etf_daily)
    etf_daily = add_flow_speed_columns(etf_daily)
    etf_daily = compute_product_temperature(etf_daily)
    etf_daily = add_product_temperature_history_features(
        etf_daily,
        read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet"),
    )
    write_dataframe(etf_daily, NORMALIZED_DIR / "etf_daily.parquet")
    temperature_cols = [
        "etf_code",
        "date",
        "absolute_temperature",
        "market_percentile_temperature",
        "cluster_percentile_temperature",
        "final_temperature",
        "temperature_confidence",
        "trusted_temperature",
        "temperature",
        "temperature_change",
        "temperature_change_1d",
        "temperature_change_3d",
        "temperature_slope_5d",
        "hot_days_5d",
        "flow_positive_days_5d",
        "continuous_warming",
        "cooling_from_high",
        "temperature_level",
        "temperature_direction",
        "temperature_phase",
        "temperature_stage",
    ]
    temperature_cols = [col for col in temperature_cols if col in etf_daily.columns]
    write_dataframe(etf_daily[temperature_cols], NORMALIZED_DIR / "etf_temperature.parquet")
    upsert_history_file(NORMALIZED_DIR / "etf_daily_history.parquet", etf_daily, ["date", "etf_code"])
    upsert_history_file(
        NORMALIZED_DIR / "etf_temperature_history.parquet",
        etf_daily[temperature_cols],
        ["date", "etf_code"],
    )

    representatives = choose_representatives(etf_daily)
    write_dataframe(representatives, NORMALIZED_DIR / "cluster_representatives.parquet")

    cluster_daily = build_cluster_daily(etf_daily)
    cluster_daily["date"] = report_date
    cluster_daily = add_cluster_temperature_history_features(
        cluster_daily,
        read_dataframe(NORMALIZED_DIR / "cluster_daily_history.parquet"),
    )
    write_dataframe(cluster_daily, NORMALIZED_DIR / "cluster_daily.parquet")
    upsert_history_file(NORMALIZED_DIR / "cluster_daily_history.parquet", cluster_daily, ["date", "cluster_id"])
    cluster_temperature_cols = [
        "date",
        "cluster_id",
        "cluster_name",
        "cluster_temperature",
        "cluster_temperature_confidence",
        "temperature_change",
        "cluster_temperature_change_1d",
        "cluster_temperature_change_3d",
        "cluster_temperature_slope_5d",
        "temperature_level",
        "temperature_direction",
        "temperature_phase",
        "temperature_stage",
    ]
    cluster_temperature_cols = [col for col in cluster_temperature_cols if col in cluster_daily.columns]
    upsert_history_file(
        NORMALIZED_DIR / "cluster_temperature_history.parquet",
        cluster_daily[cluster_temperature_cols],
        ["date", "cluster_id"],
    )

    abnormal_pool = build_abnormal_pool(etf_daily)
    write_dataframe(abnormal_pool, NORMALIZED_DIR / "abnormal_pool.parquet")
    LOGGER.info(
        "daily aggregation ready etf_rows=%s cluster_rows=%s abnormal_rows=%s elapsed_seconds=%.2f",
        len(etf_daily),
        len(cluster_daily),
        len(abnormal_pool),
        perf_counter() - stage_started,
    )

    stage_started = perf_counter()
    corporate_flags = flag_share_jumps(etf_daily)
    if not corporate_flags.empty:
        write_dataframe(corporate_flags, NORMALIZED_DIR / "corporate_action_flags.parquet")

    summary = universe_summary(a_share, active_membership)
    anchor_audit, anchor_metrics = validate_core_anchors(a_share, active_membership, etf_daily)
    write_core_anchor_audit(anchor_audit)
    flow_audit = audit_extreme_flows(etf_daily, cluster_daily) if flow_audit.empty else flow_audit
    metrics = coverage_metrics(
        a_share,
        etf_daily,
        active_membership,
        cluster_daily=cluster_daily,
        flow_audit=flow_audit,
    )
    metrics.update(anchor_metrics)
    history_snapshot = _cached_rows_until_date(NORMALIZED_DIR / "etf_daily_history.parquet", report_date)
    if not history_snapshot.empty and "date" in history_snapshot:
        metrics["history_trading_days"] = int(pd.to_datetime(history_snapshot["date"], errors="coerce").dropna().nunique())
    else:
        metrics["history_trading_days"] = 0
    metrics["render_mode"] = "cache_recompute" if market_data_cache_only else "official_refresh"
    metrics["official_refresh_performed"] = not market_data_cache_only
    metrics["cache_reason"] = ""
    metrics["source_refresh_mode"] = source_refresh_mode
    membership_validation = validate_membership(active_membership)
    (output_dir / "validation.md").write_text(validation_markdown(metrics, membership_validation), encoding="utf-8")
    summary_paths = _write_dated_daily_outputs(output_dir, report_date, etf_daily, cluster_daily, metrics)

    chart_dir = output_dir / "charts"
    LOGGER.info("daily generate charts")
    current_unsettled = quotes.get("date", pd.Series(None, index=quotes.index)).eq(report_date) & unsettled_quote_mask(quotes)
    chart_paths = generate_charts(quotes.loc[~current_unsettled], etf_daily, representatives, abnormal_pool, chart_dir)

    report_path = output_dir / f"A股ETF资金温度日报_{report_date}.md"
    LOGGER.info("daily generate report path=%s", report_path)
    generate_report(
        report_date,
        report_path,
        metrics,
        summary,
        cluster_daily,
        etf_daily,
        representatives,
        abnormal_pool,
        chart_paths,
    )
    write_audit_outputs(report_date)
    finished = perf_counter()
    LOGGER.info(
        "daily run finished date=%s formal_allowed=%s report=%s validation_and_render_seconds=%.2f elapsed_seconds=%.2f",
        report_date,
        metrics.get("formal_report_allowed"),
        report_path,
        finished - stage_started,
        finished - run_started,
    )

    unresolved = []
    if not metrics["formal_report_allowed"]:
        unresolved.append("部分覆盖率或观察集群门槛未达正式报告阈值，报告已显式降级为预览/故障口径。")
    if corporate_flags is not None and not corporate_flags.empty:
        unresolved.append("存在份额单日变化超过20%的ETF，需后续核查公司行为公告。")
    if membership_validation["missing_clusters"]:
        unresolved.append(f"缺少成员集群: {membership_validation['missing_clusters']}")
    unresolved.extend(szse_official.LAST_WARNINGS[:20])
    unresolved.extend(cninfo_nsdk.LAST_WARNINGS[:20])
    unresolved.extend(csrc_eid.LAST_WARNINGS[:20])

    return PipelineResult(
        report_date=report_date,
        report_path=report_path,
        chart_dir=chart_dir,
        metrics=metrics,
        summary=summary,
        representatives=representatives,
        cluster_daily=cluster_daily,
        unresolved_issues=unresolved,
        used_cache=False,
        cache_reason="",
        summary_paths=summary_paths,
    )


def make_summary(report_date: str) -> dict:
    """Rebuild the AI-readable summary artifacts from local cache only.

    This never touches the network: it slices the stored daily history, recomputes
    the same coverage metrics the report uses, and rewrites
    ``summary.json`` / ``llms.txt`` / ``snapshot.md`` for that date.
    """
    store = SnapshotStore()
    if str(report_date).lower() == "auto":
        etf_daily = read_dataframe(NORMALIZED_DIR / "etf_daily.parquet")
        if etf_daily.empty or "date" not in etf_daily:
            raise RuntimeError("无法从本地 etf_daily.parquet 识别 auto 摘要日期。")
        report_date = sorted(etf_daily["date"].dropna().map(normalize_date).unique())[-1]
    snapshot = store.load(report_date)
    etf_daily = snapshot.etf_daily
    cluster_daily = snapshot.cluster_daily
    membership = snapshot.membership()
    universe = etf_daily.copy()
    universe["is_a_share_equity_etf"] = True
    flow_audit = audit_extreme_flows(etf_daily, cluster_daily)
    metrics = coverage_metrics(
        universe,
        etf_daily,
        membership,
        cluster_daily=cluster_daily,
        flow_audit=flow_audit,
    )
    metrics["history_trading_days"] = store.history_trading_days_through(snapshot.report_date)
    _, anchor_metrics = validate_core_anchors(universe, membership, etf_daily)
    metrics.update(anchor_metrics)
    output_dir = output_date_dir(snapshot.report_date)
    result = write_summary_artifacts(output_dir, snapshot.report_date, etf_daily, cluster_daily, metrics)
    return {
        "report_date": snapshot.report_date,
        "formal_report_allowed": bool(metrics.get("formal_report_allowed")),
        "formal_blockers": list(metrics.get("formal_blockers") or []),
        **result,
    }


def validate_run(report_date: str) -> dict:
    store = SnapshotStore()
    if str(report_date).lower() == "auto":
        etf_daily = read_dataframe(NORMALIZED_DIR / "etf_daily.parquet")
        if etf_daily.empty or "date" not in etf_daily:
            raise RuntimeError("无法从本地 etf_daily.parquet 识别 auto 验证日期。")
        report_date = sorted(etf_daily["date"].dropna().map(normalize_date).unique())[-1]
    snapshot = store.load(report_date)
    etf_daily = snapshot.etf_daily
    cluster_daily = snapshot.cluster_daily
    membership = snapshot.membership()
    universe = etf_daily.copy()
    universe["is_a_share_equity_etf"] = True
    flow_audit = audit_extreme_flows(etf_daily, cluster_daily)
    metrics = coverage_metrics(
        universe,
        etf_daily,
        membership,
        cluster_daily=cluster_daily,
        flow_audit=flow_audit,
    )
    metrics["history_trading_days"] = store.history_trading_days_through(snapshot.report_date)
    anchor_audit, anchor_metrics = validate_core_anchors(universe, membership, etf_daily)
    write_core_anchor_audit(anchor_audit)
    metrics.update(anchor_metrics)
    membership_validation = validate_membership(membership)
    out = {"report_date": snapshot.report_date, **metrics, "membership": membership_validation}
    (OUTPUTS_DIR / f"validate_{snapshot.report_date}.json").write_text(
        pd.Series(out).to_json(force_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def validate_anchors(report_date: str | None = None, client: OfficialClient | None = None) -> dict:
    reference = ensure_anchor_reference_data(report_date, client)
    target = normalize_date(report_date) or reference.get("report_date")
    universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    etf_daily = read_dataframe(NORMALIZED_DIR / "etf_daily.parquet")
    required_codes = set(anchor_required_codes())
    if target and not etf_daily.empty and "date" in etf_daily:
        etf_daily = etf_daily[etf_daily["date"].map(normalize_date).eq(target)].copy()
    present = set(etf_daily.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6)) if not etf_daily.empty else set()
    if target and not required_codes.issubset(present):
        etf_daily = build_etf_daily(
            universe,
            membership,
            read_dataframe(NORMALIZED_DIR / "etf_daily_quotes_history.parquet"),
            read_dataframe(NORMALIZED_DIR / "etf_share.parquet"),
            read_dataframe(NORMALIZED_DIR / "etf_nav.parquet"),
            target,
            aum=read_dataframe(NORMALIZED_DIR / "etf_aum.parquet"),
        )
    audit, metrics = validate_core_anchors(universe, membership, etf_daily)
    path = write_core_anchor_audit(audit)
    metrics["audit_path"] = str(path)
    return metrics


def rebuild_history(
    start: str,
    end: str,
    client: OfficialClient | None = None,
    *,
    recompute: bool = False,
) -> list[PipelineResult]:
    ensure_dirs()
    client = client or OfficialClient()
    start_norm = normalize_date(start) or start
    end_norm = normalize_date(end) or end
    target_dates = _weekday_dates(start_norm, end_norm)
    missing_snapshot_dates = [date_text for date_text in target_dates if not _local_snapshot_available(date_text)]

    LOGGER.info("rebuild start start=%s end=%s dates=%s recompute=%s", start_norm, end_norm, len(target_dates), recompute)
    if recompute:
        prefetch_summary = {
            "start_date": start_norm,
            "end_date": end_norm,
            "target_dates": target_dates,
            "skipped": True,
            "skip_reason": "recompute requested; use local market-data cache without official prefetch",
            "missing_snapshot_dates": missing_snapshot_dates,
            "recompute": True,
        }
    else:
        prefetch_summary = prefetch_rebuild_market_data(start_norm, end_norm, client)
        prefetch_summary["skipped"] = False
        prefetch_summary["missing_snapshot_dates"] = missing_snapshot_dates
    (OUTPUTS_DIR / f"rebuild_prefetch_{start_norm}_{end_norm}.json").write_text(
        pd.Series(prefetch_summary).to_json(force_ascii=False, indent=2),
        encoding="utf-8",
    )

    market_data_changed = any(
        int(prefetch_summary.get(key, 0) or 0) > 0
        for key in ("fetched_quote_rows", "fetched_share_rows", "fetched_nav_rows")
    )

    results: list[PipelineResult] = []
    errors: list[dict] = []
    for date_text in target_dates:
        try:
            if recompute:
                LOGGER.info("rebuild recompute date=%s from local market-data cache", date_text)
                results.append(
                    run_pipeline(
                        date_text,
                        client,
                        refresh=True,
                        full_refresh=False,
                        market_data_cache_only=True,
                    )
                )
            elif not market_data_changed and not should_refresh_report_date(date_text)[0]:
                results.append(
                    render_report_from_cache(
                        date_text,
                        cache_reason=(
                            f"rebuild 命中 {date_text} 本地完整快照且市场数据未变化；"
                            "跳过重算，仅重渲染报告。"
                        ),
                    )
                )
            else:
                results.append(
                    run_pipeline(
                        date_text,
                        client,
                        refresh=True,
                        full_refresh=False,
                        market_data_cache_only=True,
                    )
                )
        except Exception as exc:
            errors.append({"date": date_text, "error": repr(exc)})
    if errors:
        path = OUTPUTS_DIR / f"rebuild_errors_{start_norm}_{end_norm}.json"
        path.write_text(pd.Series({"errors": errors}).to_json(force_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"rebuild failed for {len(errors)} date(s); see {path}")
    return results
