from __future__ import annotations

import math

import pandas as pd

from etf_temperature.fund_company_official import (
    implied_share_cache_rows,
    parse_efunds_product_page,
    validate_implied_shares,
)
from etf_temperature.indicators import build_etf_daily


def _efunds_html(*, aum_date: str = "2026-07-17", nav_date: str = "2026-07-17") -> str:
    return f"""
    <td id="baseinfoAsset">数据截至{aum_date}：44,943,486,647.66元</td>
    <span class="nav-update">{nav_date}</span>
    <div id="net-today">3.4507</div>
    """


def test_parse_efunds_same_date_aum_and_nav() -> None:
    aum, nav = parse_efunds_product_page(_efunds_html(), "159915")

    assert aum is not None and nav is not None
    assert aum["date"] == "2026-07-17"
    assert aum["aum"] == 44943486647.66
    assert math.isclose(aum["implied_shares"], 44943486647.66 / 3.4507)
    assert aum["validation_status"] == "official_same_date_aum_nav"
    assert nav["nav"] == 3.4507


def test_parse_efunds_date_mismatch_never_derives_shares() -> None:
    aum, nav = parse_efunds_product_page(
        _efunds_html(nav_date="2026-07-16"),
        "159915",
    )

    assert aum is not None and nav is not None
    assert aum["implied_shares"] is None
    assert aum["validation_status"] == "aum_nav_date_mismatch"


def test_current_official_list_validates_formula_but_not_share_date() -> None:
    aum_rows = []
    evidence_rows = []
    for index in range(6):
        code = f"159{index:03d}"
        shares = 100_000_000 + index * 1_000_000
        aum_rows.append(
            {
                "etf_code": code,
                "date": "2026-07-17",
                "aum": shares * 2.0,
                "nav": 2.0,
                "implied_shares": shares,
                "source": "fund_company_official:efunds:aum",
                "source_url": f"https://example.invalid/{code}",
                "retrieved_at": "2026-07-18T08:00:00",
                "evidence_sha256": "a" * 64,
            }
        )
        evidence_rows.append(
            {
                "etf_code": code,
                "current_list_shares": shares + 1,
                "evidence_retrieved_date": "2026-07-18",
            }
        )

    audit, summary = validate_implied_shares(
        pd.DataFrame(aum_rows),
        pd.DataFrame(),
        current_list_evidence=pd.DataFrame(evidence_rows),
    )
    eligible = implied_share_cache_rows(audit)

    assert summary[0]["provider_overlap_valid"] is True
    assert summary[0]["overlap_relation"] == "undated_current_official_list"
    assert summary[0]["overlap_evidence_use"] == "validation_only_not_dated_share_input"
    assert summary[0]["formula_validated"] is True
    assert summary[0]["dated_share_flow_valid"] is False
    assert audit["validation_status"].eq("derived_formula_validated_date_unresolved").all()
    assert eligible.empty


def test_same_date_exchange_overlap_is_flow_eligible() -> None:
    aum_rows = []
    exchange_rows = []
    for index in range(6):
        code = f"159{index:03d}"
        shares = 100_000_000 + index * 1_000_000
        aum_rows.append(
            {
                "etf_code": code,
                "date": "2026-07-17",
                "aum": shares * 2.0,
                "nav": 2.0,
                "implied_shares": shares,
                "source": "fund_company_official:efunds:aum",
                "source_url": f"https://example.invalid/{code}",
                "retrieved_at": "2026-07-18T08:00:00",
                "evidence_sha256": "a" * 64,
            }
        )
        exchange_rows.append(
            {
                "etf_code": code,
                "date": "2026-07-17",
                "shares": shares + 1,
                "source": "szse_official:基金规模",
                "validation_status": "ok",
            }
        )

    audit, summary = validate_implied_shares(
        pd.DataFrame(aum_rows),
        pd.DataFrame(exchange_rows),
    )
    eligible = implied_share_cache_rows(audit)

    assert summary[0]["overlap_relation"] == "same_date"
    assert summary[0]["dated_share_flow_valid"] is True
    assert len(eligible) == 6
    assert set(eligible["date"]) == {"2026-07-17"}


def test_insufficient_overlap_never_enters_share_cache() -> None:
    aum = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "date": "2026-07-17",
                "aum": 200.0,
                "nav": 2.0,
                "implied_shares": 100.0,
                "source": "fund_company_official:efunds:aum",
            }
        ]
    )
    audit, summary = validate_implied_shares(
        aum,
        pd.DataFrame(),
        current_list_evidence=pd.DataFrame(
            [
                {
                    "etf_code": "159915",
                    "current_list_shares": 100.0,
                    "evidence_retrieved_date": "2026-07-18",
                }
            ]
        ),
    )

    assert summary[0]["provider_overlap_valid"] is False
    assert audit.iloc[0]["validation_status"] == "derived_same_date_overlap_pending"
    assert implied_share_cache_rows(audit).empty


def test_stale_current_list_evidence_cannot_validate_new_aum() -> None:
    aum = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "date": "2026-07-17",
                "aum": 200.0,
                "nav": 2.0,
                "implied_shares": 100.0,
                "source": "fund_company_official:efunds:aum",
            }
        ]
    )
    audit, summary = validate_implied_shares(
        aum,
        pd.DataFrame(),
        current_list_evidence=pd.DataFrame(
            [
                {
                    "etf_code": "159915",
                    "current_list_shares": 100.0,
                    "evidence_retrieved_date": "2026-07-04",
                }
            ]
        ),
    )

    assert summary[0]["overlap_count"] == 0
    assert summary[0]["provider_overlap_valid"] is False
    assert audit.iloc[0]["validation_status"] == "derived_same_date_overlap_pending"


def test_aum_only_builds_scale_without_fabricating_flow() -> None:
    universe = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "etf_name": "创业板ETF",
                "is_a_share_equity_etf": True,
            }
        ]
    )
    membership = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "primary_cluster": "C01",
                "primary_cluster_name": "宽基",
                "secondary_tags": "[]",
                "classification_basis": "test",
                "confidence": 1.0,
            }
        ]
    )
    quotes = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "date": "2026-07-17",
                "open": 3.4,
                "high": 3.5,
                "low": 3.3,
                "close": 3.45,
                "amount": 1_000_000,
                "volume": 100_000,
                "source": "szse_official:test",
            }
        ]
    )
    nav = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "date": "2026-07-17",
                "nav": 3.4507,
                "source": "fund_company_official:efunds:nav",
            }
        ]
    )
    aum = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "date": "2026-07-17",
                "aum": 44_943_486_647.66,
                "source": "fund_company_official:efunds:aum",
                "validation_status": "official_same_date_aum_nav",
            }
        ]
    )

    row = build_etf_daily(
        universe,
        membership,
        quotes,
        pd.DataFrame(),
        nav,
        "2026-07-17",
        aum=aum,
    ).iloc[0]

    assert row["fund_size_yuan"] == 44_943_486_647.66
    assert row["fund_size_basis"] == "official_aum"
    assert pd.isna(row["estimated_net_subscription_100m"])
    assert row["data_quality_status"] == "scale_only_no_flow"
