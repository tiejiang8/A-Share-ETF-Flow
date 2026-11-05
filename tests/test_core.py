from io import BytesIO

import pandas as pd

from etf_temperature import sse_official, szse_official
from etf_temperature.cninfo_nsdk import _market_code, _next_weekday, _records_from_payload
from etf_temperature.aggregation import build_abnormal_pool, build_cluster_daily
from etf_temperature.classification import classify_universe, load_cluster_rules, validate_membership
from etf_temperature.indicators import build_etf_daily, compute_share_features
from etf_temperature.official_endpoints import parse_json_or_jsonp, strip_html
from etf_temperature.temperature import compute_product_temperature
from etf_temperature.universe import build_universe
from etf_temperature.validation import coverage_metrics


def sample_universe():
    sse = pd.DataFrame(
        [
            {
                "etf_code": "510050",
                "etf_name": "50ETF",
                "expanded_name": "上证50ETF华夏",
                "exchange": "SSE",
                "manager": "华夏基金",
                "etf_type": "stock_etf",
                "tracking_index_code": "000016",
                "tracking_index_name": "上证50指数",
            },
            {
                "etf_code": "512880",
                "etf_name": "证券ETF",
                "expanded_name": "证券ETF国泰",
                "exchange": "SSE",
                "manager": "国泰基金",
                "etf_type": "stock_etf",
                "tracking_index_code": "",
                "tracking_index_name": "中证全指证券公司指数",
            },
            {
                "etf_code": "518880",
                "etf_name": "黄金ETF",
                "expanded_name": "黄金ETF",
                "exchange": "SSE",
                "manager": "华安基金",
                "etf_type": "stock_etf",
                "tracking_index_code": "",
                "tracking_index_name": "黄金现货",
            },
            {
                "etf_code": "511880",
                "etf_name": "银华日利",
                "expanded_name": "银华日利",
                "exchange": "SSE",
                "manager": "银华基金",
                "etf_type": "stock_etf",
                "tracking_index_code": "",
                "tracking_index_name": "",
            },
        ]
    )
    szse = pd.DataFrame(
        [
            {
                "etf_code": "159995",
                "etf_name": "芯片ETF",
                "expanded_name": "芯片ETF",
                "exchange": "SZSE",
                "manager": "华夏基金",
                "etf_type": "etf",
                "tracking_index_code": "931865",
                "tracking_index_name": "国证半导体芯片指数",
            },
            {
                "etf_code": "159840",
                "etf_name": "锂电池ETF",
                "expanded_name": "锂电池ETF",
                "exchange": "SZSE",
                "manager": "工银基金",
                "etf_type": "etf",
                "tracking_index_code": "",
                "tracking_index_name": "中证新能源汽车电池指数",
            },
            {
                "etf_code": "159920",
                "etf_name": "恒生ETF",
                "expanded_name": "恒生ETF",
                "exchange": "SZSE",
                "manager": "华夏基金",
                "etf_type": "etf",
                "tracking_index_code": "",
                "tracking_index_name": "恒生指数",
            },
        ]
    )
    return build_universe(sse, szse)


def test_jsonp_and_html_helpers():
    assert parse_json_or_jsonp("cb({\"a\":1})")["a"] == 1
    assert parse_json_or_jsonp("[1,2]") == [1, 2]
    assert strip_html("<a><u>159001</u></a>") == "159001"


def test_cninfo_nsdk_market_code_keeps_szse_159_etfs():
    assert _market_code("159915", "SZSE") == "sz159915"
    assert _market_code("560000", "SSE") == "sh560000"
    assert _next_weekday("2026-05-17") == "2026-05-18"


def test_cninfo_nsdk_payload_records():
    payload = [
        {
            "code": "159915",
            "data": {
                "Date": [20260630, 20260701],
                "Open": [3.9, 4.0],
                "High": [4.1, 4.2],
                "Low": [3.8, 3.9],
                "Close": [4.0, 4.1],
                "Volume": [1000, 2000],
                "Amount": [3000, 5000],
            },
        }
    ]
    records = _records_from_payload(payload, "2026-07-01", "2026-07-01")
    assert len(records) == 1
    assert records[0]["etf_code"] == "159915"
    assert records[0]["date"] == "2026-07-01"
    assert records[0]["close"] == 4.1
    assert records[0]["amount"] == 5000


def test_szse_base_fallback_tries_fund_site_then_main_site():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_json(self, url, *, referer=None, raw_prefix="request"):
            self.calls.append((url, referer, raw_prefix))
            if url.startswith("https://fund.szse.cn"):
                raise RuntimeError("fund site unavailable")
            return [{"metadata": {"tabkey": "tab1", "pagecount": 1}, "data": []}]

    client = FakeClient()
    data, base = szse_official._get_json_with_base_fallback(
        client,
        "/api/report/ShowReport/data?CATALOGID=1945",
        referer_path="/marketdata/etf/index.html",
        raw_prefix="szse_test",
    )
    assert data[0]["metadata"]["tabkey"] == "tab1"
    assert base == "https://www.szse.cn"
    assert client.calls[0][0].startswith("https://fund.szse.cn")
    assert client.calls[1][0].startswith("https://www.szse.cn")


def test_szse_base_fallback_reuses_preferred_base_first():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_json(self, url, *, referer=None, raw_prefix="request"):
            self.calls.append((url, referer, raw_prefix))
            return [{"metadata": {"tabkey": "tab2", "pagecount": 1}, "data": []}]

    client = FakeClient()
    _, base = szse_official._get_json_with_base_fallback(
        client,
        "/api/report/ShowReport/data?CATALOGID=1945&TABKEY=tab2&PAGENO=2",
        referer_path="/marketdata/etf/index.html",
        raw_prefix="szse_nav_test",
        preferred_base="https://www.szse.cn",
    )

    assert base == "https://www.szse.cn"
    assert len(client.calls) == 1
    assert client.calls[0][0].startswith("https://www.szse.cn")


def test_szse_nav_fetch_uses_tab2_only_and_bounded_paging(monkeypatch):
    calls: list[tuple[str, str | None, str]] = []

    def fake_get_json_with_base_fallback(
        client,
        path_and_query,
        *,
        referer_path,
        raw_prefix,
        preferred_base=None,
    ):
        page = 1
        if "PAGENO=" in path_and_query:
            page = int(path_and_query.rsplit("PAGENO=", 1)[1])
        calls.append((path_and_query, preferred_base, raw_prefix))
        return (
            [
                {
                    "metadata": {"tabkey": "tab2", "pagecount": 5},
                    "data": [
                        {
                            "fund_code": f"159{page:03d}",
                            "nav_date": "2026-07-15",
                            "nav_per_share": f"1.00{page}",
                        }
                    ],
                }
            ],
            "https://fund.szse.cn",
        )

    monkeypatch.setattr(szse_official, "_get_json_with_base_fallback", fake_get_json_with_base_fallback)
    monkeypatch.setattr(szse_official.time, "sleep", lambda seconds: None)

    result = szse_official.fetch_nav_latest(
        object(),
        codes=["159001", "159004"],
        max_workers=2,
    )

    assert len(calls) == 5
    assert all("TABKEY=tab2" in path for path, _, _ in calls)
    assert all("szse_etf_list" not in raw_prefix for _, _, raw_prefix in calls)
    assert all(preferred == "https://fund.szse.cn" for _, preferred, _ in calls if preferred is not None)
    assert set(result["etf_code"]) == {"159001", "159004"}


def test_sse_dayk_requests_one_extra_position_for_t_minus_one():
    class FakeClient:
        def __init__(self):
            self.url = ""

        def get_json(self, url, *, referer=None, raw_prefix="request"):
            self.url = url
            return {"kline": [[20260715, 1, 1, 1, 1, 1, 1]]}

    client = FakeClient()
    rows = sse_official._fetch_dayk_one(client, "510050", points=2)

    assert "begin=-3&end=-1" in client.url
    assert rows[0]["date"] == "2026-07-15"


def test_szse_share_parser_strips_exchange_code_whitespace():
    class FakeClient:
        def get_json(self, url, *, referer=None, raw_prefix="request"):
            return [
                {
                    "metadata": {"tabkey": "tab1", "pagecount": 1},
                    "data": [
                        {
                            "fund_code": "159007  ",
                            "size_date": "2026-07-10",
                            "current_size": "19,660.32",
                        }
                    ],
                }
            ]

    shares = szse_official.fetch_share_history(FakeClient(), "2026-07-10", "2026-07-10")

    assert shares.iloc[0]["etf_code"] == "159007"
    assert shares.iloc[0]["shares"] == 196603200.0


def _xlsx_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False)
    return buffer.getvalue()


def test_szse_bulk_xlsx_list_nav_and_scale_date():
    list_payload = _xlsx_bytes(
        pd.DataFrame(
            [
                {
                    "证券代码": "159915",
                    "证券简称": "创业板ETF",
                    "拟合指数": "399006 创业板指",
                    "当前规模(份)": 123456789,
                    "基金管理人": "易方达基金",
                }
            ]
        )
    )
    nav_payload = _xlsx_bytes(
        pd.DataFrame(
            [{"净值日期": "2026-07-17", "基金代码": "159915", "基金简称": "创业板ETF", "单位基金净值": 2.34}]
        )
    )

    class FakeClient:
        def __init__(self):
            self.binary_calls = []

        def get_json(self, url, *, referer=None, raw_prefix="request"):
            return [{"metadata": {"subname": "2026-07-17"}, "data": []}]

        def get_bytes(self, url, *, referer=None, raw_prefix="request"):
            self.binary_calls.append(url)
            return nav_payload if "CATALOGID=1945" in url else list_payload

    client = FakeClient()
    universe, nav = szse_official.fetch_etf_list(client)

    assert len(client.binary_calls) == 2
    assert universe.iloc[0]["latest_share_10k_from_list"] == 12345.6789
    assert universe.iloc[0]["latest_share_date_from_list"] == "2026-07-17"
    assert nav.iloc[0]["nav"] == 2.34


def test_szse_share_xlsx_splits_calendar_quarters_and_keeps_share_units():
    calls = []

    class FakeClient:
        def get_bytes(self, url, *, referer=None, raw_prefix="request"):
            calls.append(url)
            date_text = "2026-03-31" if "txtEnd=2026-03-31" in url else "2026-04-01"
            return _xlsx_bytes(
                pd.DataFrame(
                    [{"日期": date_text, "基金代码": "159915", "基金简称": "创业板ETF", "基金规模(份)": 200000000}]
                )
            )

    shares = szse_official.fetch_share_history(FakeClient(), "2026-03-30", "2026-04-02")

    assert len(calls) == 2
    assert set(shares["date"]) == {"2026-03-31", "2026-04-01"}
    assert set(shares["shares"]) == {200000000.0}
    assert set(shares["share_10k"]) == {20000.0}


def test_szse_share_json_topup_when_xlsx_lags_end_date():
    class FakeClient:
        def get_bytes(self, url, *, referer=None, raw_prefix="request"):
            # Workbook only covers up to 2026-07-30 (weekend publication lag).
            return _xlsx_bytes(
                pd.DataFrame(
                    [{"日期": "2026-07-30", "基金代码": "159915", "基金简称": "创业板ETF", "基金规模(份)": 100000000}]
                )
            )

        def get_json(self, url, *, referer=None, raw_prefix="request"):
            assert "txtStart=2026-07-31" in url
            assert "txtEnd=2026-07-31" in url
            return [
                {
                    "metadata": {"tabkey": "tab1", "pagecount": 1},
                    "data": [
                        {
                            "fund_code": "159915",
                            "size_date": "2026-07-31",
                            "current_size": "20,000.00",
                        }
                    ],
                }
            ]

    shares = szse_official.fetch_share_history(FakeClient(), "2026-07-30", "2026-07-31")

    assert set(shares["date"]) == {"2026-07-30", "2026-07-31"}
    day31 = shares[shares["date"] == "2026-07-31"].iloc[0]
    assert day31["shares"] == 200000000.0
    assert day31["source"] == "szse_official:基金规模"


def test_a_share_filter_excludes_cross_asset():
    universe = sample_universe()
    included = set(universe.loc[universe["is_a_share_equity_etf"], "etf_code"])
    assert "518880" not in included
    assert "511880" not in included
    assert "159920" not in included
    assert {"510050", "512880", "159995", "159840"}.issubset(included)


def test_cluster_rules_have_36_members():
    assert len(load_cluster_rules()) == 36


def test_classification_single_primary_and_synonyms():
    membership = classify_universe(sample_universe())
    assert not membership.duplicated("etf_code").any()
    by_code = membership.set_index("etf_code")
    assert by_code.loc["512880", "primary_cluster_name"] == "券商/证券"
    assert by_code.loc["159995", "primary_cluster_name"] == "半导体/芯片"
    assert by_code.loc["159840", "primary_cluster_name"] == "电池/储能"


def test_share_change_features_do_not_sum_raw_cluster_shares():
    shares = pd.DataFrame(
        [
            {"etf_code": "510050", "date": "2026-06-24", "shares": 90},
            {"etf_code": "510050", "date": "2026-06-30", "shares": 95},
            {"etf_code": "510050", "date": "2026-07-01", "shares": 100},
        ]
    )
    out = compute_share_features(shares, "2026-07-01").iloc[0]
    assert out["share_daily_change"] == 5
    assert pd.isna(out["share_5d_change"]) or out["share_5d_change"] == 10


def test_daily_temperature_cluster_and_abnormal_pool():
    universe = sample_universe()
    membership = classify_universe(universe)
    quotes = pd.DataFrame(
        [
            {"etf_code": code, "date": "2026-06-30", "close": 1.0, "open": 1.0, "high": 1.0, "low": 1.0, "amount": 1000000, "volume": 1000}
            for code in ["510050", "512880", "159995", "159840"]
        ]
        + [
            {"etf_code": code, "date": "2026-07-01", "close": 1.1, "open": 1.0, "high": 1.2, "low": 0.9, "amount": 3000000, "volume": 2000}
            for code in ["510050", "512880", "159995", "159840"]
        ]
    )
    shares = pd.DataFrame(
        [
            {"etf_code": code, "date": "2026-06-30", "shares": 1000000}
            for code in ["510050", "512880", "159995", "159840"]
        ]
        + [
            {"etf_code": code, "date": "2026-07-01", "shares": 1100000}
            for code in ["510050", "512880", "159995", "159840"]
        ]
    )
    nav = pd.DataFrame(
        [{"etf_code": code, "date": "2026-07-01", "nav": 1.09} for code in ["510050", "512880", "159995", "159840"]]
        + [{"etf_code": "159995", "date": None, "nav": None, "source_priority": 1}]
    )
    daily = build_etf_daily(universe, membership, quotes, shares, nav, "2026-07-01")
    assert daily.set_index("etf_code").loc["159995", "nav"] == 1.09
    daily = compute_product_temperature(daily)
    assert daily["temperature"].between(0, 100).all()
    cluster = build_cluster_daily(daily)
    assert len(cluster) == 36
    pool = build_abnormal_pool(daily)
    assert not pool.empty
    metrics = coverage_metrics(universe, daily, membership)
    assert metrics["quote_coverage"] == 1.0
    assert metrics["share_coverage"] == 1.0
