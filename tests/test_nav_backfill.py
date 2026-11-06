from __future__ import annotations

import pandas as pd

from etf_temperature import csrc_eid
from etf_temperature.indicators import build_etf_daily


def test_fetch_nav_history_uses_bulk_pages(monkeypatch):
    monkeypatch.setattr(csrc_eid, "_warm_up", lambda client: "2026-07-10")

    pages = {
        0: {
            "iTotalRecords": 3,
            "iTotalDisplayRecords": 3,
            "aaData": [
                {"idStr": 1, "code": "510300", "shareNetValue": "4.80", "valuationDate": "2026-07-06"},
                {"idStr": 2, "code": "159915", "shareNetValue": "2.10", "valuationDate": "2026-07-06"},
            ],
        },
        2: {
            "iTotalRecords": 3,
            "iTotalDisplayRecords": 3,
            "aaData": [
                {"idStr": 3, "code": "000001", "shareNetValue": "1.00", "valuationDate": "2026-07-06"},
            ],
        },
    }

    def fake_page(client, valuation_date, *, display_start, display_length):
        assert valuation_date == "2026-07-06"
        assert display_length == 2
        return pages[display_start]

    monkeypatch.setattr(csrc_eid, "_fetch_nav_page", fake_page)

    result = csrc_eid.fetch_nav_history(
        object(),
        ["510300", "159915"],
        "2026-07-06",
        "2026-07-06",
        page_size=2,
        sleep_seconds=0,
        fallback_per_code=False,
    )

    assert result[["date", "etf_code"]].values.tolist() == [
        ["2026-07-06", "159915"],
        ["2026-07-06", "510300"],
    ]
    assert result["nav"].tolist() == [2.1, 4.8]


def test_fetch_nav_history_accepts_discovered_trading_dates(monkeypatch):
    monkeypatch.setattr(csrc_eid, "_warm_up", lambda client: "2026-07-10")
    requested = []

    def fake_page(client, valuation_date, *, display_start, display_length):
        requested.append(valuation_date)
        return {
            "iTotalRecords": 1,
            "iTotalDisplayRecords": 1,
            "aaData": [
                {
                    "idStr": valuation_date,
                    "code": "510300",
                    "shareNetValue": "4.80",
                    "valuationDate": valuation_date,
                }
            ],
        }

    monkeypatch.setattr(csrc_eid, "_fetch_nav_page", fake_page)
    result = csrc_eid.fetch_nav_history(
        object(),
        ["510300"],
        "2026-07-06",
        "2026-07-10",
        sleep_seconds=0,
        fallback_per_code=False,
        valuation_dates=["2026-07-06", "2026-07-08"],
    )

    assert requested == ["2026-07-06", "2026-07-08"]
    assert result["date"].tolist() == ["2026-07-06", "2026-07-08"]


def test_fetch_nav_history_can_filter_bulk_pages_to_etfs(monkeypatch):
    captured = []
    monkeypatch.setattr(csrc_eid, "_warm_up", lambda client: "2026-07-17")

    def fake_page(client, valuation_date, *, display_start, display_length, fund_name=""):
        captured.append(fund_name)
        return {"aaData": [], "iTotalDisplayRecords": 0}

    monkeypatch.setattr(csrc_eid, "_fetch_nav_page", fake_page)
    csrc_eid.fetch_nav_history(
        object(),
        ["510300"],
        "2026-07-17",
        "2026-07-17",
        fallback_per_code=False,
        fund_name_filter="ETF",
    )

    assert captured == ["ETF"]


def test_fetch_nav_gaps_only_requests_selected_date_codes(monkeypatch):
    calls = []
    monkeypatch.setattr(csrc_eid, "_warm_up", lambda client: "2026-07-17")

    def fake_one(client, code, valuation_date):
        calls.append((valuation_date, code))
        return {
            "etf_code": code,
            "date": valuation_date,
            "nav": 1.0,
            "source_priority": 2,
        }

    monkeypatch.setattr(csrc_eid, "_fetch_nav_one", fake_one)
    result = csrc_eid.fetch_nav_gaps(
        object(),
        {"20260717": ["510300", "510300", "159919"]},
        sleep_seconds=0,
    )

    assert calls == [("2026-07-17", "159919"), ("2026-07-17", "510300")]
    assert len(result) == 2


def test_daily_bulk_nav_falls_back_only_for_missing_same_date_codes(monkeypatch):
    day = "2026-11-05"
    monkeypatch.setattr(csrc_eid, "DAILY_NAV_BULK_MIN_CODES", 2)
    monkeypatch.setattr(csrc_eid, "_warm_up", lambda client: day)

    def page(client, valuation_date, **options):
        assert valuation_date == day and options["fund_name"] == "ETF"
        return {"aaData": [
            {"code": "510300", "shareNetValue": "4.80", "valuationDate": day},
            {"code": "159915", "shareNetValue": "2.00", "valuationDate": "2026-11-04"},
        ]}

    requested = []

    def fetch_one(client, code, valuation_date):
        requested.append((code, valuation_date))
        return {"etf_code": code, "date": valuation_date, "nav": 2.1}

    monkeypatch.setattr(csrc_eid, "_fetch_nav_page", page)
    monkeypatch.setattr(csrc_eid, "_fetch_nav_one", fetch_one)
    rows = csrc_eid.fetch_nav_latest(
        object(), ["510300", "159915"], day, sleep_seconds=0, prefer_bulk=True,
    )
    assert requested == [("159915", day)]
    assert rows.set_index("etf_code")["nav"].to_dict() == {"510300": 4.8, "159915": 2.1}


def test_malformed_daily_bulk_page_uses_existing_single_code_path(monkeypatch):
    day = "2026-11-05"
    monkeypatch.setattr(csrc_eid, "DAILY_NAV_BULK_MIN_CODES", 1)
    monkeypatch.setattr(csrc_eid, "_warm_up", lambda client: day)
    monkeypatch.setattr(csrc_eid, "_fetch_nav_page", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        csrc_eid, "_fetch_nav_one",
        lambda client, code, valuation_date: {"etf_code": code, "date": valuation_date, "nav": 1.0},
    )

    result = csrc_eid.fetch_nav_latest(object(), ["510300"], day, sleep_seconds=0, prefer_bulk=True)

    assert result[["etf_code", "date", "nav"]].values.tolist() == [["510300", day, 1.0]]
    assert any("格式无效" in warning for warning in csrc_eid.LAST_WARNINGS)


def test_build_etf_daily_never_uses_future_nav():
    universe = pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "etf_name": "沪深300ETF",
                "is_a_share_equity_etf": True,
            }
        ]
    )
    membership = pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "primary_cluster": "large_cap",
                "primary_cluster_name": "大盘",
                "secondary_tags": "",
                "classification_basis": "test",
                "confidence": 1.0,
            }
        ]
    )
    quotes = pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "date": "2026-07-06",
                "open": 4.7,
                "high": 4.9,
                "low": 4.6,
                "close": 4.8,
                "volume": 1,
                "amount": 1,
                "source": "test",
                "data_date": "2026-07-06",
                "validation_status": "ok",
            }
        ]
    )
    shares = pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "date": "2026-07-06",
                "shares": 100,
                "source": "test",
                "data_date": "2026-07-06",
                "validation_status": "ok",
            }
        ]
    )
    nav = pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "date": "2026-07-06",
                "nav": 4.8,
                "source_priority": 2,
                "source": "historical",
                "data_date": "2026-07-06",
                "validation_status": "ok",
            },
            {
                "etf_code": "510300",
                "date": "2026-07-10",
                "nav": 5.2,
                "source_priority": 1,
                "source": "future",
                "data_date": "2026-07-10",
                "validation_status": "ok",
            },
        ]
    )

    result = build_etf_daily(universe, membership, quotes, shares, nav, "2026-07-06")
    assert result.loc[0, "nav"] == 4.8
    assert result.loc[0, "nav_data_date"] == "2026-07-06"
