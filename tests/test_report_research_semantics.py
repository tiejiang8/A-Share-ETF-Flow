from __future__ import annotations

import pandas as pd

from etf_temperature.aggregation import _contributor_stats
from etf_temperature.narrative import price_layer
from etf_temperature.report import _contributor_text, _important_cluster_lines


def test_price_layer_separates_resource_cycle_from_dividend_defense() -> None:
    clusters = pd.DataFrame(
        [
            {"cluster_id": "C11", "cluster_name": "红利", "weighted_pct_change": 1.0, "total_size_100m": 100},
            {"cluster_id": "C15", "cluster_name": "银行", "weighted_pct_change": 0.5, "total_size_100m": 100},
            {"cluster_id": "C29", "cluster_name": "有色", "weighted_pct_change": 5.0, "total_size_100m": 100},
            {"cluster_id": "C30", "cluster_name": "煤炭", "weighted_pct_change": 4.0, "total_size_100m": 100},
            {"cluster_id": "C31", "cluster_name": "化工", "weighted_pct_change": 3.0, "total_size_100m": 100},
            {"cluster_id": "C16", "cluster_name": "半导体/芯片", "weighted_pct_change": 1.5, "total_size_100m": 100},
            {"cluster_id": "C01", "cluster_name": "上证50", "weighted_pct_change": 1.0, "total_size_100m": 100},
            {"cluster_id": "C04", "cluster_name": "中证500", "weighted_pct_change": 0.8, "total_size_100m": 100},
            {"cluster_id": "C07", "cluster_name": "创业板", "weighted_pct_change": 2.0, "total_size_100m": 100},
            {"cluster_id": "C09", "cluster_name": "科创50", "weighted_pct_change": 1.0, "total_size_100m": 100},
        ]
    )

    text = price_layer(clusters)

    assert "资源周期相对占优" in text
    assert "红利防御相对占优" not in text


def test_contributor_stats_uses_same_direction_as_cluster_net_flow() -> None:
    subset = pd.DataFrame(
        [
            {"etf_code": "159206", "etf_name": "卫星ETF永赢", "estimated_net_subscription_100m": -13.29},
            {"etf_code": "515880", "etf_name": "通信ETF", "estimated_net_subscription_100m": 8.73},
            {"etf_code": "515050", "etf_name": "5GETF", "estimated_net_subscription_100m": 8.65},
            {"etf_code": "159994", "etf_name": "5GETF银华", "estimated_net_subscription_100m": -2.70},
        ]
    )

    code, name, flow, ratio, top3_ratio, same_direction_total = _contributor_stats(
        subset, "estimated_net_subscription_100m"
    )

    assert subset["estimated_net_subscription_100m"].sum() > 0
    assert code == "515880"
    assert name == "通信ETF"
    assert flow == 8.73
    assert same_direction_total > 0
    assert ratio == 8.73 / (8.73 + 8.65)
    assert top3_ratio == 1.0


def test_small_member_cluster_is_not_described_as_concentrated() -> None:
    row = pd.Series(
        {
            "member_count": 1,
            "max_contributor_code_1d": "512690",
            "max_contributor_name_1d": "酒ETF",
            "max_contributor_flow_1d_100m": -0.3,
            "max_contributor_ratio_1d": 1.0,
            "estimated_net_subscription_100m": -0.3,
            "one_day_flow_to_size": -0.0019,
            "concentration_sample_valid": False,
        }
    )

    text = _contributor_text(row)

    assert "样本1只" in text
    assert "不作集中度判断" in text
    assert "占同向流出100.00%" not in text


def test_concentration_observation_ignores_tiny_flow_pseudo_signal() -> None:
    clusters = pd.DataFrame(
        [
            {
                "cluster_name": "环保/碳中和",
                "member_count": 17,
                "estimated_net_subscription_100m": 0.01,
                "estimated_net_subscription_5d_100m": 0.02,
                "one_day_flow_to_size": 0.0001,
                "weighted_pct_change": 0.2,
                "temperature_change": 0.0,
                "max_contributor_code_1d": "159790",
                "max_contributor_name_1d": "碳中和ETF华夏",
                "max_contributor_flow_1d_100m": 0.03,
                "max_contributor_ratio_1d": 1.0,
                "top3_contributor_ratio_1d": 1.0,
                "concentration_sample_valid": True,
            },
            {
                "cluster_name": "中证500",
                "member_count": 38,
                "estimated_net_subscription_100m": 26.6,
                "estimated_net_subscription_5d_100m": 112.0,
                "one_day_flow_to_size": 0.0325,
                "weighted_pct_change": 1.4,
                "temperature_change": 2.0,
                "max_contributor_code_1d": "510500",
                "max_contributor_name_1d": "500ETF",
                "max_contributor_flow_1d_100m": 19.0,
                "max_contributor_ratio_1d": 0.71,
                "top3_contributor_ratio_1d": 0.87,
                "concentration_sample_valid": True,
            },
        ]
    )

    lines = _important_cluster_lines(clusters)
    text = "\n".join(lines)

    assert "单一ETF主导：中证500" in text
    assert "单一ETF主导：环保/碳中和" not in text
