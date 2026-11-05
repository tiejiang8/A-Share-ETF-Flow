from __future__ import annotations

import pandas as pd

from etf_temperature.corporate_actions import (
    _official_termination_event,
    apply_adjusted_history_features,
    apply_corporate_action_adjustments,
    detect_etf_corporate_action,
)
from etf_temperature.weekly import build_weekly_etf


def _daily(**overrides) -> pd.DataFrame:
    row = {
        "etf_code": "588170",
        "date": "2026-07-08",
        "close": 1.02,
        "prev_close": 2.00,
        "nav": 1.01,
        "previous_nav": 2.00,
        "latest_shares": 20_200_000,
        "previous_shares": 10_000_000,
        "share_daily_change": 10_200_000,
        "share_daily_change_pct": 1.02,
        "share_5d_change": 10_200_000,
        "share_5d_change_pct": 1.02,
        "estimated_net_subscription_100m": 103.02,
        "estimated_net_subscription_5d_100m": 103.02,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_two_for_one_split_adjusts_return_and_flow():
    adjusted = apply_corporate_action_adjustments(_daily()).iloc[0]

    assert adjusted["corporate_action_type"] == "split"
    assert adjusted["corporate_action_factor"] == 2.0
    assert round(adjusted["adjusted_daily_return"], 6) == 0.02
    assert adjusted["raw_share_daily_change"] == 10_200_000
    assert adjusted["adjusted_share_change"] == 200_000
    assert round(adjusted["estimated_net_subscription_100m"], 6) == round(200_000 * 1.01 / 1e8, 6)


def test_five_for_one_split_is_not_limited_to_two():
    event = detect_etf_corporate_action(5.0, 1.01, 5.0, 1.0, 10_000_000, 50_000_000)

    assert event["corporate_action_type"] == "split"
    assert event["corporate_action_factor"] == 5.0


def test_reverse_split_is_adjusted_symmetrically():
    adjusted = apply_corporate_action_adjustments(
        _daily(
            close=2.04,
            prev_close=1.0,
            nav=2.02,
            previous_nav=1.0,
            latest_shares=5_050_000,
            previous_shares=10_000_000,
            share_daily_change=-4_950_000,
            share_daily_change_pct=-0.495,
        )
    ).iloc[0]

    assert adjusted["corporate_action_type"] == "reverse_split"
    assert adjusted["corporate_action_factor"] == 0.5
    assert round(adjusted["adjusted_daily_return"], 6) == 0.02
    assert adjusted["adjusted_share_change"] == 50_000


def test_real_market_crash_is_not_misclassified_as_split():
    event = detect_etf_corporate_action(2.0, 1.4, 2.0, 1.4, 10_000_000, 10_100_000)

    assert event["corporate_action_flag"] is False


def test_real_large_subscription_is_not_misclassified_as_split():
    adjusted = apply_corporate_action_adjustments(
        _daily(close=2.02, prev_close=2.0, nav=2.01, previous_nav=2.0, latest_shares=15_000_000)
    ).iloc[0]

    assert not bool(adjusted["corporate_action_flag"])
    assert adjusted["estimated_net_subscription_100m"] > 0


def test_real_large_subscription_on_ordinary_down_day_is_not_marked_suspect():
    adjusted = apply_corporate_action_adjustments(
        _daily(
            close=1.90,
            prev_close=2.0,
            nav=1.90,
            previous_nav=2.0,
            latest_shares=13_500_000,
            share_daily_change=3_500_000,
            share_daily_change_pct=0.35,
        )
    ).iloc[0]

    assert not bool(adjusted["corporate_action_flag"])
    assert adjusted["corporate_action_status"] == "normal"
    assert adjusted["adjusted_share_change"] == 3_500_000
    assert adjusted["estimated_net_subscription_100m"] > 0


def test_split_with_real_subscription_keeps_only_extra_shares():
    adjusted = apply_corporate_action_adjustments(_daily(latest_shares=20_500_000)).iloc[0]

    assert adjusted["adjusted_share_change"] == 500_000
    assert round(adjusted["estimated_net_subscription_100m"], 6) == round(500_000 * 1.01 / 1e8, 6)


def test_suspect_event_is_excluded_with_nan_metrics():
    adjusted = apply_corporate_action_adjustments(
        _daily(close=1.4, prev_close=2.0, nav=1.5, previous_nav=2.0, latest_shares=20_000_000)
    ).iloc[0]

    assert adjusted["corporate_action_type"] == "suspect"
    assert adjusted["share_flow_excluded"]
    assert pd.isna(adjusted["pct_change"])
    assert pd.isna(adjusted["estimated_net_subscription_100m"])


def test_share_balance_falling_to_zero_is_excluded_as_liquidation():
    adjusted = apply_corporate_action_adjustments(
        _daily(
            etf_code="560000",
            close=0.939,
            prev_close=0.939,
            nav=0.9379,
            previous_nav=0.9379,
            latest_shares=0,
            previous_shares=7_022_900,
            share_daily_change=-7_022_900,
            share_daily_change_pct=-1.0,
            share_5d_change=-7_022_900,
            share_5d_change_pct=-1.0,
        )
    ).iloc[0]

    assert adjusted["corporate_action_type"] == "liquidation"
    assert adjusted["corporate_action_status"] == "liquidation_excluded"
    assert bool(adjusted["share_flow_excluded"])
    assert pd.isna(adjusted["pct_change"])
    assert pd.isna(adjusted["estimated_net_subscription_100m"])


def test_weekly_return_compounds_adjusted_daily_returns_across_midweek_split():
    rows = []
    dates = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]
    for day, daily_return, close in zip(dates, [0.01, 0.01, 0.02, 0.01, 0.01], [2.02, 2.0402, 1.040502, 1.050907, 1.061416]):
        rows.append(
            {
                "date": day,
                "etf_code": "588170",
                "etf_name": "科创半导",
                "primary_cluster": "C16",
                "primary_cluster_name": "半导体/芯片",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "amount": 1.0,
                "latest_shares": 20.0,
                "fund_size_100m": 1.0,
                "estimated_net_subscription_100m": 0.0,
                "adjusted_share_change": 0.0,
                "adjusted_daily_return": daily_return,
                "raw_daily_return": -0.49 if day == "2026-07-08" else daily_return,
                "quote_data_date": day,
                "share_data_date": day,
                "nav_data_date": day,
                "nav": 1.0,
                "temperature": 50.0,
                "corporate_action_type": "split" if day == "2026-07-08" else None,
            }
        )
    weekly = build_weekly_etf("2026-W28", pd.DataFrame(rows), pd.DataFrame())
    row = weekly.iloc[0]

    assert round(row["weekly_pct_change"], 6) == round(((1.01 * 1.01 * 1.02 * 1.01 * 1.01) - 1) * 100, 6)
    assert row["weekly_pct_change"] > 0
    assert row["corporate_action_days"] == 1


def test_w28_named_split_regression_fixture_has_no_false_half_price_crash():
    codes = ["588170", "562590", "515880", "515070"]
    frame = pd.concat([_daily(etf_code=code) for code in codes], ignore_index=True)
    adjusted = apply_corporate_action_adjustments(frame)

    assert set(adjusted["corporate_action_type"]) == {"split"}
    assert (adjusted["pct_change"] > -5).all()
    assert (adjusted["estimated_net_subscription_100m"] < 1.0).all()


def test_adjusted_history_uses_daily_flow_sum_not_raw_five_day_share_delta():
    first = apply_corporate_action_adjustments(_daily(date="2026-07-06", close=2.0, prev_close=1.98, nav=2.0, previous_nav=1.98, latest_shares=10_100_000, previous_shares=10_000_000))
    days = [first]
    for offset in range(1, 5):
        current = _daily(date=f"2026-07-{6 + offset:02d}", close=1.02, prev_close=1.01, nav=1.01, previous_nav=1.0)
        days.append(apply_corporate_action_adjustments(current))
    current = apply_adjusted_history_features(days[-1], pd.concat(days[:-1], ignore_index=True)).iloc[0]

    assert pd.notna(current["estimated_net_subscription_5d_100m"])


def test_weekly_split_factor_matches_next_trading_day_price_jump():
    rows = []
    dates = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]
    raw_returns = [-0.01, 0.02, 0.01, -0.65, -0.04]
    for day, raw_return in zip(dates, raw_returns):
        rows.append(
            {
                "date": day,
                "etf_code": "159558",
                "etf_name": "半导体设备ETF易方达",
                "primary_cluster": "C16",
                "primary_cluster_name": "半导体/芯片",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "amount": 1.0,
                "latest_shares": 100.0,
                "fund_size_100m": 1.0,
                "estimated_net_subscription_100m": 0.0,
                "adjusted_share_change": 0.0,
                "adjusted_daily_return": raw_return,
                "raw_daily_return": raw_return,
                "quote_data_date": day,
                "share_data_date": day,
                "nav_data_date": day,
                "nav": 1.0,
                "temperature": 50.0,
                "corporate_action_type": "split" if day == "2026-07-08" else None,
                "corporate_action_factor": 3.0 if day == "2026-07-08" else float("nan"),
            }
        )

    weekly = build_weekly_etf("2026-W28", pd.DataFrame(rows), pd.DataFrame())
    row = weekly.iloc[0]

    expected = ((1 - 0.01) * (1 + 0.02) * (1 + 0.01) * ((1 - 0.65) * 3) * (1 - 0.04) - 1) * 100
    assert round(row["weekly_pct_change"], 6) == round(expected, 6)
    assert not bool(row["corporate_action_adjustment_failed"])
    assert '"effective_price_date": "2026-07-09"' in row["corporate_action_events"]


def test_failed_split_repair_excludes_weekly_return_and_flow():
    rows = []
    for day in ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]:
        rows.append(
            {
                "date": day,
                "etf_code": "159999",
                "etf_name": "测试ETF",
                "primary_cluster": "C16",
                "primary_cluster_name": "半导体/芯片",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "amount": 1.0,
                "latest_shares": 100.0,
                "fund_size_100m": 1.0,
                "estimated_net_subscription_100m": 1.0,
                "adjusted_share_change": 1.0,
                "adjusted_daily_return": 0.01,
                "raw_daily_return": 0.01,
                "quote_data_date": day,
                "share_data_date": day,
                "nav_data_date": day,
                "nav": 1.0,
                "temperature": 50.0,
                "corporate_action_type": "split" if day == "2026-07-08" else None,
                "corporate_action_factor": 3.0 if day == "2026-07-08" else float("nan"),
            }
        )

    row = build_weekly_etf("2026-W28", pd.DataFrame(rows), pd.DataFrame()).iloc[0]
    assert bool(row["corporate_action_adjustment_failed"])
    assert pd.isna(row["weekly_pct_change"])
    assert pd.isna(row["weekly_net_subscription_100m"])


def _ex_rights_daily(**overrides) -> pd.DataFrame:
    """Registration day already doubled shares and halved NAV; only price lags."""

    row = {
        "etf_code": "159560",
        "date": "2026-09-07",
        "close": 1.08,
        "prev_close": 2.153,
        "nav": 1.0724,
        "previous_nav": 1.0724,
        "latest_shares": 404_832_920,
        "previous_shares": 404_832_920,
        "share_daily_change": 1_000_000,
        "share_daily_change_pct": 0.00247,
        "adjusted_share_change": 1_000_000,
        "share_5d_change": 10_500_000,
        "share_5d_change_pct": 0.026627,
        "estimated_net_subscription_100m": 0.010724,
        "estimated_net_subscription_5d_100m": 0.128349,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_ex_rights_session_restores_real_return_without_touching_flow():
    adjusted = apply_corporate_action_adjustments(_ex_rights_daily()).iloc[0]

    assert adjusted["corporate_action_type"] == "ex_rights"
    assert adjusted["corporate_action_factor"] == 2.0
    # 1.08 * 2 / 2.153 - 1 ~= +0.33%, not the raw -49.8% price drop.
    assert round(adjusted["pct_change"], 4) == 0.3251
    assert round(adjusted["adjusted_daily_return"], 6) == 0.003251
    assert bool(adjusted["share_flow_excluded"]) is False
    assert round(adjusted["estimated_net_subscription_100m"], 6) == 0.010724


def test_real_price_move_inside_daily_limit_is_never_treated_as_ex_rights():
    row = _ex_rights_daily(close=1.83, prev_close=2.153)
    adjusted = apply_corporate_action_adjustments(row).iloc[0]

    assert bool(adjusted["corporate_action_flag"]) is False
    assert adjusted["corporate_action_type"] is None
    assert round(adjusted["pct_change"], 4) == -15.0023


def test_share_5d_change_pct_is_recomputed_from_adjusted_base():
    history = pd.DataFrame(
        [
            {"date": day, "etf_code": "159560", "latest_shares": 400_000_000, "adjusted_share_change": change}
            for day, change in [
                ("2026-08-31", 500_000),
                ("2026-09-01", 0),
                ("2026-09-02", 250_000),
                ("2026-09-03", 750_000),
            ]
        ]
    )
    current = _ex_rights_daily(date="2026-09-04", share_5d_change_pct=1.061095)
    current["adjusted_share_change"] = 9_000_000
    merged = apply_adjusted_history_features(current, history).iloc[0]

    assert merged["share_5d_change"] == 10_500_000
    # The stale percentage came from the pre-split balance; the adjusted base is
    # latest_shares minus the adjusted change.
    assert round(merged["share_5d_change_pct"], 6) == 0.026627


def test_officially_terminated_fund_is_excluded_not_unresolved():
    """清算期标的份额未注销，份额归零判定看不到它，须靠官方公告显式判为 liquidation。"""

    terminations = {
        "512390": {
            "etf_code": "512390",
            "effective_date": "2026-09-09",
            "reason": "自 2026-09-09 起进入基金清算程序。",
            "source_url": "https://www.sse.com.cn/example.pdf",
        }
    }

    # 生效日之前：不干预
    assert _official_termination_event("512390", "2026-09-08", terminations) is None
    # 生效日当天及之后：判为清盘
    event = _official_termination_event("512390", "2026-09-10", terminations)
    assert event is not None
    assert event["corporate_action_type"] == "liquidation"
    # 其他代码不受影响
    assert _official_termination_event("510300", "2026-09-10", terminations) is None


def test_terminated_fund_flow_is_excluded_from_aggregation():
    """512390 于 2026-09-09 进入清算：份额冻结、净值停更，资金流必须剔除而非留作未解决。"""

    daily = pd.DataFrame(
        [
            {
                "etf_code": "512390",
                "date": "2026-09-10",
                "close": 1.227,
                "prev_close": 1.227,
                "nav": 1.2208,
                "previous_nav": 1.2208,
                "latest_shares": 9_682_000.0,
                "previous_shares": 9_682_000.0,
                "share_daily_change": 0.0,
                "share_daily_change_pct": 0.0,
            }
        ]
    )

    adjusted = apply_corporate_action_adjustments(daily).iloc[0]

    assert adjusted["corporate_action_type"] == "liquidation"
    assert bool(adjusted["share_flow_excluded"]) is True
    assert pd.isna(adjusted["estimated_net_subscription_100m"])
