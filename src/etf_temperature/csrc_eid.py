from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pandas as pd

from .official_endpoints import OfficialClient, normalize_date, to_float


CSRC_EID_BASE = "http://eid.csrc.gov.cn"
CSRC_EID_HOME = f"{CSRC_EID_BASE}/fund"
CSRC_EID_NAV_PAGE = f"{CSRC_EID_BASE}/fund/subpage/public_net_value_subpage.html"
CSRC_EID_NAV_SEARCH = f"{CSRC_EID_BASE}/fund/disclose/publicDailyReportSearchData.json"
CSRC_EID_NAV_MORE = f"{CSRC_EID_BASE}/fund/disclose/getPublicFundJZInfoMore.do"
LAST_WARNINGS: list[str] = []
LOGGER = logging.getLogger(__name__)
DAILY_NAV_BULK_MIN_CODES = 100
DAILY_NAV_BULK_MAX_PAGES = 5


def _warm_up(client: OfficialClient) -> str | None:
    client.session.headers.update(
        {
            "Referer": CSRC_EID_HOME,
            "Origin": CSRC_EID_BASE,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    client.get_text(CSRC_EID_HOME, referer=CSRC_EID_BASE, raw_prefix="csrc_eid_home")
    client.get_text(CSRC_EID_NAV_PAGE, referer=CSRC_EID_HOME, raw_prefix="csrc_eid_nav_page")
    response = client.session.post(
        CSRC_EID_NAV_SEARCH,
        data={"reportTypeStatus": "01"},
        headers={"Referer": CSRC_EID_HOME, "X-Requested-With": "XMLHttpRequest"},
        timeout=client.timeout,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    client._save_raw(CSRC_EID_NAV_SEARCH, response.text, "csrc_eid_nav_search")
    data = response.json()
    return normalize_date(data.get("maxValuationDate"))


def _nav_ao_data(
    code: str,
    valuation_date: str,
    *,
    display_start: int = 0,
    display_length: int = 20,
    fund_name: str = "",
) -> list[dict[str, object]]:
    return [
        {"name": "sEcho", "value": 1},
        {"name": "iColumns", "value": 5},
        {"name": "sColumns", "value": ""},
        {"name": "iDisplayStart", "value": int(display_start)},
        {"name": "iDisplayLength", "value": int(display_length)},
        {"name": "mDataProp_0", "value": "fund"},
        {"name": "mDataProp_1", "value": "fund"},
        {"name": "mDataProp_2", "value": "fund"},
        {"name": "mDataProp_3", "value": "fund"},
        {"name": "mDataProp_4", "value": "valuationDate"},
        {"name": "fundType", "value": "all"},
        {"name": "fundCompanyShortName", "value": ""},
        {"name": "fundCode", "value": str(code).strip().zfill(6) if str(code).strip() else ""},
        {"name": "fundName", "value": fund_name},
        {"name": "startDate", "value": valuation_date},
        {"name": "endDate", "value": valuation_date},
    ]


def _nav_record(row: dict, valuation_date: str) -> dict | None:
    row_code = str(row.get("code") or "").strip().zfill(6)
    nav_date = normalize_date(row.get("valuationDate"))
    nav = to_float(row.get("shareNetValue"))
    if not row_code.strip("0") or nav_date != valuation_date or nav is None:
        return None
    return {
        "etf_code": row_code,
        "date": nav_date,
        "nav": nav,
        "source": "csrc_eid:公募基金净值信息",
        "source_priority": 2,
        "source_url": CSRC_EID_NAV_MORE,
        "retrieved_at": datetime.now().isoformat(timespec="seconds"),
        "data_date": nav_date,
        "is_official": True,
        "validation_status": "official_nav_fallback",
    }


def _fetch_nav_page(
    client: OfficialClient,
    valuation_date: str,
    *,
    display_start: int,
    display_length: int,
    fund_name: str = "",
) -> dict:
    params = {
        "aoData": json.dumps(
            _nav_ao_data(
                "",
                valuation_date,
                display_start=display_start,
                display_length=display_length,
                fund_name=fund_name,
            ),
            ensure_ascii=False,
        )
    }
    url = f"{CSRC_EID_NAV_MORE}?{urlencode(params)}"
    return client.get_json(
        url,
        referer=CSRC_EID_NAV_PAGE,
        raw_prefix=f"csrc_eid_nav_bulk_{valuation_date.replace('-', '')}_{display_start}",
    )


def _fetch_nav_one(client: OfficialClient, code: str, valuation_date: str) -> dict | None:
    params = {"aoData": json.dumps(_nav_ao_data(code, valuation_date), ensure_ascii=False)}
    url = f"{CSRC_EID_NAV_MORE}?{urlencode(params)}"
    data = client.get_json(url, referer=CSRC_EID_NAV_PAGE, raw_prefix=f"csrc_eid_nav_{code}")
    rows = data.get("aaData") or []
    expected_code = str(code).strip().zfill(6)
    for row in rows:
        record = _nav_record(row, valuation_date)
        if record and record["etf_code"] == expected_code:
            return record
    return None


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(normalize_date(start_date) or "", "%Y-%m-%d").date()
    end = datetime.strptime(normalize_date(end_date) or "", "%Y-%m-%d").date()
    result: list[str] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return result


def _fetch_nav_bulk_for_date(
    client: OfficialClient,
    expected: set[str],
    valuation_date: str,
    *,
    page_size: int,
    sleep_seconds: float,
    fund_name_filter: str,
    max_pages: int | None = None,
) -> tuple[dict[str, dict], bool]:
    found: dict[str, dict] = {}
    display_start = 0
    previous_signature: tuple[str, ...] | None = None
    pages = 0
    while max_pages is None or pages < max_pages:
        try:
            options = {"fund_name": fund_name_filter} if fund_name_filter else {}
            data = _fetch_nav_page(
                client, valuation_date, display_start=display_start, display_length=page_size, **options,
            )
        except Exception as exc:
            LAST_WARNINGS.append(f"CSRC EID 净值 {valuation_date} 批量页 {display_start} 失败: {exc}")
            return found, True

        pages += 1
        rows = data.get("aaData") if isinstance(data, dict) else None
        if not isinstance(data, dict) or (rows is not None and not isinstance(rows, list)):
            LAST_WARNINGS.append(f"CSRC EID 净值 {valuation_date} 批量页 {display_start} 格式无效。")
            return found, True
        rows = [row for row in (rows or []) if isinstance(row, dict)]
        signature = tuple(str(row.get("idStr") or row.get("code") or "") for row in rows[:5])
        if display_start > 0 and rows and signature == previous_signature:
            LAST_WARNINGS.append(f"CSRC EID 净值 {valuation_date} 分页未前进，停止批量分页。")
            return found, True
        previous_signature = signature
        for row in rows:
            record = _nav_record(row, valuation_date)
            if record and record["etf_code"] in expected:
                found[record["etf_code"]] = record

        total = to_float(data.get("iTotalDisplayRecords") or data.get("iTotalRecords")) or len(rows)
        display_start += len(rows)
        if not rows or len(found) >= len(expected) or display_start >= total:
            break
        time.sleep(sleep_seconds)
    return found, False


def fetch_nav_history(
    client: OfficialClient,
    codes: list[str],
    start_date: str,
    end_date: str,
    *,
    page_size: int = 2000,
    sleep_seconds: float = 0.05,
    fallback_per_code: bool = True,
    valuation_dates: list[str] | None = None,
    fund_name_filter: str = "",
) -> pd.DataFrame:
    """Backfill official historical NAV for a set of ETF codes.

    The CSRC EID endpoint supports date filters and DataTables pagination. Fetching
    all funds for one valuation date is much faster than issuing one request per
    ETF; returned rows are filtered to the A-share ETF universe before caching.
    """

    LAST_WARNINGS.clear()
    expected = {str(code).strip().zfill(6) for code in codes if str(code).strip()}
    if not expected:
        return pd.DataFrame()

    try:
        latest_date = _warm_up(client)
    except Exception as exc:
        LAST_WARNINGS.append(f"CSRC EID 净值入口初始化失败: {exc}")
        return pd.DataFrame()

    records: list[dict] = []
    requested_dates = (
        sorted(
            {
                normalize_date(value)
                for value in valuation_dates
                if normalize_date(value) is not None
            }
        )
        if valuation_dates is not None
        else _date_range(start_date, end_date)
    )
    for valuation_date in requested_dates:
        if latest_date and valuation_date > latest_date:
            continue

        found, bulk_failed = _fetch_nav_bulk_for_date(
            client, expected, valuation_date, page_size=page_size,
            sleep_seconds=sleep_seconds, fund_name_filter=fund_name_filter,
        )

        missing = sorted(expected - set(found))
        if missing and fallback_per_code:
            consecutive_failures = 0
            for code in missing:
                try:
                    record = _fetch_nav_one(client, code, valuation_date)
                    if record:
                        found[code] = record
                    consecutive_failures = 0
                except Exception as exc:
                    consecutive_failures += 1
                    LAST_WARNINGS.append(f"CSRC EID 历史净值 {valuation_date} {code} 失败: {exc}")
                    if consecutive_failures >= 8:
                        LAST_WARNINGS.append(
                            f"CSRC EID 历史净值 {valuation_date} 连续失败，停止逐只回退。"
                        )
                        break
                time.sleep(sleep_seconds)

        if bulk_failed and not found:
            LAST_WARNINGS.append(f"CSRC EID 历史净值 {valuation_date} 未取得有效记录。")
        records.extend(found.values())

    if not records:
        return pd.DataFrame()
    return (
        pd.DataFrame(records)
        .sort_values(["date", "etf_code", "source_priority"])
        .drop_duplicates(["date", "etf_code"], keep="first")
        .reset_index(drop=True)
    )


def fetch_nav_gaps(
    client: OfficialClient,
    codes_by_date: dict[str, list[str]],
    *,
    sleep_seconds: float = 0.02,
    max_consecutive_failures: int = 8,
) -> pd.DataFrame:
    """Fetch only known NAV gaps, avoiding another full-market bulk scan."""

    LAST_WARNINGS.clear()
    if not codes_by_date:
        return pd.DataFrame()
    try:
        latest_date = _warm_up(client)
    except Exception as exc:
        LAST_WARNINGS.append(f"CSRC EID NAV gap initialization failed: {exc}")
        return pd.DataFrame()

    records: list[dict] = []
    consecutive_failures = 0
    for raw_date in sorted(codes_by_date):
        valuation_date = normalize_date(raw_date)
        if not valuation_date or (latest_date and valuation_date > latest_date):
            continue
        codes = sorted({str(code).strip().zfill(6) for code in codes_by_date[raw_date] if str(code).strip()})
        for code in codes:
            try:
                record = _fetch_nav_one(client, code, valuation_date)
                if record:
                    records.append(record)
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                LAST_WARNINGS.append(f"CSRC EID NAV gap failed {valuation_date} {code}: {exc}")
                if consecutive_failures >= max_consecutive_failures:
                    LAST_WARNINGS.append("CSRC EID NAV gap fetch stopped after consecutive failures.")
                    break
            time.sleep(sleep_seconds)
        if consecutive_failures >= max_consecutive_failures:
            break
    if not records:
        return pd.DataFrame()
    return (
        pd.DataFrame(records)
        .sort_values(["date", "etf_code", "source_priority"])
        .drop_duplicates(["date", "etf_code"], keep="first")
        .reset_index(drop=True)
    )


def fetch_nav_latest(
    client: OfficialClient,
    codes: list[str],
    report_date: str | None = None,
    sleep_seconds: float = 0.03,
    max_consecutive_failures: int = 8,
    *,
    prefer_bulk: bool = False,
) -> pd.DataFrame:
    """Fetch official NAV rows from CSRC EID for specific fund codes.

    EID is an official disclosure source, but it is not a secondary-market quote
    source. Returned rows should only fill NAV gaps.
    """

    LAST_WARNINGS.clear()
    if not codes:
        return pd.DataFrame()
    try:
        latest_date = _warm_up(client)
    except Exception as exc:
        LAST_WARNINGS.append(f"CSRC EID 净值入口初始化失败: {exc}")
        return pd.DataFrame()
    requested_date = normalize_date(report_date)
    valuation_date = requested_date or latest_date
    if latest_date and valuation_date and valuation_date > latest_date:
        valuation_date = latest_date
    if not valuation_date:
        LAST_WARNINGS.append("CSRC EID 未返回可用净值日期。")
        return pd.DataFrame()

    normalized_codes = list(dict.fromkeys(str(code).strip().zfill(6) for code in codes))
    found: dict[str, dict] = {}
    if prefer_bulk and len(normalized_codes) >= DAILY_NAV_BULK_MIN_CODES and valuation_date == requested_date:
        started = time.perf_counter()
        found, _ = _fetch_nav_bulk_for_date(
            client, set(normalized_codes), valuation_date, page_size=2000, sleep_seconds=0.05,
            fund_name_filter="ETF", max_pages=DAILY_NAV_BULK_MAX_PAGES,
        )
        LOGGER.info(
            "CSRC NAV bulk date=%s matched=%s residual=%s elapsed_seconds=%.2f",
            valuation_date, len(found), len(normalized_codes) - len(found), time.perf_counter() - started,
        )
    records = list(found.values())
    failures = 0
    for normalized_code in normalized_codes:
        if normalized_code in found:
            continue
        try:
            record = _fetch_nav_one(client, normalized_code, valuation_date)
            if record:
                records.append(record)
            failures = 0
        except Exception as exc:
            failures += 1
            LAST_WARNINGS.append(f"CSRC EID 净值 {normalized_code} 失败: {exc}")
            if failures >= max_consecutive_failures:
                LAST_WARNINGS.append("CSRC EID 净值连续失败，触发熔断。")
                break
        time.sleep(sleep_seconds)
    return pd.DataFrame(records)
