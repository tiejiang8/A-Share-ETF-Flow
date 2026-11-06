from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse
from weakref import WeakValueDictionary

import pandas as pd
import requests

from ._atomic import atomic_replace


def _project_paths(package_dir: Path) -> tuple[Path, Path]:
    """Use the checkout or an explicit writable workspace, never site-packages."""
    source_root = package_dir.parents[1]
    is_checkout = (source_root / "src" / "etf_temperature").resolve() == package_dir.resolve() and (source_root / "pyproject.toml").is_file()
    workspace = os.environ.get("ETF_FLOW_HOME")
    root = Path(workspace).expanduser().resolve() if workspace else (source_root if is_checkout else Path.cwd())
    config_dir = root / "config"
    if not config_dir.is_dir():
        config_dir = package_dir / "_default_config"
    return root, config_dir


ROOT, CONFIG_DIR = _project_paths(Path(__file__).resolve().parent)
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
OUTPUTS_DIR = ROOT / "outputs"
DOCS_DIR = ROOT / "docs"


OFFICIAL_ENDPOINTS: list[dict[str, str]] = [
    {
        "exchange": "SSE",
        "name": "ETF stock list",
        "method": "GET",
        "url": "https://query.sse.com.cn/commonSoaQuery.do",
        "params": "sqlId=FUND_LIST&fundType=00",
        "usage": "上交所股票ETF清单，含代码、简称、管理人、指数代码、指数名称、上市日期。",
    },
    {
        "exchange": "SSE",
        "name": "ETF share scale",
        "method": "GET",
        "url": "https://query.sse.com.cn/commonQuery.do",
        "params": "sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE=YYYY-MM-DD",
        "usage": "上交所ETF指定日期总份额，单位为万份。",
    },
    {
        "exchange": "SSE",
        "name": "ETF historical K line",
        "method": "GET",
        "url": "https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}",
        "params": "select=date,open,high,low,close,volume,amount&begin=-20&end=-1",
        "usage": "上交所单只ETF日K线，金额单位为元。",
    },
    {
        "exchange": "SSE",
        "name": "ETF creation redemption basket",
        "method": "GET",
        "url": "https://query.sse.com.cn/commonQuery.do",
        "params": "sqlId=COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C&FUNDID2={code}",
        "usage": "上交所ETF申购赎回清单，取上一交易日单位净值NAV。",
    },
    {
        "exchange": "SZSE",
        "name": "ETF list and latest NAV",
        "method": "GET",
        "url": "https://fund.szse.cn/api/report/ShowReport",
        "params": "SHOWTYPE=xlsx&CATALOGID=fund_etf|1945&TABKEY=tab1|tab2",
        "usage": "深交所ETF列表及最新净值官方XLSX整表；各一次请求，JSON分页仅作失败回退。",
    },
    {
        "exchange": "SZSE",
        "name": "ETF share scale",
        "method": "GET",
        "url": "https://fund.szse.cn/api/report/ShowReport",
        "params": "SHOWTYPE=xlsx&CATALOGID=scsj_fund_jjgm&TABKEY=tab1&jjlb=ETF&txtStart=YYYY-MM-DD&txtEnd=YYYY-MM-DD",
        "usage": "深交所ETF历史基金规模官方XLSX，单位为份；按自然季度分块，每季度一次请求，JSON分页仅作回退。",
    },
    {
        "exchange": "SZSE",
        "name": "Fund historical quote",
        "method": "GET",
        "url": "https://www.szse.cn/api/report/ShowReport/data",
        "params": "SHOWTYPE=JSON&CATALOGID=1815_stock_snapshot&TABKEY=tab2&loading=first&txtBeginDate=YYYY-MM-DD&txtEndDate=YYYY-MM-DD&PAGENO=n",
        "usage": "深交所基金历史行情，含前收、开盘、最高、最低、收盘、成交量、成交额；程序优先访问HTTPS，失败时自动回退HTTP同路径。",
    },
    {
        "exchange": "CNINFO",
        "name": "CNINFO market data SDK",
        "method": "Playwright/NSDK",
        "url": "https://webapi.cninfo.com.cn/",
        "params": "SDK_REQUEST_KLINE; code.SE; Date/Open/High/Low/Close/Volume/Amount",
        "usage": "深圳证券信息/巨潮深证信数据服务平台浏览器行情SDK；交易所行情接口不可用时补齐ETF日K线。",
    },
    {
        "exchange": "CSRC-EID",
        "name": "Public fund NAV",
        "method": "GET/POST",
        "url": "http://eid.csrc.gov.cn/fund/disclose/getPublicFundJZInfoMore.do",
        "params": "aoData=DataTables JSON; fundCode={code}; startDate=endDate=YYYY-MM-DD",
        "usage": "中国证监会基金电子披露网站公募基金净值信息，可作为交易所净值缺口的官方回退源。",
    },
    {
        "exchange": "CSRC-EID",
        "name": "Public fund announcements",
        "method": "GET",
        "url": "http://eid.csrc.gov.cn/fund/disclose/advanced_search_report.do",
        "params": "aoData=DataTables JSON; reportType/fundCode/date filters",
        "usage": "中国证监会基金电子披露网站公告查询，用于公司行为、临时公告和净值日报证据链。",
    },
    {
        "exchange": "CNINFO",
        "name": "Fund disclosure portal",
        "method": "Browser/API",
        "url": "https://www.cninfo.com.cn/",
        "params": "fund/disclosure search filters",
        "usage": "巨潮资讯网基金公告入口，适合深市ETF公告、申购赎回相关披露和公司行为交叉验证。",
    },
    {
        "exchange": "FundCo",
        "name": "Fund manager ETF PCF/NAV pages",
        "method": "Browser/API",
        "url": "基金管理人官网ETF专区",
        "params": "fundCode/date",
        "usage": "基金公司官网ETF申购赎回清单、净值和产品公告；按管理人补齐，不作为全市场行情主源。",
    },
]


def ensure_dirs() -> None:
    for path in [
        CONFIG_DIR,
        DATA_DIR,
        RAW_DIR,
        NORMALIZED_DIR,
        DATA_DIR / "cache",
        OUTPUTS_DIR,
        DOCS_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def normalize_date(value: str | int | date | datetime | None) -> str | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def ymd(value: str | int | date | datetime | None) -> str | None:
    norm = normalize_date(value)
    return None if norm is None else norm.replace("-", "")


def parse_json_or_jsonp(text: str) -> Any:
    text = text.strip()
    if not text:
        return {}
    if text[0] in "[{":
        return json.loads(text)
    match = re.match(r"^[\w$]+\((.*)\)\s*;?$", text, flags=re.S)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"Unsupported JSON/JSONP payload prefix: {text[:80]}")


def strip_html(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_first_code(value: Any) -> str:
    text = html.unescape(str(value or ""))
    match = re.search(r"\b(\d{6})\b", text)
    return match.group(1) if match else ""


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace(",", "").replace("￥", "").replace("%", "").strip()
    if text in {"", "-", "--", "nan", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None


def safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def last_calendar_days(end_date: str, days: int = 20) -> list[str]:
    end = datetime.strptime(normalize_date(end_date) or "", "%Y-%m-%d").date()
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def output_date_dir(report_date: str) -> Path:
    path = OUTPUTS_DIR / (normalize_date(report_date) or report_date)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        # Fail before replacing the last good artifact if the write is unreadable.
        pd.read_parquet(tmp, columns=[])
        atomic_replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_dataframe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


_RAW_RESPONSE_NAME = re.compile(r"\d{8}_\d{6}_\d{6}_(.*_[0-9a-f]{16}\.(?:txt|bin))")


def _raw_response_key(url: str, prefix: str, extension: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix).strip("_")[:80]
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{safe}_{digest}.{extension}"


class _RawResponseIndex:
    """Index the latest audited response per request across worker clients."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = Lock()
        self._mtime_ns: int | None = None
        self._names: dict[str, str] = {}

    def _latest_path(self, key: str) -> Path | None:
        # A changed directory stamp also picks up files added or removed by another process.
        mtime_ns = self.directory.stat().st_mtime_ns
        if self._mtime_ns != mtime_ns:
            names: dict[str, str] = {}
            with os.scandir(self.directory) as entries:
                for entry in entries:
                    match = _RAW_RESPONSE_NAME.fullmatch(entry.name)
                    if match:
                        cache_key = os.path.normcase(match[1])
                        if entry.name > names.get(cache_key, ""):
                            names[cache_key] = entry.name
            self._names = names
            self._mtime_ns = mtime_ns
        name = self._names.get(os.path.normcase(key))
        return self.directory / name if name else None

    def latest_path(self, key: str) -> Path | None:
        with self._lock:
            return self._latest_path(key)

    def save(
        self,
        key: str,
        payload: str | bytes,
        read_payload: Callable[[Path], str | bytes],
        write_payload: Callable[[Path], int],
        *,
        cache_not_before: float | None = None,
    ) -> Path:
        with self._lock:
            previous = self._latest_path(key)
            if previous:
                try:
                    if (
                        (cache_not_before is None or previous.stat().st_mtime >= cache_not_before)
                        and read_payload(previous) == payload
                    ):
                        return previous
                except (OSError, UnicodeError):
                    pass
            path = self.directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{key}"
            tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                write_payload(tmp)
                atomic_replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            if previous is None or path.name > previous.name:
                self._names[os.path.normcase(key)] = path.name
            self._mtime_ns = self.directory.stat().st_mtime_ns
            return path


_RAW_RESPONSE_INDEXES: WeakValueDictionary[Path, _RawResponseIndex] = WeakValueDictionary()
_RAW_RESPONSE_INDEXES_LOCK = Lock()


def _raw_response_index(directory: Path) -> _RawResponseIndex:
    directory = directory.resolve()
    with _RAW_RESPONSE_INDEXES_LOCK:
        index = _RAW_RESPONSE_INDEXES.get(directory)
        if index is None:
            index = _RawResponseIndex(directory)
            _RAW_RESPONSE_INDEXES[directory] = index
        return index


@dataclass
class OfficialClient:
    raw_dir: Path = RAW_DIR
    timeout: int = 12
    retries: int = 3
    sleep_seconds: float = 0.5
    raw_latest_ttl_seconds: int = 6 * 60 * 60

    def __post_init__(self) -> None:
        ensure_dirs()
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._raw_index = _raw_response_index(self.raw_dir)
        if not hasattr(self, "browser_fallback_urls"):
            self.browser_fallback_urls: list[str] = []
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
                "Accept": "application/json,text/javascript,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )

    @staticmethod
    def _read_raw_payload(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        return text.split("\n\n", 1)[1] if "\n\n" in text else text

    def _raw_cache_ttl(self, url: str) -> int:
        """Return -1 for immutable historical requests, otherwise a short TTL.

        Historical exchange requests with an explicit end/stat date before today
        can be cached permanently. Endpoints representing "latest" data remain
        refreshable and therefore use raw_latest_ttl_seconds.
        """

        try:
            params = parse_qs(urlparse(url).query)
            candidates: list[str] = []
            for key in ("txtEndDate", "txtEnd", "STAT_DATE", "endDate"):
                candidates.extend(params.get(key, []))
            today = datetime.now().date()
            for value in candidates:
                norm = normalize_date(value)
                if norm and datetime.strptime(norm, "%Y-%m-%d").date() < today:
                    return -1
        except Exception:
            pass
        return max(int(self.raw_latest_ttl_seconds), 0)

    def get_text(
        self,
        url: str,
        *,
        referer: str | None = None,
        raw_prefix: str = "request",
        force_refresh: bool = False,
        cache_ttl_seconds: int | None = None,
        cache_not_before: float | None = None,
    ) -> str:
        headers = {"Referer": referer} if referer else None
        last_error: Exception | None = None
        key = _raw_response_key(url, raw_prefix, "txt")
        cached = self._raw_index.latest_path(key)
        ttl = self._raw_cache_ttl(url) if cache_ttl_seconds is None else int(cache_ttl_seconds)
        if cached and not force_refresh:
            try:
                captured = cached.stat().st_mtime
                age_seconds = max(time.time() - captured, 0)
                if (cache_not_before is None or captured >= cache_not_before) and (ttl < 0 or age_seconds <= ttl):
                    return self._read_raw_payload(cached)
            except FileNotFoundError:
                pass

        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                text = response.text
                self._save_raw(url, text, raw_prefix, cache_not_before=cache_not_before)
                return text
            except Exception as exc:  # pragma: no cover - retry branch
                last_error = exc
                try:
                    self.session.close()
                finally:
                    self.__post_init__()
                if attempt < self.retries:
                    time.sleep(self.sleep_seconds * attempt)
        cached = self._raw_index.latest_path(key)
        if cached:
            try:
                if cache_not_before is None or cached.stat().st_mtime >= cache_not_before:
                    return self._read_raw_payload(cached)
            except FileNotFoundError:
                pass
        try:
            text = self._get_text_with_playwright(url, referer=referer)
            self._save_raw(url, text, f"{raw_prefix}_playwright", cache_not_before=cache_not_before)
            self.browser_fallback_urls.append(url)
            return text
        except Exception:
            pass
        raise RuntimeError(f"GET failed after {self.retries} attempts: {url}") from last_error

    def _get_text_with_playwright(self, url: str, *, referer: str | None = None) -> str:
        from urllib.parse import urlparse

        from playwright.sync_api import sync_playwright

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        bootstrap = referer or origin
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
                locale="zh-CN",
                ignore_https_errors=True,
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
            )
            page = context.new_page()
            try:
                try:
                    page.goto(bootstrap, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    # If the page shell is unavailable, try the data URL directly.
                    response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    if response is None or response.status >= 400:
                        raise
                    return page.locator("body").inner_text(timeout=10000)
                result = page.evaluate(
                    """async ({ url }) => {
                        const res = await fetch(url, {
                          credentials: 'same-origin',
                          cache: 'no-store',
                          headers: { 'X-Requested-With': 'XMLHttpRequest' }
                        });
                        const text = await res.text();
                        return { status: res.status, ok: res.ok, text };
                    }""",
                    {"url": url},
                )
                if not result.get("ok"):
                    raise RuntimeError(f"Playwright fetch returned {result.get('status')}")
                return result.get("text") or ""
            finally:
                context.close()
                browser.close()

    def get_json(
        self,
        url: str,
        *,
        referer: str | None = None,
        raw_prefix: str = "request",
        cache_not_before: float | None = None,
    ) -> Any:
        options = {} if cache_not_before is None else {"cache_not_before": cache_not_before}
        return parse_json_or_jsonp(self.get_text(url, referer=referer, raw_prefix=raw_prefix, **options))

    def get_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        raw_prefix: str = "request",
        force_refresh: bool = False,
        cache_ttl_seconds: int | None = None,
    ) -> bytes:
        """Download a binary official artifact and reuse the auditable raw cache.

        Exchange XLSX exports are materially more efficient than JSON pagination.
        The URL digest in the cache filename and the normalized row ``source_url``
        together preserve provenance without altering the downloaded workbook.
        """

        headers = {"Referer": referer} if referer else None
        last_error: Exception | None = None
        key = _raw_response_key(url, raw_prefix, "bin")
        cached = self._raw_index.latest_path(key)
        ttl = self._raw_cache_ttl(url) if cache_ttl_seconds is None else int(cache_ttl_seconds)
        if cached and not force_refresh:
            try:
                age_seconds = max(time.time() - cached.stat().st_mtime, 0)
                if ttl < 0 or age_seconds <= ttl:
                    return cached.read_bytes()
            except FileNotFoundError:
                pass

        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                payload = response.content
                if not payload:
                    raise ValueError("empty binary response")
                self._save_raw_bytes(url, payload, raw_prefix)
                return payload
            except Exception as exc:  # pragma: no cover - retry branch
                last_error = exc
                try:
                    self.session.close()
                finally:
                    self.__post_init__()
                if attempt < self.retries:
                    time.sleep(self.sleep_seconds * attempt)
        cached = self._raw_index.latest_path(key)
        if cached:
            try:
                return cached.read_bytes()
            except FileNotFoundError:
                pass
        raise RuntimeError(f"GET binary failed after {self.retries} attempts: {url}") from last_error

    def _save_raw(
        self, url: str, text: str, prefix: str, *, cache_not_before: float | None = None,
    ) -> Path:
        """Save one auditable raw response without duplicating identical payloads."""

        return self._raw_index.save(
            _raw_response_key(url, prefix, "txt"),
            text,
            self._read_raw_payload,
            lambda path: path.write_text(f"URL: {url}\n\n{text}", encoding="utf-8"),
            cache_not_before=cache_not_before,
        )

    def _save_raw_bytes(self, url: str, payload: bytes, prefix: str) -> Path:
        return self._raw_index.save(
            _raw_response_key(url, prefix, "bin"),
            payload,
            Path.read_bytes,
            lambda path: path.write_bytes(payload),
        )


def endpoint_markdown() -> str:
    lines = [
        "# 官方接口探测记录",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 探测方式：Playwright 打开交易所动态页面并记录 XHR/fetch；随后用 requests 复现接口。",
        "- 约束：交易所官方接口为主数据源，第三方仅用于故障回退或交叉验证。",
        "",
        "| 来源 | 名称 | 方法 | URL | 参数 | 用途 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in OFFICIAL_ENDPOINTS:
        lines.append(
            f"| {item['exchange']} | {item['name']} | {item['method']} | "
            f"`{item['url']}` | `{item['params']}` | {item['usage']} |"
        )
    lines.append("")
    lines.append("## 重要口径")
    lines.append("")
    lines.append("- 成交额只表示二级市场换手、活跃度和分歧，不直接等同资金流入。")
    lines.append("- 资金流用份额变化乘以统计日单位净值估算，并在报告中标注非精确现金流。")
    lines.append("- 一个 ETF 只能有一个主集群，辅助标签不参与重复聚合。")
    return "\n".join(lines) + "\n"


def write_probe_outputs(extra: dict[str, Any] | None = None, *, update_reference: bool = True) -> None:
    ensure_dirs()
    md = endpoint_markdown()
    if update_reference:
        (DOCS_DIR / "official_endpoints.md").write_text(md, encoding="utf-8")
    (OUTPUTS_DIR / "source_probe.md").write_text(md, encoding="utf-8")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "probe_method": "playwright_network_then_requests_replay",
        "endpoints": OFFICIAL_ENDPOINTS,
    }
    if extra:
        payload.update(extra)
    (OUTPUTS_DIR / "source_probe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def concat_non_empty(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    useful = [df for df in frames if df is not None and not df.empty]
    return pd.concat(useful, ignore_index=True) if useful else pd.DataFrame()
