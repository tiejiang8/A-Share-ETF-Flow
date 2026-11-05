from __future__ import annotations

from pathlib import Path

import math
import pandas as pd
import pytest

from etf_temperature.aggregation import build_cluster_daily
from etf_temperature.flow_structure import (
    flow_offset_metrics,
    flow_speed_metrics,
    flow_structure_label,
)
from etf_temperature.narrative import price_layer, temperature_layer
from etf_temperature.official_endpoints import NORMALIZED_DIR, read_dataframe
from etf_temperature.report import _quadrant_reconciliation, _temperature_detail, generate_report
from etf_temperature.temperature import temperature_level, temperature_phase_from_values
from etf_temperature.weekly_report import _next_week_lines


def test_flow_speed_accelerates_when_last_day_exceeds_previous_four_day_average() -> None:
    result = flow_speed_metrics(10, 25, 5)
    assert result["flow_5d_daily_avg"] == 5
    assert result["flow_prev4_total"] == 15
    assert result["flow_prev4_daily_avg"] == 3.75
    assert result["flow_acceleration_vs_prev4"] == 6.25
    assert result["flow_acceleration"] == result["flow_acceleration_vs_prev4"]
    assert result["flow_speed_status"] == "持续流入且加速"


def test_flow_speed_slows_when_last_day_is_below_previous_four_day_average() -> None:
    result = flow_speed_metrics(2, 18, 5)
    assert result["flow_5d_daily_avg"] == 3.6
    assert result["flow_prev4_daily_avg"] == 4
    assert result["flow_acceleration_vs_prev4"] == -2
    assert result["flow_speed_status"] == "持续流入但减速"


def test_semiconductor_example_is_continuous_inflow_but_slowing() -> None:
    result = flow_speed_metrics(50.7, 361.5, 5)
    assert round(result["flow_prev4_total"], 1) == 310.8
    assert round(result["flow_prev4_daily_avg"], 1) == 77.7
    assert round(result["flow_acceleration_ratio_vs_prev4"], 3) == -0.347
    assert result["flow_speed_status"] == "持续流入但减速"


def test_flow_speed_detects_previous_four_day_outflow_reversal() -> None:
    result = flow_speed_metrics(21.4, 11.6, 5)
    assert round(result["flow_prev4_total"], 1) == -9.8
    assert result["flow_speed_status"] == "由流出转流入"


def test_flow_speed_does_not_force_five_day_or_near_zero_ratio() -> None:
    insufficient = flow_speed_metrics(2, None, 0)
    assert insufficient["flow_speed_status"] == "方向不明确"
    near_zero = flow_speed_metrics(1, 1.04, 5)
    assert near_zero["flow_acceleration_vs_prev4"] == 0.99
    assert math.isnan(near_zero["flow_acceleration_ratio_vs_prev4"])


def test_incomplete_previous_stage_does_not_force_speed_status() -> None:
    result = flow_speed_metrics(5, 12, 4, required_days=5)
    assert result["flow_prev4_valid_days"] == 3
    assert result["flow_speed_status"] == "方向不明确"


def test_large_two_way_cluster_flow_is_high_offset_switching() -> None:
    members = pd.DataFrame(
        [
            {"etf_code": "515880", "etf_name": "通信ETF", "flow": 10.0},
            {"etf_code": "159206", "etf_name": "卫星ETF", "flow": -9.0},
        ]
    )
    result = flow_offset_metrics(members, "flow", total_size_100m=500)
    assert result["gross_inflow"] == 10
    assert result["gross_outflow"] == -9
    assert result["net_flow"] == 1
    assert result["flow_offset_ratio"] > 0.89
    assert result["flow_offset_status"] == "高分歧换仓"


def test_single_or_double_member_cluster_does_not_claim_concentration() -> None:
    for member_count in (1, 2):
        label = flow_structure_label(
            member_count=member_count,
            net_flow=10,
            flow_to_size=0.02,
            share_increase_member_ratio=1,
            max_contributor_ratio=1,
            top3_contributor_ratio=1,
            gross_turnover_flow=10,
            flow_offset_ratio=0,
            total_size_100m=500,
        )
        assert label == "小样本不判断"
        assert "集中" not in label


def test_tiny_net_flow_does_not_trigger_high_contribution_warning() -> None:
    label = flow_structure_label(
        member_count=10,
        net_flow=0.05,
        flow_to_size=0.00005,
        share_increase_member_ratio=0.3,
        max_contributor_ratio=1,
        top3_contributor_ratio=1,
        gross_turnover_flow=0.05,
        flow_offset_ratio=0,
        total_size_100m=1000,
    )
    assert label == "低流量无方向"


def test_high_breadth_but_weak_flow_is_not_full_diffusion() -> None:
    label = flow_structure_label(
        member_count=24,
        net_flow=2.6,
        flow_to_size=0.0016,
        share_increase_member_ratio=0.625,
        max_contributor_ratio=0.45,
        top3_contributor_ratio=0.76,
        gross_turnover_flow=4.7,
        flow_offset_ratio=0.45,
        total_size_100m=1668,
    )
    assert label == "广度较高但力度较弱"


def test_medium_breadth_and_strong_flow_is_not_breadth_insufficient() -> None:
    label = flow_structure_label(
        member_count=35,
        net_flow=6.9,
        flow_to_size=0.009,
        share_increase_member_ratio=0.559,
        max_contributor_ratio=0.32,
        top3_contributor_ratio=0.70,
        gross_turnover_flow=8.2,
        flow_offset_ratio=0.16,
        total_size_100m=765,
    )
    assert label == "流入力度较强，广度中等"


def test_medium_offset_with_weak_net_flow_is_internal_divergence() -> None:
    label = flow_structure_label(
        member_count=45,
        net_flow=2.7,
        flow_to_size=0.0014,
        share_increase_member_ratio=0.333,
        max_contributor_ratio=0.34,
        top3_contributor_ratio=0.71,
        gross_turnover_flow=8.8,
        flow_offset_ratio=0.69,
        total_size_100m=1906,
    )
    assert label == "资金强度较低，内部中度分化"


def test_high_offset_has_priority_over_breadth_and_concentration() -> None:
    label = flow_structure_label(
        member_count=10,
        net_flow=1,
        flow_to_size=0.001,
        share_increase_member_ratio=0.8,
        max_contributor_ratio=0.9,
        top3_contributor_ratio=1,
        gross_turnover_flow=20,
        flow_offset_ratio=0.95,
        total_size_100m=500,
    )
    assert label == "高分歧换仓"


def test_broad_market_rally_uses_relative_gain_not_defensiveness() -> None:
    clusters = pd.DataFrame(
        [
            {"cluster_id": "C01", "cluster_name": "上证50", "weighted_pct_change": 2.0, "total_size_100m": 100},
            {"cluster_id": "C04", "cluster_name": "中证500", "weighted_pct_change": 1.0, "total_size_100m": 100},
            {"cluster_id": "C11", "cluster_name": "红利", "weighted_pct_change": 1.5, "total_size_100m": 100},
            {"cluster_id": "C16", "cluster_name": "半导体", "weighted_pct_change": 3.0, "total_size_100m": 100},
        ]
    )
    text = price_layer(clusters)
    assert "涨幅相对占优" in text
    assert "相对抗跌" not in text


def test_high_temperature_definition_is_unified_at_75() -> None:
    assert temperature_level(74.99) == "偏热"
    assert temperature_level(75) == "高温"
    assert temperature_phase_from_values(70, 0, 0, 0) == "持续偏热"


def test_report_modules_read_the_same_precomputed_temperature_state() -> None:
    row = pd.Series(
        {
            "cluster_name": "上证50",
            "cluster_temperature": 74.1,
            "temperature_level": "偏热",
            "temperature_direction": "快速升温",
            "temperature_stage": "持续偏热",
            "cluster_temperature_change_1d": 4,
            "cluster_temperature_change_3d": 7,
            "cluster_temperature_slope_5d": 9,
            "weekly_net_subscription_100m": 1,
            "weekly_temperature_change": 4,
        }
    )
    frame = pd.DataFrame([row])
    assert "74.1 偏热" in _temperature_detail(row)
    assert "持续偏热的是上证50" in temperature_layer(frame)
    assert "上证50当前为持续偏热" in _next_week_lines(frame)[2]
    assert "上证50当前为高温" not in _next_week_lines(frame)[2]


def test_quadrant_included_and_excluded_counts_reconcile() -> None:
    etf = pd.DataFrame(
        [
            {"pct_change": 1, "share_daily_change": 1},
            {"pct_change": 1, "share_daily_change": -1},
            {"pct_change": -1, "share_daily_change": 1},
            {"pct_change": -1, "share_daily_change": -1},
            {"pct_change": 1, "share_daily_change": 0},
            {"pct_change": 0, "share_daily_change": 1},
            {"pct_change": None, "share_daily_change": 1},
        ]
    )
    text = _quadrant_reconciliation(etf)
    assert "纳入ETF 4只" in text
    assert "未纳入 3只" in text
    assert "份额不变 1只" in text
    assert "价格持平 1只" in text
    assert "方向不可判定 1只" in text
    assert "总计 7只" in text


@pytest.mark.local_data
def test_2026_07_14_daily_report_regression(tmp_path: Path) -> None:
    history = read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet")
    etf_daily = history[history["date"].astype(str).eq("2026-07-14")].copy()
    assert not etf_daily.empty, "2026-07-14 regression snapshot is required"
    cluster_daily = build_cluster_daily(etf_daily)
    cluster_daily["date"] = "2026-07-14"

    csi1000 = cluster_daily[cluster_daily["cluster_id"].eq("C05")].iloc[0]
    communication = cluster_daily[cluster_daily["cluster_id"].eq("C19")].iloc[0]
    assert csi1000["flow_speed_status"] == "由流出转流入"
    assert round(float(csi1000["flow_prev4_total"]), 1) == -9.8
    assert communication["flow_offset_status"] == "高分歧换仓"
    assert float(communication["gross_turnover_flow"]) > 30

    report_path = tmp_path / "A股ETF资金温度日报_2026-07-14.md"
    generate_report(
        "2026-07-14",
        report_path,
        {
            "formal_report_allowed": True,
            "quote_fresh_coverage": 1.0,
            "share_fresh_coverage": 1.0,
            "nav_fresh_coverage": 1.0,
            "flow_size_coverage": 1.0,
            "history_trading_days": 19,
        },
        {},
        cluster_daily,
        etf_daily,
        pd.DataFrame(),
        pd.DataFrame(),
        [],
    )
    text = report_path.read_text(encoding="utf-8")
    assert "### 资金速度与内部结构" in text
    assert "中证1000" in text and "由流出转流入" in text
    assert "通信" in text and "高分歧换仓" in text
    assert "相对抗跌" not in text
    assert "高温≥75" in text
    assert "前4日资金日均" in text
    assert "5日资金日均" not in text
    assert "四象限对账" in text
