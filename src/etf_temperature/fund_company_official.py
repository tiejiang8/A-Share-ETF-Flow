from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

from ._atomic import atomic_replace
from .csv_cache import compact_timeseries_cache, select_timeseries_cache, upsert_timeseries_cache
from .official_endpoints import (
    NORMALIZED_DIR,
    OUTPUTS_DIR,
    OfficialClient,
    normalize_date,
    write_dataframe,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FundCompanySource:
    manager: str
    adapter: str
    official_url: str
    capability: str
    enabled: bool = False
    note: str = ""


@dataclass
class FundCompanyCollection:
    aum: pd.DataFrame
    nav: pd.DataFrame
    source_status: list[dict]


# Only a source whose fields and dates have been verified is enabled.  Other
# entries document the official discovery state; they are not silently treated
# as daily data sources.
SOURCE_REGISTRY: dict[str, FundCompanySource] = {
    "易方达基金管理有限公司": FundCompanySource(
        manager="易方达基金管理有限公司",
        adapter="efunds_product_page",
        official_url="https://www.efunds.com.cn/Mobile/fund/{code}.shtml",
        capability="current_daily_aum_and_nav",
        enabled=True,
        note="产品页同时披露数据日期、基金规模和同日单位净值",
    ),
    "富国基金管理有限公司": FundCompanySource(
        manager="富国基金管理有限公司",
        adapter="not_enabled",
        official_url="https://www.fullgoal.com.cn/fundDetail/{code}/index.html",
        capability="periodic_aum_only",
        note="公开产品页规模来自最新定期报告，未确认日度资产净值",
    ),
    "华夏基金管理有限公司": FundCompanySource(
        manager="华夏基金管理有限公司",
        adapter="not_enabled",
        official_url="https://www.chinaamc.com/fund/{code}/index.shtml",
        capability="pcf_and_periodic_report_only",
        note="PCF含最小申赎单位，不等于实际总份额",
    ),
    "南方基金管理股份有限公司": FundCompanySource(
        manager="南方基金管理股份有限公司",
        adapter="not_enabled",
        official_url="https://www.nffund.com/",
        capability="periodic_report_only",
        note="已确认定期报告期末份额，未确认日度历史份额或AUM",
    ),
}


_EFUNDS_AUM = re.compile(
    r'id=["\']baseinfoAsset["\'][^>]*>\s*[^<]*?'
    r'(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})[^0-9]+'
    r'([0-9][0-9,]*(?:\.[0-9]+)?)',
    re.IGNORECASE,
)
_EFUNDS_NAV_DATE = re.compile(
    r'class=["\'][^"\']*nav-update[^"\']*["\'][^>]*>\s*'
    r'(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})',
    re.IGNORECASE,
)
_EFUNDS_NAV = re.compile(
    r'id=["\']net-today["\'][^>]*>\s*([0-9][0-9,]*(?:\.[0-9]+)?)',
    re.IGNORECASE,
)


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_efunds_product_page(
    html: str,
    code: str,
    *,
    source_url: str | None = None,
    retrieved_at: str | None = None,
) -> tuple[dict | None, dict | None]:
    """Parse one issuer page without inferring a date that is not disclosed."""

    code = str(code).zfill(6)
    source_url = source_url or f"https://www.efunds.com.cn/Mobile/fund/{code}.shtml"
    retrieved_at = retrieved_at or datetime.now().isoformat(timespec="seconds")
    evidence_sha256 = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()
    aum_match = _EFUNDS_AUM.search(html)
    nav_date_match = _EFUNDS_NAV_DATE.search(html)
    nav_match = _EFUNDS_NAV.search(html)
    if not aum_match:
        return None, None

    aum_date = normalize_date(aum_match.group(1))
    aum_value = _number(aum_match.group(2))
    nav_date = normalize_date(nav_date_match.group(1)) if nav_date_match else None
    nav_value = _number(nav_match.group(1)) if nav_match else None
    same_date = bool(aum_date and nav_date and aum_date == nav_date)
    valid_numbers = bool(aum_value and aum_value > 0 and nav_value and nav_value > 0)
    implied_shares = aum_value / nav_value if same_date and valid_numbers else None
    validation_status = (
        "official_same_date_aum_nav"
        if implied_shares is not None
        else "aum_nav_date_mismatch" if aum_date and nav_date and aum_date != nav_date
        else "official_aum_only"
    )
    common = {
        "etf_code": code,
        "source_priority": 1,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "is_official": True,
        "evidence_sha256": evidence_sha256,
        "raw_prefix": f"fund_company_efunds_{code}",
    }
    aum_row = {
        **common,
        "date": aum_date,
        "data_date": aum_date,
        "aum": aum_value,
        "nav": nav_value if same_date else None,
        "nav_date": nav_date,
        "implied_shares": implied_shares,
        "aum_type": "fund_net_assets_as_disclosed",
        "date_convention": "issuer_disclosed_as_of_date",
        "derivation_method": "aum_div_nav_same_date" if implied_shares is not None else "none",
        "source": "fund_company_official:efunds:aum",
        "validation_status": validation_status,
    }
    nav_row = None
    if nav_date and nav_value and nav_value > 0:
        nav_row = {
            **common,
            "date": nav_date,
            "data_date": nav_date,
            "nav": nav_value,
            "source": "fund_company_official:efunds:nav",
            "validation_status": "ok",
        }
    return aum_row, nav_row


def _clone_client(client: OfficialClient) -> OfficialClient:
    return OfficialClient(
        raw_dir=client.raw_dir,
        timeout=client.timeout,
        retries=client.retries,
        sleep_seconds=client.sleep_seconds,
        raw_latest_ttl_seconds=client.raw_latest_ttl_seconds,
    )


def fetch_efunds_current_aum(
    client: OfficialClient,
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    *,
    workers: int = 6,
) -> FundCompanyCollection:
    """Fetch the current issuer-disclosed AUM snapshot with bounded concurrency."""

    start = normalize_date(start_date) or start_date
    end = normalize_date(end_date) or end_date
    normalized_codes = sorted({str(code).zfill(6) for code in codes})
    aum_rows: list[dict] = []
    nav_rows: list[dict] = []
    failures: list[dict] = []

    def fetch_one(code: str) -> tuple[str, dict | None, dict | None, str | None]:
        local = _clone_client(client)
        url = f"https://www.efunds.com.cn/Mobile/fund/{code}.shtml"
        try:
            html = local.get_text(
                url,
                referer="https://www.efunds.com.cn/",
                raw_prefix=f"fund_company_efunds_{code}",
                cache_ttl_seconds=6 * 60 * 60,
            )
            aum_row, nav_row = parse_efunds_product_page(html, code, source_url=url)
            return code, aum_row, nav_row, None
        except Exception as exc:  # pragma: no cover - network error branch
            return code, None, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 8))) as executor:
        futures = {executor.submit(fetch_one, code): code for code in normalized_codes}
        for future in as_completed(futures):
            code, aum_row, nav_row, error = future.result()
            if error:
                failures.append({"etf_code": code, "error": error})
                continue
            if aum_row and aum_row.get("date") and start <= str(aum_row["date"]) <= end:
                aum_rows.append(aum_row)
            if nav_row and nav_row.get("date") and start <= str(nav_row["date"]) <= end:
                nav_rows.append(nav_row)

    status = {
        "manager": "易方达基金管理有限公司",
        "adapter": "efunds_product_page",
        "target_codes": len(normalized_codes),
        "aum_rows": len(aum_rows),
        "nav_rows": len(nav_rows),
        "failed_codes": len(failures),
        "failure_examples": failures[:5],
    }
    return FundCompanyCollection(pd.DataFrame(aum_rows), pd.DataFrame(nav_rows), [status])


def collect_fund_company_aum(
    client: OfficialClient,
    universe: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> FundCompanyCollection:
    if universe.empty or not {"manager", "etf_code"}.issubset(universe.columns):
        return FundCompanyCollection(pd.DataFrame(), pd.DataFrame(), [])
    target = universe.copy()
    if "exchange" in target:
        target = target[target["exchange"].eq("SZSE")]
    if "is_a_share_equity_etf" in target:
        target = target[target["is_a_share_equity_etf"].fillna(False)]
    frames_aum: list[pd.DataFrame] = []
    frames_nav: list[pd.DataFrame] = []
    statuses: list[dict] = []
    for manager, group in target.groupby("manager", dropna=False):
        source = SOURCE_REGISTRY.get(str(manager))
        if source is None or not source.enabled:
            continue
        if source.adapter == "efunds_product_page":
            result = fetch_efunds_current_aum(
                client,
                group["etf_code"].astype(str),
                start_date,
                end_date,
            )
            frames_aum.append(result.aum)
            frames_nav.append(result.nav)
            statuses.extend(result.source_status)
    return FundCompanyCollection(
        pd.concat([frame for frame in frames_aum if not frame.empty], ignore_index=True, sort=False)
        if any(not frame.empty for frame in frames_aum)
        else pd.DataFrame(),
        pd.concat([frame for frame in frames_nav if not frame.empty], ignore_index=True, sort=False)
        if any(not frame.empty for frame in frames_nav)
        else pd.DataFrame(),
        statuses,
    )


def _exchange_share_rows(shares: pd.DataFrame) -> pd.DataFrame:
    columns = ["etf_code", "date", "shares"]
    if shares.empty or not set(columns).issubset(shares.columns):
        return pd.DataFrame(columns=columns)
    out = shares.copy()
    source = out.get("source", pd.Series("", index=out.index)).fillna("").astype(str)
    status = out.get("validation_status", pd.Series("", index=out.index)).fillna("").astype(str)
    out = out[
        source.str.startswith(("sse_official:", "szse_official:"))
        & ~status.isin({"current_share_only_no_history", "anchor_supplement_current_share_only"})
    ].copy()
    out["etf_code"] = out["etf_code"].astype(str).str.zfill(6)
    out["date"] = out["date"].map(normalize_date)
    out["shares"] = pd.to_numeric(out.get("shares"), errors="coerce")
    return out.dropna(subset=["date", "shares"])


def _current_list_share_evidence(universe: pd.DataFrame) -> pd.DataFrame:
    """Return official SZSE current-scale rows for overlap validation only.

    The exchange list does not disclose an economic date, so these values are
    never written to the dated shares cache.  They can still independently
    verify that issuer AUM / NAV yields the same current share count.
    """

    required = {"etf_code", "exchange", "latest_share_10k_from_list"}
    if universe.empty or not required.issubset(universe.columns):
        return pd.DataFrame(columns=["etf_code", "current_list_shares", "evidence_retrieved_date"])
    frame = universe[
        universe["exchange"].eq("SZSE")
        & pd.to_numeric(universe["latest_share_10k_from_list"], errors="coerce").notna()
    ].copy()
    if frame.empty:
        return pd.DataFrame(columns=["etf_code", "current_list_shares", "evidence_retrieved_date"])
    frame["etf_code"] = frame["etf_code"].astype(str).str.zfill(6)
    frame["current_list_shares"] = (
        pd.to_numeric(frame["latest_share_10k_from_list"], errors="coerce") * 10000
    )
    frame["evidence_retrieved_date"] = pd.to_datetime(
        frame.get("retrieved_at", pd.Series(None, index=frame.index)), errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    return (
        frame[["etf_code", "current_list_shares", "evidence_retrieved_date"]]
        .dropna()
        .drop_duplicates("etf_code")
    )


def _attach_overlap_relation(derived: pd.DataFrame, exchange: pd.DataFrame, relation: str) -> pd.DataFrame:
    left = derived.copy()
    right = exchange[["etf_code", "date", "shares"]].rename(
        columns={"date": "exchange_date", "shares": "exchange_shares"}
    )
    if relation == "same_date":
        return left.merge(
            right,
            left_on=["etf_code", "date"],
            right_on=["etf_code", "exchange_date"],
            how="left",
        )
    rows: list[dict] = []
    by_code = {code: group.sort_values("exchange_date") for code, group in right.groupby("etf_code")}
    for record in left.to_dict("records"):
        candidates = by_code.get(record["etf_code"])
        match = None
        if candidates is not None and not candidates.empty:
            if relation == "previous_exchange_date":
                eligible = candidates[candidates["exchange_date"].lt(record["date"])]
                match = eligible.iloc[-1] if not eligible.empty else None
            elif relation == "next_exchange_date":
                eligible = candidates[candidates["exchange_date"].gt(record["date"])]
                match = eligible.iloc[0] if not eligible.empty else None
        record["exchange_date"] = match.get("exchange_date") if match is not None else None
        record["exchange_shares"] = match.get("exchange_shares") if match is not None else None
        rows.append(record)
    return pd.DataFrame(rows)


def validate_implied_shares(
    aum: pd.DataFrame,
    exchange_shares: pd.DataFrame,
    *,
    current_list_evidence: pd.DataFrame | None = None,
    minimum_overlap: int = 5,
    row_relative_tolerance: float = 0.001,
    provider_median_tolerance: float = 0.0002,
) -> tuple[pd.DataFrame, list[dict]]:
    """Validate issuer AUM/NAV shares against exchange rows and date conventions."""

    if aum.empty:
        return pd.DataFrame(), []
    derived = aum.copy()
    derived["etf_code"] = derived["etf_code"].astype(str).str.zfill(6)
    derived["date"] = derived["date"].map(normalize_date)
    derived["implied_shares"] = pd.to_numeric(derived.get("implied_shares"), errors="coerce")
    derived = derived.dropna(subset=["date", "implied_shares"])
    if derived.empty:
        return pd.DataFrame(), []
    exchange = _exchange_share_rows(exchange_shares)
    relations = ["same_date", "previous_exchange_date", "next_exchange_date"]
    candidates: list[tuple[tuple[int, float], str, pd.DataFrame]] = []
    for relation in relations:
        compared = _attach_overlap_relation(derived, exchange, relation)
        compared["exchange_shares"] = pd.to_numeric(
            compared["exchange_shares"], errors="coerce"
        )
        compared["overlap_relative_error"] = (
            (compared["implied_shares"] - compared["exchange_shares"]).abs()
            / compared["exchange_shares"].abs()
        )
        errors = compared["overlap_relative_error"].dropna()
        score = (-len(errors), float(errors.median()) if not errors.empty else float("inf"))
        candidates.append((score, relation, compared))
    if current_list_evidence is not None and not current_list_evidence.empty:
        current = current_list_evidence.copy()
        current["etf_code"] = current["etf_code"].astype(str).str.zfill(6)
        current["current_list_shares"] = pd.to_numeric(
            current["current_list_shares"], errors="coerce"
        )
        compared = derived.merge(current, on="etf_code", how="left")
        derived_dates = pd.to_datetime(compared["date"], errors="coerce")
        evidence_dates = pd.to_datetime(
            compared.get("evidence_retrieved_date", pd.Series(None, index=compared.index)),
            errors="coerce",
        )
        evidence_gap_days = (evidence_dates - derived_dates).abs().dt.days
        compared["overlap_evidence_gap_days"] = evidence_gap_days
        compared["exchange_date"] = None
        compared["exchange_shares"] = compared["current_list_shares"].where(
            evidence_gap_days.le(1)
        )
        compared["overlap_relative_error"] = (
            (compared["implied_shares"] - compared["exchange_shares"]).abs()
            / compared["exchange_shares"].abs()
        )
        errors = compared["overlap_relative_error"].dropna()
        score = (-len(errors), float(errors.median()) if not errors.empty else float("inf"))
        candidates.append((score, "undated_current_official_list", compared))
    _, relation, audit = min(candidates, key=lambda item: item[0])
    errors = audit["overlap_relative_error"].dropna()
    overlap_count = int(len(errors))
    median_error = float(errors.median()) if not errors.empty else None
    p95_error = float(errors.quantile(0.95)) if not errors.empty else None
    provider_valid = bool(
        overlap_count >= int(minimum_overlap)
        and median_error is not None
        and median_error <= float(provider_median_tolerance)
        and p95_error is not None
        and p95_error <= float(row_relative_tolerance)
    )
    if provider_valid:
        row_valid = audit["overlap_relative_error"].isna() | audit["overlap_relative_error"].le(
            float(row_relative_tolerance)
        )
        audit["validation_status"] = (
            "derived_same_date_overlap_validated"
            if relation == "same_date"
            else "derived_formula_validated_date_unresolved"
        )
        audit.loc[~row_valid, "validation_status"] = "derived_same_date_overlap_failed"
    else:
        audit["validation_status"] = "derived_same_date_overlap_pending"
    audit["overlap_relation"] = relation
    audit["provider_overlap_valid"] = provider_valid
    summary = {
        "source": str(derived.get("source", pd.Series(["fund_company_official"])).iloc[0]),
        "overlap_relation": relation,
        "overlap_count": overlap_count,
        "overlap_median_relative_error": median_error,
        "overlap_p95_relative_error": p95_error,
        "provider_overlap_valid": provider_valid,
        "formula_validated": provider_valid,
        "dated_share_flow_valid": bool(provider_valid and relation == "same_date"),
        "minimum_overlap": int(minimum_overlap),
        "row_relative_tolerance": float(row_relative_tolerance),
        "overlap_evidence_use": (
            "validation_only_not_dated_share_input"
            if relation == "undated_current_official_list"
            else "dated_exchange_share_input"
        ),
    }
    return audit, [summary]


def implied_share_cache_rows(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame()
    eligible = audit[
        audit["validation_status"].eq("derived_same_date_overlap_validated")
        & audit["overlap_relation"].eq("same_date")
    ].copy()
    if eligible.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "etf_code": eligible["etf_code"],
            "date": eligible["date"],
            "share_10k": eligible["implied_shares"] / 10000,
            "shares": eligible["implied_shares"],
            "source": eligible["source"].astype(str).str.replace(":aum$", ":aum_nav", regex=True),
            "source_priority": 2,
            "source_url": eligible["source_url"],
            "retrieved_at": eligible["retrieved_at"],
            "data_date": eligible["date"],
            "is_official": True,
            "validation_status": eligible["validation_status"],
            "derivation_method": "aum_div_nav_same_date",
            "aum": eligible["aum"],
            "nav": eligible["nav"],
            "overlap_relation": eligible["overlap_relation"],
            "overlap_relative_error": eligible["overlap_relative_error"],
            "evidence_sha256": eligible["evidence_sha256"],
        }
    )


def source_catalog(universe: pd.DataFrame) -> list[dict]:
    if universe.empty or "manager" not in universe:
        return []
    target = universe.copy()
    if "exchange" in target:
        target = target[target["exchange"].eq("SZSE")]
    if "is_a_share_equity_etf" in target:
        target = target[target["is_a_share_equity_etf"].fillna(False)]
    counts = target["manager"].fillna("未披露管理人").astype(str).value_counts()
    rows: list[dict] = []
    for manager, count in counts.items():
        source = SOURCE_REGISTRY.get(manager)
        rows.append(
            {
                "manager": manager,
                "etf_count": int(count),
                "adapter": source.adapter if source else "not_mapped",
                "official_url": source.official_url if source else None,
                "capability": source.capability if source else "unverified",
                "enabled": bool(source.enabled) if source else False,
                "note": source.note if source else "待逐家公司确认官方日度份额或AUM接口",
            }
        )
    return rows


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    atomic_replace(temp, path)


def backfill_official_aum(
    client: OfficialClient,
    universe: pd.DataFrame,
    start_date: str,
    end_date: str,
    *,
    artifact_suffix: str,
) -> dict:
    """Collect AUM, validate implied shares, and keep unvalidated rows out of flows."""

    collection = collect_fund_company_aum(client, universe, start_date, end_date)
    if not collection.aum.empty:
        aum_cache = upsert_timeseries_cache("aum", collection.aum)
    else:
        aum_cache = select_timeseries_cache("aum", start_date=start_date, end_date=end_date)
    if not collection.nav.empty:
        upsert_timeseries_cache("nav", collection.nav)
    exchange_shares = select_timeseries_cache("shares", start_date=start_date, end_date=end_date)
    audit, overlap = validate_implied_shares(
        collection.aum,
        exchange_shares,
        current_list_evidence=_current_list_share_evidence(universe),
    )
    eligible = implied_share_cache_rows(audit)
    if not eligible.empty:
        upsert_timeseries_cache("shares", eligible)
    share_cache = compact_timeseries_cache("shares")

    audit_path = NORMALIZED_DIR / f"fund_company_implied_shares_{artifact_suffix}.parquet"
    aum_path = NORMALIZED_DIR / "etf_aum.parquet"
    catalog_path = OUTPUTS_DIR / f"fund_company_source_coverage_{artifact_suffix}.json"
    write_dataframe(aum_cache, aum_path)
    write_dataframe(audit, audit_path)
    write_dataframe(share_cache, NORMALIZED_DIR / "etf_share.parquet")
    catalog = source_catalog(universe)
    payload = {
        "start_date": normalize_date(start_date) or start_date,
        "end_date": normalize_date(end_date) or end_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manager_count": len(catalog),
        "enabled_manager_count": sum(bool(row["enabled"]) for row in catalog),
        "source_status": collection.source_status,
        "overlap_validation": overlap,
        "aum_rows_fetched": int(len(collection.aum)),
        "nav_rows_fetched": int(len(collection.nav)),
        "implied_share_rows_audited": int(len(audit)),
        "implied_share_rows_flow_eligible": int(len(eligible)),
        "catalog": catalog,
        "aum_path": str(aum_path),
        "audit_path": str(audit_path),
    }
    _write_json(catalog_path, payload)
    payload["catalog_path"] = str(catalog_path)
    return payload
