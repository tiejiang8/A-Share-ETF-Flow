from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd

from .official_endpoints import normalize_date, ymd
from .quote_freshness import MARKET_TIMEZONE


CNINFO_WEBAPI_HOME = "https://webapi.cninfo.com.cn/"
LAST_WARNINGS: list[str] = []


def _market_code(code: str, exchange: str | None = None) -> str:
    digits = str(code).strip().zfill(6)
    exchange_norm = str(exchange or "").upper()
    if exchange_norm == "SSE":
        return f"sh{digits}"
    if exchange_norm == "SZSE":
        return f"sz{digits}"
    if digits.startswith(("51", "56", "58")):
        return f"sh{digits}"
    return f"sz{digits}"


def _input_rows(codes: Iterable[str] | pd.DataFrame, exchange: str | None = None) -> list[dict[str, str]]:
    if isinstance(codes, pd.DataFrame):
        rows = []
        for _, row in codes.iterrows():
            code = str(row.get("etf_code") or row.get("code") or "").strip().zfill(6)
            if code.strip("0"):
                rows.append({"code": code, "exchange": str(row.get("exchange") or exchange or "")})
        return rows
    return [{"code": str(code).strip().zfill(6), "exchange": str(exchange or "")} for code in codes if str(code).strip()]


def _records_from_payload(payload: list[dict], start_date: str, end_date: str) -> list[dict]:
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    now = datetime.now(MARKET_TIMEZONE).replace(tzinfo=None).isoformat(timespec="seconds")
    records: list[dict] = []
    for item in payload:
        code = str(item.get("code") or "").zfill(6)
        values = item.get("data") or {}
        dates = values.get("Date") or []
        opens = values.get("Open") or []
        highs = values.get("High") or []
        lows = values.get("Low") or []
        closes = values.get("Close") or []
        volumes = values.get("Volume") or []
        amounts = values.get("Amount") or []
        prev_close = None
        for idx, raw_date in enumerate(dates):
            d = normalize_date(str(raw_date))
            if not d or (start and d < start) or (end and d > end):
                continue
            close = _at(closes, idx)
            records.append(
                {
                    "etf_code": code,
                    "date": d,
                    "open": _at(opens, idx),
                    "high": _at(highs, idx),
                    "low": _at(lows, idx),
                    "close": close,
                    "prev_close": prev_close,
                    "pct_change": None if prev_close in (None, 0) or close is None else (close / prev_close - 1) * 100,
                    "volume": _at(volumes, idx),
                    "amount": _at(amounts, idx),
                    "source": "cninfo_nsdk_official:kline",
                    "source_priority": 2,
                    "source_url": CNINFO_WEBAPI_HOME,
                    "retrieved_at": now,
                    "data_date": d,
                    "is_official": True,
                    "validation_status": "official_market_sdk_fallback",
                }
            )
            if close is not None:
                prev_close = close
    return records


def _next_weekday(value: str) -> str:
    date_text = normalize_date(value)
    if not date_text:
        return value
    current = datetime.strptime(date_text, "%Y-%m-%d").date()
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def _at(values: list, idx: int) -> float | None:
    if idx >= len(values):
        return None
    value = values[idx]
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_quote_history(
    codes: Iterable[str] | pd.DataFrame,
    start_date: str,
    end_date: str,
    *,
    per_code_timeout_ms: int = 12000,
) -> pd.DataFrame:
    """Fetch daily K-line data through CNINFO's browser market-data SDK.

    The browser SDK's formatter defaults ETF codes beginning with ``1`` to SH,
    so callers must pass an exchange or use the explicit sz/sh prefix generated
    here.
    """

    LAST_WARNINGS.clear()
    rows = _input_rows(codes)
    if not rows:
        return pd.DataFrame()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment guard
        LAST_WARNINGS.append(f"CNINFO NSDK fallback unavailable: playwright import failed: {exc}")
        return pd.DataFrame()

    request_rows = [
        {
            "code": row["code"],
            "marketCode": _market_code(row["code"], row.get("exchange")),
        }
        for row in rows
    ]
    sdk_start_date = _next_weekday(start_date)
    start_ymd = ymd(sdk_start_date) or ""
    end_ymd = ymd(end_date) or ""
    payload: list[dict] = []
    errors: list[str] = []
    script = """
async ({ rows, startYmd, endYmd, perCodeTimeoutMs }) => {
  async function one(row) {
    return await new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ code: row.code, timeout: true }), perCodeTimeoutMs);
      try {
        const sdkCode = window.NSDKWrap.codeFormatterToList(row.marketCode, "code.SE")[0];
        window.NSDKWrap.createRequest({
          api: SDK_REQUEST_KLINE,
          setCode: sdkCode,
          setPeriod: "day",
          setCqMode: "none",
          setDateRange: [startYmd, endYmd],
          setFields: "Date,Open,High,Low,Close,Volume,Amount",
          setSubscribe: false,
          request: "",
          resultFormatter: (x) => x,
          onResult: (formatted) => {
            clearTimeout(timer);
            resolve({ code: row.code, sdkCode, data: formatted || {} });
          },
        });
      } catch (error) {
        clearTimeout(timer);
        resolve({ code: row.code, error: String(error && error.message ? error.message : error) });
      }
    });
  }
  async function pass(inputRows) {
    const out = [];
    for (const row of inputRows) out.push(await one(row));
    return out;
  }
  let out = await pass(rows);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const missing = out
      .filter((item) => !(item.data && Array.isArray(item.data.Date) && item.data.Date.length))
      .map((item) => rows.find((row) => row.code === item.code))
      .filter(Boolean);
    if (!missing.length) break;
    await new Promise((resolve) => setTimeout(resolve, 300));
    const retry = await pass(missing);
    const byCode = Object.fromEntries(retry.map((item) => [item.code, item]));
    out = out.map((item) => {
      const next = byCode[item.code];
      return next && next.data && Array.isArray(next.data.Date) && next.data.Date.length ? next : item;
    });
  }
  return out;
}
"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                locale="zh-CN",
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                page.goto(CNINFO_WEBAPI_HOME, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_function("() => window.NSDKWrap && window.NSDK", timeout=20000)
                payload = page.evaluate(
                    script,
                    {
                        "rows": request_rows,
                        "startYmd": start_ymd,
                        "endYmd": end_ymd,
                        "perCodeTimeoutMs": per_code_timeout_ms,
                    },
                )
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        LAST_WARNINGS.append(f"CNINFO NSDK fallback failed: {exc}")
        return pd.DataFrame()

    for item in payload:
        if item.get("timeout"):
            errors.append(f"{item.get('code')} timeout")
        elif item.get("error"):
            errors.append(f"{item.get('code')} {item.get('error')}")
    if errors:
        LAST_WARNINGS.append(f"CNINFO NSDK partial failures: {len(errors)}; examples: {errors[:5]}")
    records = _records_from_payload(payload, start_date, end_date)
    return pd.DataFrame(records)
