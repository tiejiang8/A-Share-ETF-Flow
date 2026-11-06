from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import BytesIO
import logging
import re
import time
import warnings
from urllib.parse import quote

import pandas as pd

from .official_endpoints import OfficialClient, extract_first_code, normalize_date, parse_json_or_jsonp, strip_html, to_float
from .quote_freshness import MARKET_TIMEZONE, quote_close_timestamp, unsettled_quote_mask


SZSE_BASES = ["https://fund.szse.cn", "https://www.szse.cn"]
SZSE_BASE = SZSE_BASES[0]
SZSE_REFERER_ETF_LIST_PATH = "/marketdata/etf/index.html"
SZSE_REFERER_VOLUME_PATH = "/marketdata/etf/index.html"
SZSE_REFERER_TREND_PATH = "/marketdata/trade/index.html"
LAST_WARNINGS: list[str] = []
SZSE_QUOTE_MAX_PAGES = 250
SZSE_QUOTE_PAGE_SLEEP_SECONDS = 0.05
LOGGER = logging.getLogger(__name__)


def _szse_url(path_and_query: str, base: str = SZSE_BASE) -> str:
    return f"{base}{path_and_query}"


def _szse_referer(path: str, base: str = SZSE_BASE) -> str:
    return f"{base}{path}"


def _get_json_with_base_fallback(
    client: OfficialClient,
    path_and_query: str,
    *,
    referer_path: str,
    raw_prefix: str,
    preferred_base: str | None = None,
    cache_not_before: float | None = None,
) -> tuple[list[dict], str]:
    errors: list[str] = []
    bases = list(SZSE_BASES)
    if preferred_base in bases:
        bases.remove(preferred_base)
        bases.insert(0, preferred_base)
    options = {} if cache_not_before is None else {"cache_not_before": cache_not_before}
    for base in bases:
        try:
            return (
                client.get_json(
                    _szse_url(path_and_query, base),
                    referer=_szse_referer(referer_path, base),
                    raw_prefix=raw_prefix,
                    **options,
                ),
                base,
            )
        except Exception as exc:
            errors.append(f"{base}: {exc}")
            continue
    LAST_WARNINGS.append(f"SZSE 多站点请求失败 {path_and_query}: {' | '.join(errors)}")
    raise RuntimeError(f"SZSE all bases failed: {path_and_query}")


def _get_bytes_with_base_fallback(
    client: OfficialClient,
    path_and_query: str,
    *,
    referer_path: str,
    raw_prefix: str,
    preferred_base: str | None = None,
) -> tuple[bytes, str]:
    errors: list[str] = []
    bases = list(SZSE_BASES)
    if preferred_base in bases:
        bases.remove(preferred_base)
        bases.insert(0, preferred_base)
    for base in bases:
        try:
            return (
                client.get_bytes(
                    _szse_url(path_and_query, base),
                    referer=_szse_referer(referer_path, base),
                    raw_prefix=raw_prefix,
                ),
                base,
            )
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    LAST_WARNINGS.append(f"SZSE XLSX 多站点请求失败 {path_and_query}: {' | '.join(errors)}")
    raise RuntimeError(f"SZSE XLSX all bases failed: {path_and_query}")


def _report_tab(data: list[dict], tabkey: str | None = None, name: str | None = None) -> dict:
    for tab in data:
        meta = tab.get("metadata", {})
        if tabkey and meta.get("tabkey") == tabkey:
            return tab
        if name and meta.get("name") == name:
            return tab
    return data[0] if data else {}


def _page_count(tab: dict) -> int:
    meta = tab.get("metadata", {})
    return int(meta.get("pagecount") or 1)


def _nav_records_from_rows(rows: list[dict], source_base: str) -> list[dict]:
    now = datetime.now().isoformat(timespec="seconds")
    records: list[dict] = []
    for row in rows:
        # SZSE returns codes such as "159007  "; normalize whitespace before
        # zero-filling so these rows join the six-digit universe codes.
        code = str(row.get("fund_code") or "").strip().zfill(6)
        nav = to_float(row.get("nav_per_share"))
        if not code.strip("0"):
            continue
        nav_date = normalize_date(row.get("nav_date"))
        records.append(
            {
                "etf_code": code,
                "date": nav_date,
                "nav": nav,
                "source": "szse_official:ETFs最新净值列表",
                "source_priority": 1,
                "source_url": _szse_url("/api/report/ShowReport/data?CATALOGID=1945&TABKEY=tab2", source_base),
                "retrieved_at": now,
                "data_date": nav_date,
                "is_official": True,
                "validation_status": "ok" if nav is not None else "nav_missing",
            }
        )
    return records


def _fetch_etf_list_json(client: OfficialClient) -> tuple[pd.DataFrame, pd.DataFrame]:
    LAST_WARNINGS.clear()
    query = "/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1945&loading=first"
    first, first_base = _get_json_with_base_fallback(
        client,
        query,
        referer_path=SZSE_REFERER_ETF_LIST_PATH,
        raw_prefix="szse_etf_list_p1",
    )
    list_tab = _report_tab(first, "tab1")
    nav_tab = _report_tab(first, "tab2")
    list_pages = _page_count(list_tab)
    nav_pages = _page_count(nav_tab)
    list_rows = list_tab.get("data") or []
    nav_rows = nav_tab.get("data") or []
    failures = 0
    for page in range(2, list_pages + 1):
        time.sleep(0.15)
        try:
            data, _ = _get_json_with_base_fallback(
                client,
                f"{query}&PAGENO={page}",
                referer_path=SZSE_REFERER_ETF_LIST_PATH,
                raw_prefix=f"szse_etf_list_p{page}",
                preferred_base=first_base,
            )
            list_rows.extend(_report_tab(data, "tab1").get("data") or [])
            failures = 0
        except Exception as exc:
            failures += 1
            LAST_WARNINGS.append(f"SZSE ETF列表第{page}页失败: {exc}")
            if failures >= 3:
                LAST_WARNINGS.append("SZSE ETF列表连续3页失败，触发熔断。")
                break
    failures = 0
    for page in range(2, nav_pages + 1):
        time.sleep(0.15)
        try:
            data, _ = _get_json_with_base_fallback(
                client,
                f"{query}&TABKEY=tab2&PAGENO={page}",
                referer_path=SZSE_REFERER_ETF_LIST_PATH,
                raw_prefix=f"szse_nav_list_p{page}",
                preferred_base=first_base,
            )
            nav_rows.extend(_report_tab(data, "tab2").get("data") or [])
            failures = 0
        except Exception as exc:
            failures += 1
            LAST_WARNINGS.append(f"SZSE 净值列表第{page}页失败: {exc}")
            if failures >= 3:
                LAST_WARNINGS.append("SZSE 净值列表连续3页失败，触发熔断。")
                break
    now = datetime.now().isoformat(timespec="seconds")
    records = []
    for row in list_rows:
        code = extract_first_code(row.get("sys_key"))
        if not code:
            continue
        index_raw = strip_html(row.get("nhzs"))
        parts = index_raw.split(maxsplit=1)
        index_code = parts[0] if parts and any(ch.isdigit() for ch in parts[0]) else ""
        index_name = parts[1] if len(parts) > 1 else index_raw.replace(index_code, "").strip()
        records.append(
            {
                "etf_code": code,
                "etf_name": strip_html(row.get("kzjcurl")),
                "expanded_name": strip_html(row.get("kzjcurl")),
                "exchange": "SZSE",
                "manager": strip_html(row.get("glrmc")),
                "etf_type": "etf",
                "sub_class": "",
                "tracking_index_code": index_code,
                "tracking_index_name": index_name,
                "listing_date": None,
                "latest_share_10k_from_list": to_float(row.get("dqgm")),
                "source": "szse_official:ETF列表",
                "source_priority": 1,
                "source_url": _szse_url("/api/report/ShowReport/data?CATALOGID=1945", first_base),
                "retrieved_at": now,
                "is_official": True,
                "validation_status": "raw",
            }
        )
    nav_records = _nav_records_from_rows(nav_rows, first_base)
    return pd.DataFrame(records), pd.DataFrame(nav_records)


def _fetch_nav_latest_json(
    client: OfficialClient,
    codes=None,
    *,
    max_workers: int = 4,
    page_delay_seconds: float = 0.15,
) -> pd.DataFrame:
    """Fetch only SZSE's latest-NAV tab with bounded parallel paging.

    The first request discovers the page count and a working SZSE base. Later
    pages reuse that base first, avoiding a repeated HTTPS/HTTP probe per page.
    """

    LAST_WARNINGS.clear()
    code_set = {str(code).strip().zfill(6) for code in (codes or []) if str(code).strip()}
    query = "/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1945&loading=first&TABKEY=tab2"
    first, first_base = _get_json_with_base_fallback(
        client,
        query,
        referer_path=SZSE_REFERER_ETF_LIST_PATH,
        raw_prefix="szse_nav_list_p1",
    )
    nav_tab = _report_tab(first, "tab2")
    pages = _page_count(nav_tab)
    nav_rows = list(nav_tab.get("data") or [])
    page_numbers = list(range(2, pages + 1))
    worker_count = min(max(int(max_workers), 1), max(len(page_numbers), 1))
    LOGGER.info(
        "szse nav start target_codes=%s pages=%s workers=%s base=%s",
        len(code_set),
        pages,
        worker_count,
        first_base,
    )

    def fetch_page_chunk(chunk: list[int]) -> list[tuple[int, list[dict], str | None]]:
        worker_client = client
        owns_client = False
        if isinstance(client, OfficialClient):
            client_kwargs = {
                "timeout": getattr(client, "timeout", 12),
                "retries": getattr(client, "retries", 3),
                "sleep_seconds": getattr(client, "sleep_seconds", 0.5),
                "raw_latest_ttl_seconds": getattr(client, "raw_latest_ttl_seconds", 6 * 60 * 60),
                "raw_dir": getattr(client, "raw_dir"),
            }
            worker_client = OfficialClient(**client_kwargs)
            owns_client = True
        results: list[tuple[int, list[dict], str | None]] = []
        try:
            for page in chunk:
                if page_delay_seconds > 0:
                    time.sleep(page_delay_seconds)
                try:
                    data, _ = _get_json_with_base_fallback(
                        worker_client,
                        f"{query}&PAGENO={page}",
                        referer_path=SZSE_REFERER_ETF_LIST_PATH,
                        raw_prefix=f"szse_nav_list_p{page}",
                        preferred_base=first_base,
                    )
                    results.append((page, _report_tab(data, "tab2").get("data") or [], None))
                except Exception as exc:
                    results.append((page, [], str(exc)))
        finally:
            if owns_client:
                try:
                    worker_client.session.close()
                except Exception:
                    pass
        return results

    page_results: list[tuple[int, list[dict], str | None]] = []
    if page_numbers:
        chunks = [page_numbers[index::worker_count] for index in range(worker_count)]
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(fetch_page_chunk, chunk) for chunk in chunks if chunk]
            for future in as_completed(futures):
                page_results.extend(future.result())

    failed_pages = 0
    for page, rows, error in sorted(page_results, key=lambda item: item[0]):
        if error:
            failed_pages += 1
            LAST_WARNINGS.append(f"SZSE 净值列表第{page}页失败: {error}")
            continue
        nav_rows.extend(rows)

    result = pd.DataFrame(_nav_records_from_rows(nav_rows, first_base))
    if not result.empty:
        result = result.drop_duplicates(["etf_code", "date"], keep="last")
        if code_set:
            result = result[result["etf_code"].isin(code_set)].copy()
    LOGGER.info("szse nav finished rows=%s failed_pages=%s", len(result), failed_pages)
    return result


def _fetch_share_history_json(client: OfficialClient, start_date: str, end_date: str) -> pd.DataFrame:
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    LOGGER.info("szse shares start window=%s..%s", start, end)
    query = (
        "/api/report/ShowReport/data?"
        "SHOWTYPE=JSON&CATALOGID=scsj_fund_jjgm&jjlb=ETF&loading=first"
        f"&txtStart={quote(start or '')}&txtEnd={quote(end or '')}"
    )
    first, first_base = _get_json_with_base_fallback(
        client,
        query,
        referer_path=SZSE_REFERER_VOLUME_PATH,
        raw_prefix="szse_share_p1",
    )
    tab = _report_tab(first)
    pages = _page_count(tab)
    rows = tab.get("data") or []
    LOGGER.info("szse shares first page rows=%s pages=%s", len(rows), pages)
    failures = 0
    for page in range(2, pages + 1):
        time.sleep(0.15)
        try:
            data, _ = _get_json_with_base_fallback(
                client,
                f"{query}&PAGENO={page}",
                referer_path=SZSE_REFERER_VOLUME_PATH,
                raw_prefix=f"szse_share_p{page}",
                preferred_base=first_base,
            )
            rows.extend(_report_tab(data).get("data") or [])
            failures = 0
            if page == pages or page % 20 == 0:
                LOGGER.info("szse shares progress page=%s/%s rows=%s", page, pages, len(rows))
        except Exception as exc:
            failures += 1
            LAST_WARNINGS.append(f"SZSE 规模第{page}页失败: {exc}")
            LOGGER.warning("szse shares page=%s/%s failed failures=%s error=%s", page, pages, failures, exc)
            if failures >= 3:
                LAST_WARNINGS.append("SZSE 规模连续3页失败，触发熔断。")
                LOGGER.warning("szse shares circuit breaker after consecutive failures")
                break
    now = datetime.now().isoformat(timespec="seconds")
    records = []
    for row in rows:
        code = str(row.get("fund_code") or "").strip().zfill(6)
        shares_10k = to_float(row.get("current_size"))
        if not code.strip("0") or shares_10k is None:
            continue
        d = normalize_date(row.get("size_date"))
        records.append(
            {
                "etf_code": code,
                "date": d,
                "share_10k": shares_10k,
                "shares": shares_10k * 10000,
                "source": "szse_official:基金规模",
                "source_priority": 1,
                "source_url": _szse_url("/api/report/ShowReport/data?CATALOGID=scsj_fund_jjgm", first_base),
                "retrieved_at": now,
                "data_date": d,
                "is_official": True,
                "validation_status": "ok",
            }
        )
    result = pd.DataFrame(records)
    LOGGER.info("szse shares finished rows=%s", len(result))
    return result


def _read_xlsx(payload: bytes) -> pd.DataFrame:
    if not payload.startswith(b"PK"):
        preview = payload[:200].decode("utf-8", errors="replace").strip()
        raise ValueError(f"SZSE response is not an XLSX workbook: {preview}")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        frame = pd.read_excel(BytesIO(payload), dtype=object, engine="openpyxl")
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _column(frame: pd.DataFrame, *candidates: str) -> str:
    def compact(value: object) -> str:
        return re.sub(r"\s+", "", str(value)).replace("（", "(").replace("）", ")")

    columns = {compact(column): str(column) for column in frame.columns}
    for candidate in candidates:
        found = columns.get(compact(candidate))
        if found is not None:
            return found
    raise KeyError(f"missing XLSX column {candidates}; actual={list(frame.columns)}")


def _excel_code(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def _excel_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return strip_html(value)


def _quarter_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        quarter_end = cursor.to_period("Q").end_time.normalize()
        chunk_end = min(quarter_end, end)
        windows.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + pd.Timedelta(days=1)
    return windows


def fetch_etf_list(client: OfficialClient) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch the complete SZSE ETF list and latest NAV using three bulk requests.

    One JSON metadata request supplies the as-of date missing from the list
    workbook; the list and NAV themselves each use one official XLSX export.
    JSON pagination remains a compatibility fallback.
    """

    if not hasattr(client, "get_bytes"):
        return _fetch_etf_list_json(client)
    LAST_WARNINGS.clear()
    metadata_query = "/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=fund_etf&loading=first"
    preferred_base: str | None = None
    share_date: str | None = None
    try:
        metadata_payload, preferred_base = _get_json_with_base_fallback(
            client,
            metadata_query,
            referer_path=SZSE_REFERER_ETF_LIST_PATH,
            raw_prefix="szse_etf_metadata",
        )
        metadata = _report_tab(metadata_payload).get("metadata", {})
        share_date = normalize_date(metadata.get("subname"))
    except Exception as exc:
        LAST_WARNINGS.append(f"SZSE ETF规模日期元数据失败，规模保留但日期留空: {exc}")

    list_query = "/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=fund_etf&TABKEY=tab1"
    nav_query = "/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1945&TABKEY=tab2"
    try:
        list_payload, list_base = _get_bytes_with_base_fallback(
            client,
            list_query,
            referer_path=SZSE_REFERER_ETF_LIST_PATH,
            raw_prefix="szse_etf_list_xlsx",
            preferred_base=preferred_base,
        )
        nav_payload, nav_base = _get_bytes_with_base_fallback(
            client,
            nav_query,
            referer_path=SZSE_REFERER_ETF_LIST_PATH,
            raw_prefix="szse_nav_latest_xlsx",
            preferred_base=list_base,
        )
        list_frame = _read_xlsx(list_payload)
        nav_frame = _read_xlsx(nav_payload)
    except Exception as exc:
        fallback_warning = f"SZSE XLSX列表/净值失败，回退JSON分页: {exc}"
        prior_warnings = [*LAST_WARNINGS, fallback_warning]
        result = _fetch_etf_list_json(client)
        LAST_WARNINGS[:0] = prior_warnings
        return result

    code_col = _column(list_frame, "证券代码")
    name_col = _column(list_frame, "证券简称")
    index_col = _column(list_frame, "拟合指数")
    shares_col = _column(list_frame, "当前规模(份)")
    manager_col = _column(list_frame, "基金管理人")
    now = datetime.now().isoformat(timespec="seconds")
    records: list[dict] = []
    for _, row in list_frame.iterrows():
        code = _excel_code(row.get(code_col))
        if not code:
            continue
        index_raw = _excel_text(row.get(index_col))
        parts = index_raw.split(maxsplit=1)
        index_code = parts[0] if parts and any(ch.isdigit() for ch in parts[0]) else ""
        index_name = parts[1] if len(parts) > 1 else index_raw.replace(index_code, "").strip()
        shares = to_float(row.get(shares_col))
        records.append(
            {
                "etf_code": code,
                "etf_name": _excel_text(row.get(name_col)),
                "expanded_name": _excel_text(row.get(name_col)),
                "exchange": "SZSE",
                "manager": _excel_text(row.get(manager_col)),
                "etf_type": "etf",
                "sub_class": "",
                "tracking_index_code": index_code,
                "tracking_index_name": index_name,
                "listing_date": None,
                "latest_share_10k_from_list": shares / 10000 if shares is not None else None,
                "latest_share_date_from_list": share_date,
                "source": "szse_official:ETF列表XLSX",
                "source_priority": 1,
                "source_url": _szse_url(list_query, list_base),
                "retrieved_at": now,
                "data_date": share_date,
                "is_official": True,
                "validation_status": "ok" if shares is not None else "share_missing",
            }
        )

    nav_date_col = _column(nav_frame, "净值日期")
    nav_code_col = _column(nav_frame, "基金代码")
    nav_col = _column(nav_frame, "单位基金净值")
    nav_records: list[dict] = []
    for _, row in nav_frame.iterrows():
        code = _excel_code(row.get(nav_code_col))
        nav = to_float(row.get(nav_col))
        if not code:
            continue
        nav_date = normalize_date(row.get(nav_date_col))
        nav_records.append(
            {
                "etf_code": code,
                "date": nav_date,
                "nav": nav,
                "source": "szse_official:ETFs最新净值XLSX",
                "source_priority": 1,
                "source_url": _szse_url(nav_query, nav_base),
                "retrieved_at": now,
                "data_date": nav_date,
                "is_official": True,
                "validation_status": "ok" if nav is not None else "nav_missing",
            }
        )
    return pd.DataFrame(records), pd.DataFrame(nav_records)


def fetch_nav_latest(
    client: OfficialClient,
    codes=None,
    *,
    max_workers: int = 4,
    page_delay_seconds: float = 0.15,
) -> pd.DataFrame:
    """Fetch all latest SZSE NAV rows in one XLSX request."""

    if not hasattr(client, "get_bytes"):
        return _fetch_nav_latest_json(
            client,
            codes,
            max_workers=max_workers,
            page_delay_seconds=page_delay_seconds,
        )
    LAST_WARNINGS.clear()
    query = "/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1945&TABKEY=tab2"
    try:
        payload, base = _get_bytes_with_base_fallback(
            client,
            query,
            referer_path=SZSE_REFERER_ETF_LIST_PATH,
            raw_prefix="szse_nav_latest_xlsx",
        )
        frame = _read_xlsx(payload)
        date_col = _column(frame, "净值日期")
        code_col = _column(frame, "基金代码")
        nav_col = _column(frame, "单位基金净值")
        now = datetime.now().isoformat(timespec="seconds")
        rows = []
        for _, row in frame.iterrows():
            code = _excel_code(row.get(code_col))
            nav = to_float(row.get(nav_col))
            if not code:
                continue
            nav_date = normalize_date(row.get(date_col))
            rows.append(
                {
                    "etf_code": code,
                    "date": nav_date,
                    "nav": nav,
                    "source": "szse_official:ETFs最新净值XLSX",
                    "source_priority": 1,
                    "source_url": _szse_url(query, base),
                    "retrieved_at": now,
                    "data_date": nav_date,
                    "is_official": True,
                    "validation_status": "ok" if nav is not None else "nav_missing",
                }
            )
        result = pd.DataFrame(rows)
        code_set = {str(code).strip().zfill(6) for code in (codes or []) if str(code).strip()}
        if code_set and not result.empty:
            result = result[result["etf_code"].isin(code_set)].copy()
        return result.drop_duplicates(["etf_code", "date"], keep="last") if not result.empty else result
    except Exception as exc:
        fallback_warning = f"SZSE 最新净值XLSX失败，回退JSON分页: {exc}"
        result = _fetch_nav_latest_json(
            client,
            codes,
            max_workers=max_workers,
            page_delay_seconds=page_delay_seconds,
        )
        LAST_WARNINGS.insert(0, fallback_warning)
        return result


def _share_history_xlsx_chunk(client: OfficialClient, start_date: str, end_date: str) -> pd.DataFrame:
    query = (
        "/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=scsj_fund_jjgm&TABKEY=tab1&jjlb=ETF"
        f"&txtStart={quote(start_date)}&txtEnd={quote(end_date)}"
    )
    payload, base = _get_bytes_with_base_fallback(
        client,
        query,
        referer_path=SZSE_REFERER_VOLUME_PATH,
        raw_prefix=f"szse_share_xlsx_{start_date}_{end_date}",
    )
    frame = _read_xlsx(payload)
    date_col = _column(frame, "日期")
    code_col = _column(frame, "基金代码")
    shares_col = _column(frame, "基金规模(份)")
    now = datetime.now().isoformat(timespec="seconds")
    records: list[dict] = []
    for _, row in frame.iterrows():
        code = _excel_code(row.get(code_col))
        shares = to_float(row.get(shares_col))
        if not code or shares is None:
            continue
        date_text = normalize_date(row.get(date_col))
        records.append(
            {
                "etf_code": code,
                "date": date_text,
                "share_10k": shares / 10000,
                "shares": shares,
                "source": "szse_official:基金规模XLSX",
                "source_priority": 1,
                "source_url": _szse_url(query, base),
                "retrieved_at": now,
                "data_date": date_text,
                "is_official": True,
                "validation_status": "ok",
            }
        )
    return pd.DataFrame(records)


def fetch_share_history(client: OfficialClient, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch historical SZSE ETF shares, one official XLSX per calendar quarter."""

    start = normalize_date(start_date)
    end = normalize_date(end_date)
    if not start or not end:
        raise ValueError(f"invalid share-history date range: {start_date}..{end_date}")
    if not hasattr(client, "get_bytes"):
        return _fetch_share_history_json(client, start, end)
    LAST_WARNINGS.clear()
    frames: list[pd.DataFrame] = []
    for chunk_start, chunk_end in _quarter_windows(start, end):
        try:
            frame = _share_history_xlsx_chunk(client, chunk_start, chunk_end)
            LOGGER.info("szse shares xlsx window=%s..%s rows=%s", chunk_start, chunk_end, len(frame))
        except Exception as exc:
            LAST_WARNINGS.append(
                f"SZSE 规模XLSX {chunk_start}..{chunk_end} 失败，回退JSON分页: {exc}"
            )
            frame = _fetch_share_history_json(client, chunk_start, chunk_end)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(["etf_code", "date"], keep="last").sort_values(
        ["date", "etf_code"]
    )
    # The XLSX export can lag the underlying report by an extra day, most often
    # across weekends: the JSON pagination API then already serves dates that the
    # workbook does not cover yet. When the requested end date has no XLSX rows,
    # top up the uncovered tail via JSON paging so T+1 report runs are not blocked
    # by the slower workbook publication cadence.
    covered_dates = set(result["date"].astype(str))
    if end not in covered_dates:
        tail_start = start
        if covered_dates:
            max_covered = max(covered_dates)
            tail_start = (
                datetime.strptime(max_covered, "%Y-%m-%d") + timedelta(days=1)
            ).strftime("%Y-%m-%d")
        if tail_start <= end:
            LOGGER.info(
                "szse shares xlsx missing end date=%s; json top-up window=%s..%s",
                end,
                tail_start,
                end,
            )
            try:
                json_frame = _fetch_share_history_json(client, tail_start, end)
            except Exception as exc:  # pragma: no cover - defensive network guard
                LAST_WARNINGS.append(f"SZSE 规模JSON补采 {tail_start}..{end} 失败: {exc}")
                json_frame = pd.DataFrame()
            if not json_frame.empty:
                result = (
                    pd.concat([result, json_frame], ignore_index=True)
                    .drop_duplicates(["etf_code", "date"], keep="last")
                    .sort_values(["date", "etf_code"])
                )
    LOGGER.info("szse shares xlsx finished rows=%s windows=%s", len(result), len(frames))
    return result.reset_index(drop=True)


def _fetch_quote_history_bulk_legacy(
    client: OfficialClient,
    start_date: str,
    end_date: str,
    *,
    max_pages: int | None = None,
) -> pd.DataFrame:
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    query = (
        "/api/report/ShowReport/data?"
        "SHOWTYPE=JSON&CATALOGID=1815_stock_snapshot&TABKEY=tab2&loading=first"
        f"&txtBeginDate={quote(start or '')}&txtEndDate={quote(end or '')}"
    )
    first, first_base = _get_json_with_base_fallback(
        client,
        query,
        referer_path=SZSE_REFERER_TREND_PATH,
        raw_prefix="szse_quote_p1",
    )
    tab = _report_tab(first, "tab2")
    pages = _page_count(tab)
    rows = tab.get("data") or []
    page_limit = SZSE_QUOTE_MAX_PAGES if max_pages is None else max(int(max_pages), 1)
    if pages > page_limit:
        LAST_WARNINGS.append(
            f"SZSE quote pagecount {pages} exceeds cap {page_limit}; "
            "truncated bulk paging and will rely on downstream quote fallbacks for missing ETF codes."
        )
    failures = 0
    for page in range(2, min(pages, page_limit) + 1):
        time.sleep(SZSE_QUOTE_PAGE_SLEEP_SECONDS)
        try:
            data, _ = _get_json_with_base_fallback(
                client,
                f"{query}&PAGENO={page}",
                referer_path=SZSE_REFERER_TREND_PATH,
                raw_prefix=f"szse_quote_p{page}",
                preferred_base=first_base,
            )
            page_rows = _report_tab(data, "tab2").get("data") or []
            if not page_rows:
                LAST_WARNINGS.append(f"SZSE quote page {page} returned no rows; stop paging.")
                break
            rows.extend(page_rows)
            failures = 0
        except Exception as exc:
            failures += 1
            LAST_WARNINGS.append(f"SZSE 行情第{page}页失败: {exc}")
            if failures >= 3:
                LAST_WARNINGS.append("SZSE 行情连续3页失败，触发熔断。")
                break
    now = datetime.now().isoformat(timespec="seconds")
    records = []
    for row in rows:
        code = str(row.get("zqdm") or "").zfill(6)
        if not code.strip("0"):
            continue
        d = normalize_date(row.get("jyrq"))
        records.append(
            {
                "etf_code": code,
                "date": d,
                "open": to_float(row.get("ks")),
                "high": to_float(row.get("zg")),
                "low": to_float(row.get("zd")),
                "close": to_float(row.get("ss")),
                "prev_close": to_float(row.get("qss")),
                "pct_change": to_float(row.get("sdf")),
                "volume": (to_float(row.get("cjgs")) or 0) * 10000,
                "amount": (to_float(row.get("cjje")) or 0) * 10000,
                "source": "szse_official:基金行情",
                "source_priority": 1,
                "source_url": _szse_url("/api/report/ShowReport/data?CATALOGID=1815_stock_snapshot&TABKEY=tab2", first_base),
                "retrieved_at": now,
                "data_date": d,
                "is_official": True,
                "validation_status": "ok",
            }
        )
    return pd.DataFrame(records)


def latest_share_date(client: OfficialClient) -> str | None:
    df = fetch_share_history(client, "2020-01-01", "2099-12-31")
    if df.empty:
        return None
    return sorted(df["date"].dropna().unique())[-1]


def _quote_records_from_rows(rows: list[dict], first_base: str) -> list[dict]:
    now = datetime.now(MARKET_TIMEZONE).replace(tzinfo=None).isoformat(timespec="seconds")
    records: list[dict] = []
    for row in rows:
        code = str(row.get("zqdm") or "").zfill(6)
        if not code.strip("0"):
            continue
        d = normalize_date(row.get("jyrq"))
        records.append(
            {
                "etf_code": code,
                "date": d,
                "open": to_float(row.get("ks")),
                "high": to_float(row.get("zg")),
                "low": to_float(row.get("zd")),
                "close": to_float(row.get("ss")),
                "prev_close": to_float(row.get("qss")),
                "pct_change": to_float(row.get("sdf")),
                "volume": (to_float(row.get("cjgs")) or 0) * 10000,
                "amount": (to_float(row.get("cjje")) or 0) * 10000,
                "source": "szse_official:quote",
                "source_priority": 1,
                "source_url": _szse_url(
                    "/api/report/ShowReport/data?CATALOGID=1815_stock_snapshot&TABKEY=tab2",
                    first_base,
                ),
                "retrieved_at": now,
                "data_date": d,
                "is_official": True,
                "validation_status": "ok",
            }
        )
    return records


def load_quote_history_from_raw_cache(raw_dir, start_date: str, end_date: str, codes) -> pd.DataFrame:
    code_set = {str(code).strip().zfill(6) for code in (codes or []) if str(code).strip()}
    if not code_set:
        return pd.DataFrame()
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    records: list[dict] = []
    for path in raw_dir.glob("*.txt"):
        name = path.name
        if "_szse_quote_" not in name:
            continue
        matched_code = next((code for code in code_set if f"_szse_quote_{code}_" in name), None)
        if not matched_code:
            continue
        try:
            captured = datetime.fromtimestamp(path.stat().st_mtime, MARKET_TIMEZONE)
            text = path.read_text(encoding="utf-8")
            payload = text.split("\n\n", 1)[1] if "\n\n" in text else text
            data = parse_json_or_jsonp(payload)
            tab = _report_tab(data, "tab2")
            rows = tab.get("data") or []
            for record in _quote_records_from_rows(rows, SZSE_BASE):
                d = normalize_date(record.get("date"))
                if d and (not start or d >= start) and (not end or d <= end):
                    record["retrieved_at"] = captured.replace(tzinfo=None).isoformat(timespec="seconds")
                    records.append(record)
        except Exception:
            continue
    if not records:
        return pd.DataFrame()
    quotes = pd.DataFrame(records)
    return quotes.loc[~unsettled_quote_mask(quotes)].sort_values("retrieved_at").drop_duplicates(
        ["etf_code", "date"], keep="last",
    )


def _fetch_quote_history_query(
    client: OfficialClient,
    start_date: str,
    end_date: str,
    *,
    code: str | None = None,
    max_pages: int | None = None,
    cache_not_before: float | None = None,
) -> list[dict]:
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    query = (
        "/api/report/ShowReport/data?"
        "SHOWTYPE=JSON&CATALOGID=1815_stock_snapshot&TABKEY=tab2&loading=first"
        f"&txtBeginDate={quote(start or '')}&txtEndDate={quote(end or '')}"
    )
    if code:
        query = f"{query}&txtDMorJC={quote(str(code).zfill(6))}"
    options = {} if cache_not_before is None else {"cache_not_before": cache_not_before}
    first, first_base = _get_json_with_base_fallback(
        client,
        query,
        referer_path=SZSE_REFERER_TREND_PATH,
        raw_prefix=f"szse_quote_{str(code).zfill(6)}_p1" if code else "szse_quote_p1",
        **options,
    )
    tab = _report_tab(first, "tab2")
    pages = _page_count(tab)
    rows = tab.get("data") or []
    page_limit = SZSE_QUOTE_MAX_PAGES if max_pages is None else max(int(max_pages), 1)
    if pages > page_limit:
        LAST_WARNINGS.append(
            f"SZSE quote pagecount {pages} exceeds cap {page_limit}; "
            "truncated paging and will use fallbacks for missing ETF codes."
        )
    failures = 0
    for page in range(2, min(pages, page_limit) + 1):
        time.sleep(SZSE_QUOTE_PAGE_SLEEP_SECONDS)
        try:
            data, _ = _get_json_with_base_fallback(
                client,
                f"{query}&PAGENO={page}",
                referer_path=SZSE_REFERER_TREND_PATH,
                raw_prefix=f"szse_quote_{str(code).zfill(6)}_p{page}" if code else f"szse_quote_p{page}",
                preferred_base=first_base,
                **options,
            )
            page_rows = _report_tab(data, "tab2").get("data") or []
            if not page_rows:
                LAST_WARNINGS.append(f"SZSE quote page {page} returned no rows; stop paging.")
                break
            rows.extend(page_rows)
            failures = 0
        except Exception as exc:
            failures += 1
            LAST_WARNINGS.append(f"SZSE quote page {page} failed: {exc}")
            if failures >= 3:
                LAST_WARNINGS.append("SZSE quote paging stopped after 3 consecutive failures.")
                break
    return _quote_records_from_rows(rows, first_base)


def fetch_quote_history(
    client: OfficialClient,
    start_date: str,
    end_date: str,
    codes=None,
    *,
    max_pages: int | None = None,
    max_workers: int = 64,
    settled_for: str | None = None,
) -> pd.DataFrame:
    options = {"cache_not_before": quote_close_timestamp(settled_for)} if settled_for else {}
    code_list = sorted({str(code).strip().zfill(6) for code in (codes or []) if str(code).strip()})
    if not code_list:
        LAST_WARNINGS.append("SZSE quote fetch called without target ETF codes; using capped legacy bulk query.")
        LOGGER.warning("szse quotes called without target codes; using capped legacy bulk query")
        return pd.DataFrame(_fetch_quote_history_query(client, start_date, end_date, max_pages=max_pages, **options))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    page_limit = 20 if max_pages is None else max(int(max_pages), 1)
    records: list[dict] = []
    worker_timeout = min(int(getattr(client, "timeout", 12)), 3)
    worker_retries = min(int(getattr(client, "retries", 3)), 1)
    LOGGER.info(
        "szse quotes start codes=%s window=%s..%s workers=%s timeout=%s retries=%s",
        len(code_list),
        start_date,
        end_date,
        max_workers,
        worker_timeout,
        worker_retries,
    )

    def fetch_one(code: str) -> list[dict]:
        # requests.Session is not thread-safe. Each worker owns its client so
        # concurrent SZSE code requests cannot block each other through shared
        # connection/session state.
        client_kwargs = {
            "timeout": worker_timeout,
            "retries": worker_retries,
            "sleep_seconds": getattr(client, "sleep_seconds", 0.5),
            "raw_latest_ttl_seconds": getattr(client, "raw_latest_ttl_seconds", 6 * 60 * 60),
        }
        if hasattr(client, "raw_dir"):
            client_kwargs["raw_dir"] = client.raw_dir
        worker_client = OfficialClient(**client_kwargs)
        try:
            return _fetch_quote_history_query(
                worker_client,
                start_date,
                end_date,
                code=code,
                max_pages=page_limit,
                **options,
            )
        finally:
            try:
                worker_client.session.close()
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max(int(max_workers), 1)) as executor:
        futures = {executor.submit(fetch_one, code): code for code in code_list}
        completed = 0
        for future in as_completed(futures):
            code = futures[future]
            completed += 1
            try:
                records.extend(future.result())
            except Exception as exc:
                LAST_WARNINGS.append(f"SZSE quote fetch failed for {code}: {exc}")
                records.append({"etf_code": code, "validation_status": "quote_fetch_failed"})
            if completed == len(code_list) or completed % 25 == 0:
                LOGGER.info("szse quotes progress %s/%s latest_code=%s records=%s", completed, len(code_list), code, len(records))
    LOGGER.info("szse quotes finished codes=%s records=%s", len(code_list), len(records))
    return pd.DataFrame(records)
