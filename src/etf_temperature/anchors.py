from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from . import cninfo_nsdk, csrc_eid
from .official_endpoints import CONFIG_DIR, NORMALIZED_DIR, OUTPUTS_DIR, OfficialClient, normalize_date, read_dataframe, write_dataframe


def load_core_anchors(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or CONFIG_DIR / "core_anchors.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_anchor_supplements(path: Path | None = None) -> pd.DataFrame:
    path = path or CONFIG_DIR / "anchor_supplements.yaml"
    if not path.exists():
        return pd.DataFrame()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = payload.get("supplements") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["etf_code"] = df["etf_code"].astype(str).str.zfill(6)
    return df


def anchor_required_codes(anchors: dict[str, dict[str, Any]] | None = None) -> list[str]:
    anchors = anchors or load_core_anchors()
    codes: list[str] = []
    for item in anchors.values():
        codes.extend(str(code).zfill(6) for code in item.get("required_codes", []) or [])
        codes.extend(str(code).zfill(6) for code in item.get("required_codes_any", []) or [])
    return sorted(set(codes))


def apply_anchor_supplements(universe: pd.DataFrame) -> pd.DataFrame:
    supplements = load_anchor_supplements()
    if supplements.empty:
        return universe
    out = universe.copy()
    existing = set(out.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6))
    now = datetime.now().isoformat(timespec="seconds")
    records = []
    for _, row in supplements.iterrows():
        code = str(row["etf_code"]).zfill(6)
        if code in existing:
            continue
        record = {column: pd.NA for column in out.columns}
        for column in supplements.columns:
            if column in record:
                record[column] = row[column]
        record.update(
            {
                "etf_code": code,
                "exchange": row.get("exchange", "SZSE"),
                "etf_type": row.get("etf_type", "etf"),
                "source_priority": 2,
                "retrieved_at": now,
                "is_official": False,
                "is_a_share_equity_etf": True,
                "exclusion_reason": "",
                "universe_version": datetime.now().strftime("%Y%m%d"),
            }
        )
        records.append(record)
    if records:
        out = pd.concat([out, pd.DataFrame(records)], ignore_index=True, sort=False)
    return out.sort_values(["exchange", "etf_code"]).reset_index(drop=True)


def supplement_anchor_current_shares(shares: pd.DataFrame, universe: pd.DataFrame, report_date: str) -> pd.DataFrame:
    """Legacy no-op retained for callers outside this package.

    A current-scale value without a disclosed economic date cannot be turned
    into a dated share observation without fabricating a flow.
    """

    return shares


def _latest_reference_date() -> str:
    for path in [NORMALIZED_DIR / "etf_daily.parquet", NORMALIZED_DIR / "etf_daily_history.parquet"]:
        df = read_dataframe(path)
        if not df.empty and "date" in df:
            dates = sorted(pd.Series(df["date"].dropna().map(normalize_date)).dropna().unique())
            if dates:
                return str(dates[-1])
    return datetime.now().strftime("%Y-%m-%d")


def ensure_anchor_reference_data(
    report_date: str | None = None,
    client: OfficialClient | None = None,
    *,
    fetch_missing: bool = True,
) -> dict[str, Any]:
    target = normalize_date(report_date) or _latest_reference_date()
    client = client or OfficialClient()
    supplements = load_anchor_supplements()
    if supplements.empty:
        return {"report_date": target, "quote_added": 0, "share_added": 0, "nav_added": 0}
    codes = supplements["etf_code"].astype(str).str.zfill(6).tolist()

    # A current-scale supplement has no proven economic date.  Keep it out of
    # the dated shares history; otherwise a synthetic daily flow is created.
    quotes = read_dataframe(NORMALIZED_DIR / "etf_daily_quotes_history.parquet")
    quote_existing = set()
    if not quotes.empty and {"etf_code", "date"}.issubset(quotes.columns):
        quote_existing = set(quotes.assign(etf_code=quotes["etf_code"].astype(str).str.zfill(6), date=quotes["date"].map(normalize_date)).set_index(["etf_code", "date"]).index)
    quote_missing = supplements.loc[~supplements["etf_code"].map(lambda code: (str(code).zfill(6), target) in quote_existing), ["etf_code", "exchange"]]
    quote_added = 0
    if fetch_missing and not quote_missing.empty:
        fetched = cninfo_nsdk.fetch_quote_history(quote_missing, _date_minus_calendar(target, 24), target)
        if not fetched.empty:
            quotes = pd.concat([quotes, fetched], ignore_index=True, sort=False)
            quotes = quotes.sort_values(["etf_code", "date", "source_priority"]).drop_duplicates(["etf_code", "date"], keep="first")
            write_dataframe(quotes, NORMALIZED_DIR / "etf_daily_quotes_history.parquet")
            quote_added = len(fetched)

    nav = read_dataframe(NORMALIZED_DIR / "etf_nav.parquet")
    nav_existing = set()
    if not nav.empty and {"etf_code", "date", "nav"}.issubset(nav.columns):
        nav_existing = set(
            nav[nav["nav"].notna()]
            .assign(etf_code=nav["etf_code"].astype(str).str.zfill(6), date=nav["date"].map(normalize_date))
            .set_index(["etf_code", "date"])
            .index
        )
    nav_missing = [code for code in codes if (code, target) not in nav_existing]
    nav_added = 0
    if fetch_missing and nav_missing:
        fetched_nav = csrc_eid.fetch_nav_latest(client, nav_missing, target)
        if not fetched_nav.empty:
            nav = pd.concat([nav, fetched_nav], ignore_index=True, sort=False)
            nav = nav.sort_values(["etf_code", "date", "source_priority"]).drop_duplicates(["etf_code", "date"], keep="first")
            write_dataframe(nav, NORMALIZED_DIR / "etf_nav.parquet")
            nav_added = len(fetched_nav)

    return {
        "report_date": target,
        "quote_added": quote_added,
        "share_added": 0,
        "nav_added": nav_added,
        "cninfo_warnings": cninfo_nsdk.LAST_WARNINGS[:5],
        "csrc_warnings": csrc_eid.LAST_WARNINGS[:5],
    }


def _date_minus_calendar(value: str, days: int) -> str:
    return (pd.to_datetime(normalize_date(value)) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def validate_core_anchors(
    universe: pd.DataFrame,
    membership: pd.DataFrame,
    etf_daily: pd.DataFrame | None = None,
    anchors: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    anchors = anchors or load_core_anchors()
    daily = etf_daily.copy() if etf_daily is not None and not etf_daily.empty else pd.DataFrame()
    universe_codes = set(universe.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6))
    membership_by_code = membership.assign(etf_code=membership.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6)).set_index("etf_code") if not membership.empty else pd.DataFrame()
    daily_by_code = daily.assign(etf_code=daily.get("etf_code", pd.Series(dtype=str)).astype(str).str.zfill(6)).set_index("etf_code") if not daily.empty else pd.DataFrame()

    rows = []
    criteria_total = 0
    criteria_valid = 0
    for cluster_id, spec in anchors.items():
        cluster_name = spec.get("cluster_name", cluster_id)
        required = [str(code).zfill(6) for code in spec.get("required_codes", []) or []]
        required_any = [str(code).zfill(6) for code in spec.get("required_codes_any", []) or []]
        for code in required:
            criteria_total += 1
            row = _anchor_row(cluster_id, cluster_name, code, universe_codes, membership_by_code, daily_by_code)
            row["requirement_type"] = "required"
            rows.append(row)
            if row["is_valid"]:
                criteria_valid += 1
        if required_any:
            any_rows = []
            for code in required_any:
                row = _anchor_row(cluster_id, cluster_name, code, universe_codes, membership_by_code, daily_by_code)
                row["requirement_type"] = "required_any"
                any_rows.append(row)
                rows.append(row)
            criteria_total += 1
            if any(row["is_valid"] for row in any_rows):
                criteria_valid += 1

    audit = pd.DataFrame(rows)
    metrics = {
        "core_anchor_total": criteria_total,
        "core_anchor_valid_count": criteria_valid,
        "core_anchor_coverage": criteria_valid / criteria_total if criteria_total else 0.0,
        "core_anchor_missing_codes": audit.loc[~audit["in_universe"], "etf_code"].drop_duplicates().tolist() if not audit.empty else [],
        "core_anchor_wrong_cluster_codes": audit.loc[audit["in_universe"] & ~audit["cluster_ok"], "etf_code"].drop_duplicates().tolist() if not audit.empty else [],
        "core_anchor_missing_data_codes": audit.loc[audit["in_universe"] & audit["cluster_ok"] & ~audit["data_complete"], "etf_code"].drop_duplicates().tolist() if not audit.empty else [],
        "core_anchor_valid": bool(criteria_total and criteria_valid == criteria_total),
    }
    return audit, metrics


def _anchor_row(
    cluster_id: str,
    cluster_name: str,
    code: str,
    universe_codes: set[str],
    membership_by_code: pd.DataFrame,
    daily_by_code: pd.DataFrame,
) -> dict[str, Any]:
    in_universe = code in universe_codes
    assigned_cluster = ""
    assigned_name = ""
    if code in membership_by_code.index:
        item = membership_by_code.loc[code]
        if isinstance(item, pd.DataFrame):
            item = item.iloc[0]
        assigned_cluster = str(item.get("primary_cluster") or "")
        assigned_name = str(item.get("primary_cluster_name") or "")
    cluster_ok = assigned_cluster == cluster_id
    quote_ok = share_ok = nav_ok = False
    if code in daily_by_code.index:
        row = daily_by_code.loc[code]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        quote_ok = pd.notna(row.get("close"))
        share_ok = pd.notna(row.get("latest_shares"))
        nav_ok = pd.notna(row.get("nav"))
    data_complete = quote_ok and share_ok and nav_ok
    return {
        "cluster_id": cluster_id,
        "cluster_name": cluster_name,
        "etf_code": code,
        "in_universe": bool(in_universe),
        "assigned_cluster": assigned_cluster,
        "assigned_cluster_name": assigned_name,
        "cluster_ok": bool(cluster_ok),
        "quote_ok": bool(quote_ok),
        "share_ok": bool(share_ok),
        "nav_ok": bool(nav_ok),
        "data_complete": bool(data_complete),
        "is_valid": bool(in_universe and cluster_ok and data_complete),
    }


def write_core_anchor_audit(audit: pd.DataFrame) -> Path:
    path = OUTPUTS_DIR / "audit" / "core_anchor_audit.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(path, index=False, encoding="utf-8-sig")
    return path
