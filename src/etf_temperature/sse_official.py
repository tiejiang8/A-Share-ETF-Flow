from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from .official_endpoints import OfficialClient, normalize_date, to_float, ymd


SSE_REFERER_LIST = "https://www.sse.com.cn/assortment/fund/etf/list/"
SSE_REFERER_FUND = "https://www.sse.com.cn/assortment/fund/list/etfinfo/basic/index.shtml"
LOGGER = logging.getLogger(__name__)


def fetch_stock_etf_universe(client: OfficialClient) -> pd.DataFrame:
    url = (
        "https://query.sse.com.cn/commonSoaQuery.do?"
        "isPagination=true&pageHelp.pageSize=3000&pageHelp.pageNo=1&pageHelp.beginPage=1&"
        "pageHelp.cacheSize=1&pageHelp.endPage=1&pagecache=false&sqlId=FUND_LIST&fundType=00"
    )
    data = client.get_json(url, referer=SSE_REFERER_LIST, raw_prefix="sse_fund_list")
    rows = data.get("result") or data.get("pageHelp", {}).get("data") or []
    now = datetime.now().isoformat(timespec="seconds")
    records: list[dict] = []
    for row in rows:
        code = str(row.get("fundCode") or row.get("FUND_CODE") or "").zfill(6)
        if not code.strip("0"):
            continue
        records.append(
            {
                "etf_code": code,
                "etf_name": row.get("fundAbbr") or row.get("FUND_ABBR") or "",
                "expanded_name": row.get("secNameFull") or row.get("FUND_EXPANSION_ABBR") or "",
                "exchange": "SSE",
                "manager": row.get("companyName") or row.get("COMPANY_NAME") or "",
                "etf_type": "stock_etf",
                "sub_class": str(row.get("subClass") or row.get("SUB_CLASS") or ""),
                "tracking_index_code": str(row.get("INDEX_CODE") or "").strip(),
                "tracking_index_name": row.get("INDEX_NAME") or "",
                "listing_date": normalize_date(row.get("listingDate") or row.get("LISTING_DATE")),
                "source": "sse_official:FUND_LIST",
                "source_priority": 1,
                "source_url": "https://query.sse.com.cn/commonSoaQuery.do?sqlId=FUND_LIST&fundType=00",
                "retrieved_at": now,
                "is_official": True,
                "validation_status": "raw",
            }
        )
    return pd.DataFrame(records)


def fetch_share_for_date(client: OfficialClient, trade_date: str) -> pd.DataFrame:
    date_norm = normalize_date(trade_date)
    url = (
        "https://query.sse.com.cn/commonQuery.do?"
        "isPagination=true&pageHelp.pageSize=5000&pageHelp.pageNo=1&pageHelp.beginPage=1&"
        "pageHelp.cacheSize=1&pageHelp.endPage=1&"
        "sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&"
        f"STAT_DATE={quote(date_norm or '')}"
    )
    data = client.get_json(url, referer="https://www.sse.com.cn/market/funddata/volumn/etfvolumn/", raw_prefix=f"sse_share_{date_norm}")
    rows = data.get("result") or data.get("pageHelp", {}).get("data") or []
    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for row in rows:
        code = str(row.get("SEC_CODE") or "").zfill(6)
        shares_10k = to_float(row.get("TOT_VOL"))
        if not code.strip("0") or shares_10k is None:
            continue
        out.append(
            {
                "etf_code": code,
                "date": normalize_date(row.get("STAT_DATE")) or date_norm,
                "share_10k": shares_10k,
                "shares": shares_10k * 10000,
                "source": "sse_official:ETF规模",
                "source_priority": 1,
                "source_url": "https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
                "retrieved_at": now,
                "data_date": normalize_date(row.get("STAT_DATE")) or date_norm,
                "is_official": True,
                "validation_status": "ok",
            }
        )
    return pd.DataFrame(out)


def fetch_share_history(client: OfficialClient, dates: list[str]) -> pd.DataFrame:
    LOGGER.info("sse shares start dates=%s", len(dates))
    frames = []
    completed = 0
    for d in dates:
        try:
            df = fetch_share_for_date(client, d)
            if not df.empty:
                frames.append(df)
            completed += 1
            LOGGER.info("sse shares date=%s rows=%s progress=%s/%s", d, len(df), completed, len(dates))
        except Exception as exc:
            completed += 1
            LOGGER.warning("sse shares date=%s failed progress=%s/%s error=%s", d, completed, len(dates), exc)
            continue
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result = _topup_latest_share_date(client, result, dates, frames)
    LOGGER.info("sse shares finished rows=%s", len(result))
    return result


def _topup_latest_share_date(
    client: OfficialClient,
    result: pd.DataFrame,
    dates: list[str],
    frames: list[pd.DataFrame],
) -> pd.DataFrame:
    """Recover the newest session when the exact-date filter has not caught up.

    The SSE ``STAT_DATE`` filter lags the underlying report by one publication
    cycle: the newest session is already served by the unfiltered "latest" query
    (``STAT_DATE=``) while the same date still returns zero rows when addressed
    explicitly. Observed 2026-10-10 08:53: ``STAT_DATE=2026-10-09`` -> 0 rows,
    ``STAT_DATE=`` -> 920 rows all dated 2026-10-09. Mirrors the SZSE XLSX tail
    top-up so T+1 report runs are not silently truncated to T-1 shares.
    """
    covered = set(result["date"].astype(str)) if not result.empty else set()
    missing = [str(d) for d in dates if str(d) not in covered]
    if not missing:
        return result
    newest = max(missing)
    LOGGER.info("sse shares date=%s missing; latest top-up", newest)
    try:
        latest = fetch_share_for_date(client, "")
    except Exception as exc:  # pragma: no cover - defensive network guard
        LOGGER.warning("sse shares latest top-up failed error=%s", exc)
        return result
    if latest.empty:
        return result
    latest = latest[latest["date"].astype(str).isin(set(missing))]
    if latest.empty:
        return result
    frames.append(latest)
    merged = pd.concat(frames, ignore_index=True)
    return (
        merged.drop_duplicates(["etf_code", "date"], keep="last")
        .sort_values(["date", "etf_code"])
        .reset_index(drop=True)
    )


def _fetch_dayk_one(client: OfficialClient, code: str, points: int = 20) -> list[dict]:
    # SSE treats end=-1 as the current/latest bar and returns one fewer row than
    # abs(begin). Request one extra position so `points=2` includes T and T-1.
    api_points = max(int(points), 1) + 1
    url = (
        f"https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}?"
        "select=date,open,high,low,close,volume,amount"
        f"&begin=-{api_points}&end=-1"
    )
    data = client.get_json(url, referer=SSE_REFERER_FUND, raw_prefix=f"sse_dayk_{code}")
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in data.get("kline") or []:
        if len(item) < 7:
            continue
        rows.append(
            {
                "etf_code": code,
                "date": normalize_date(item[0]),
                "open": to_float(item[1]),
                "high": to_float(item[2]),
                "low": to_float(item[3]),
                "close": to_float(item[4]),
                "volume": to_float(item[5]),
                "amount": to_float(item[6]),
                "source": "sse_official:yunhq_dayk",
                "source_priority": 1,
                "source_url": f"https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}",
                "retrieved_at": now,
                "data_date": normalize_date(item[0]),
                "is_official": True,
                "validation_status": "ok",
            }
        )
    return rows


def fetch_quote_history(client: OfficialClient, codes: list[str], points: int = 20, max_workers: int = 10) -> pd.DataFrame:
    records: list[dict] = []
    code_list = [str(code).zfill(6) for code in codes if str(code).strip()]
    LOGGER.info("sse quotes start codes=%s points=%s workers=%s", len(code_list), points, max_workers)

    worker_timeout = min(int(getattr(client, "timeout", 12)), 8)
    worker_retries = min(int(getattr(client, "retries", 3)), 1)

    def fetch_one(code: str) -> list[dict]:
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
            return _fetch_dayk_one(worker_client, code, points)
        finally:
            try:
                worker_client.session.close()
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, code): code for code in code_list}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            try:
                records.extend(future.result())
            except Exception:
                code = futures[future]
                records.append({"etf_code": code, "validation_status": "quote_fetch_failed"})
            if completed == len(code_list) or completed % 25 == 0:
                LOGGER.info("sse quotes progress %s/%s records=%s", completed, len(code_list), len(records))
    LOGGER.info("sse quotes finished codes=%s records=%s", len(code_list), len(records))
    return pd.DataFrame(records)


def _fetch_nav_one(client: OfficialClient, code: str) -> dict:
    url = (
        "https://query.sse.com.cn/commonQuery.do?"
        f"jsonCallBack=jsonpCallback&isPagination=false&FUNDID2={code}&"
        "sqlId=COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"
    )
    data = client.get_json(url, referer=f"{SSE_REFERER_FUND}?FUNDID={code}", raw_prefix=f"sse_nav_{code}")
    row = (data.get("result") or [{}])[0]
    nav_date = normalize_date(row.get("PRE_TRADING_DAY") or row.get("TRADING_DAY"))
    return {
        "etf_code": code,
        "date": nav_date,
        "nav": to_float(row.get("NAV")),
        "nav_per_creation_unit": to_float(row.get("NAVPERCU")),
        "creation_redemption_unit": to_float(row.get("CREATION_REDEMPTION_UNIT")),
        "source": "sse_official:申购赎回清单",
        "source_priority": 1,
        "source_url": "https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C",
        "retrieved_at": datetime.now().isoformat(timespec="seconds"),
        "data_date": nav_date,
        "is_official": True,
        "validation_status": "ok" if to_float(row.get("NAV")) is not None else "nav_missing",
    }


def fetch_nav_latest(client: OfficialClient, codes: list[str], max_workers: int = 8) -> pd.DataFrame:
    records = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_nav_one, client, code): code for code in codes}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception:
                records.append(
                    {
                        "etf_code": futures[future],
                        "date": None,
                        "nav": None,
                        "source": "sse_official:申购赎回清单",
                        "source_priority": 1,
                        "is_official": True,
                        "validation_status": "nav_fetch_failed",
                    }
                )
    return pd.DataFrame(records)


def latest_share_date(client: OfficialClient) -> str | None:
    df = fetch_share_for_date(client, "")
    if df.empty:
        return None
    return sorted(df["date"].dropna().unique())[-1]
