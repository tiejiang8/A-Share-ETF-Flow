from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import pytest

from etf_temperature.aggregation import build_cluster_daily
from etf_temperature.corporate_actions import apply_corporate_action_adjustments
from etf_temperature.history import upsert_history
from etf_temperature.indicators import build_etf_daily, compute_quote_features
from etf_temperature.temperature import add_product_temperature_history_features, consistency_score
from etf_temperature.universe import apply_a_share_filter, build_universe, classify_a_share_equity
from etf_temperature.weekly import build_weekly_cluster, build_weekly_etf, validate_weekly_data
from etf_temperature.weekly_report import generate_weekly_report
from etf_temperature.charts import write_cover_image


def test_price_down_share_down_consistency_score_is_low():
    score = consistency_score(pd.Series([-1.0]), pd.Series([-100.0])).iloc[0]
    assert score == 0


def test_amount_5d_mean_excludes_current_day():
    quotes = pd.DataFrame(
        [
            {"etf_code": "510050", "date": f"2026-06-{day:02d}", "close": 1, "amount": amount}
            for day, amount in zip(range(24, 29), [10, 20, 30, 40, 50])
        ]
        + [{"etf_code": "510050", "date": "2026-06-29", "close": 1, "amount": 1000}]
    )
    out = compute_quote_features(quotes, "2026-06-29").iloc[0]
    assert out["amount_5d_mean"] == 30
    assert round(out["amount_vs_5d"], 6) == round(1000 / 30, 6)


def test_unconfirmed_corporate_action_is_removed_before_aggregation():
    daily = pd.DataFrame(
        [
            {
                "etf_code": "512480",
                "primary_cluster": "C16",
                "fund_size_100m": 100,
                "latest_shares": 200,
                "previous_shares": 100,
                "shares_5d_ago": 100,
                "share_daily_change": 100,
                "share_daily_change_pct": 1.0,
                "share_5d_change": 100,
                "share_5d_change_pct": 1.0,
                "close": 1.80,
                "prev_close": 2.94,
                "nav": 1.34,
                "estimated_net_subscription_100m": 100,
                "estimated_net_subscription_5d_100m": 100,
                "amount": 1,
                "amount_5d_mean": 1,
                "pct_change": -38.78,
                "return_5d": 0,
                "return_10d": 0,
                "premium_discount": 0,
            },
            {
                "etf_code": "588200",
                "primary_cluster": "C16",
                "fund_size_100m": 100,
                "latest_shares": 101,
                "previous_shares": 100,
                "shares_5d_ago": 100,
                "share_daily_change": 1,
                "share_daily_change_pct": 0.01,
                "share_5d_change": 1,
                "share_5d_change_pct": 0.01,
                "close": 1.0,
                "prev_close": 1.0,
                "nav": 1.0,
                "estimated_net_subscription_100m": 1,
                "estimated_net_subscription_5d_100m": 1,
                "amount": 1,
                "amount_5d_mean": 1,
                "pct_change": 0,
                "return_5d": 0,
                "return_10d": 0,
                "premium_discount": 0,
            },
        ]
    )
    adjusted = apply_corporate_action_adjustments(daily)
    assert pd.isna(adjusted.loc[adjusted["etf_code"].eq("512480"), "estimated_net_subscription_100m"]).iloc[0]
    cluster = build_cluster_daily(adjusted)
    c16 = cluster[cluster["cluster_id"].eq("C16")].iloc[0]
    assert c16["estimated_net_subscription_100m"] == 1e-8


def test_gold_cross_border_bond_and_money_etfs_do_not_enter_real_a_share_pool():
    universe_path = Path("data/normalized/etf_universe.parquet")
    if universe_path.exists():
        universe = apply_a_share_filter(pd.read_parquet(universe_path))
        for code in ["513310", "518600", "518880", "513500", "513100", "511880"]:
            rows = universe[universe["etf_code"].astype(str).eq(code)]
            if not rows.empty:
                assert not rows.iloc[0]["is_a_share_equity_etf"], code
    synthetic = build_universe(
        pd.DataFrame(
            [
                {"etf_code": "518600", "etf_name": "上海金", "exchange": "SSE", "etf_type": "stock_etf"},
                {"etf_code": "513310", "etf_name": "中韩芯片", "tracking_index_name": "中证韩交所中韩半导体指数", "exchange": "SSE", "etf_type": "stock_etf"},
                {"etf_code": "511880", "etf_name": "银华日利", "exchange": "SSE", "etf_type": "stock_etf"},
            ]
        ),
        pd.DataFrame(),
    )
    assert synthetic["is_a_share_equity_etf"].sum() == 0


@pytest.mark.local_data
def test_512480_split_like_jump_does_not_create_false_subscription_when_data_available():
    history_path = Path("data/normalized/etf_daily_history.parquet")
    if not history_path.exists():
        pytest.skip("daily history sample data not available")
    history = pd.read_parquet(history_path)
    row = history[(history["date"].astype(str).eq("2026-07-02")) & (history["etf_code"].astype(str).eq("512480"))]
    if row.empty:
        pytest.skip("512480 2026-07-02 regression row not available")
    assert pd.isna(row.iloc[0]["estimated_net_subscription_100m"])
    assert row.iloc[0]["share_flow_excluded"]


def test_daily_history_upsert_is_idempotent():
    existing = pd.DataFrame([{"date": "2026-07-01", "etf_code": "510050", "temperature": 50}])
    new = pd.DataFrame([{"date": "2026-07-01", "etf_code": "510050", "temperature": 60}])
    once = upsert_history(existing, new, ["date", "etf_code"])
    twice = upsert_history(once, new, ["date", "etf_code"])
    assert len(twice) == 1
    assert twice.iloc[0]["temperature"] == 60


def test_chart_cover_uses_first_generated_chart(tmp_path):
    first = tmp_path / "589720_kline.png"
    first.write_bytes(b"first-chart")
    second = tmp_path / "510050_kline.png"
    second.write_bytes(b"second-chart")

    cover = write_cover_image([first, second], tmp_path)

    assert cover == tmp_path / "cover.png"
    assert cover.read_bytes() == b"first-chart"


def test_should_refresh_report_date_reuses_cached_snapshot_until_refresh_requested(monkeypatch):
    import etf_temperature.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_local_snapshot_available", lambda report_date: True)
    monkeypatch.setattr(
        pipeline,
        "_history_dates",
        lambda: ["2026-06-26", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"],
    )

    should_refresh_old, _ = pipeline.should_refresh_report_date("2026-06-30", recent_days=2)
    should_refresh_recent, _ = pipeline.should_refresh_report_date("2026-07-02", recent_days=2)
    should_refresh_forced, _ = pipeline.should_refresh_report_date("2026-06-30", force=True, recent_days=2)

    assert not should_refresh_old
    assert not should_refresh_recent
    assert should_refresh_forced


def test_run_pipeline_uses_cached_render_before_official_refresh(monkeypatch):
    import etf_temperature.pipeline as pipeline

    def fail_if_called(*args, **kwargs):
        raise AssertionError("official refresh path should not run")

    def fake_render(report_date, *, cache_reason=""):
        return pipeline.PipelineResult(
            report_date=report_date,
            report_path=None,
            chart_dir=None,
            metrics={"render_mode": "cache"},
            summary={},
            representatives=pd.DataFrame(),
            cluster_daily=pd.DataFrame(),
            unresolved_issues=[],
            used_cache=True,
            cache_reason=cache_reason,
        )

    monkeypatch.setattr(pipeline, "should_refresh_report_date", lambda *args, **kwargs: (False, "cache-hit"))
    monkeypatch.setattr(pipeline, "render_report_from_cache", fake_render)
    monkeypatch.setattr(pipeline, "write_probe_outputs", fail_if_called)
    monkeypatch.setattr(pipeline, "OfficialClient", fail_if_called)

    result = pipeline.run_pipeline("2026-06-30")

    assert result.used_cache
    assert result.cache_reason == "cache-hit"


def test_cli_refresh_recent_days_implies_refresh(monkeypatch, capsys):
    from etf_temperature import cli
    from etf_temperature.pipeline import PipelineResult

    captured = {}

    def fake_run_pipeline(date_text, **kwargs):
        captured["date"] = date_text
        captured.update(kwargs)
        return PipelineResult(
            report_date=date_text,
            report_path=None,
            chart_dir=None,
            metrics={"formal_report_allowed": True},
            summary={},
            representatives=pd.DataFrame(),
            cluster_daily=pd.DataFrame(),
            unresolved_issues=[],
            used_cache=False,
            cache_reason="",
        )

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    assert cli.main(["run", "--date", "2026-07-03", "--refresh-recent-days", "2"]) == 0

    assert captured["date"] == "2026-07-03"
    assert captured["refresh"] is True
    assert captured["refresh_recent_days"] == 2
    assert captured["full_refresh"] is False
    assert '"used_cache": false' in capsys.readouterr().out


def test_cli_full_refresh_implies_refresh(monkeypatch):
    from etf_temperature import cli
    from etf_temperature.pipeline import PipelineResult

    captured = {}

    def fake_run_pipeline(date_text, **kwargs):
        captured.update(kwargs)
        return PipelineResult(
            report_date=date_text,
            report_path=None,
            chart_dir=None,
            metrics={"formal_report_allowed": True},
            summary={},
            representatives=pd.DataFrame(),
            cluster_daily=pd.DataFrame(),
            unresolved_issues=[],
            used_cache=False,
            cache_reason="",
        )

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    assert cli.main(["run", "--date", "2026-07-03", "--full-refresh"]) == 0

    assert captured["refresh"] is True
    assert captured["refresh_recent_days"] == 2
    assert captured["full_refresh"] is True


def test_temperature_change_and_three_day_warming_signal():
    history = pd.DataFrame(
        [
            {"date": "2026-07-01", "etf_code": "510050", "temperature": 50, "estimated_net_subscription_100m": 1},
            {"date": "2026-07-02", "etf_code": "510050", "temperature": 55, "estimated_net_subscription_100m": 1},
        ]
    )
    current = pd.DataFrame(
        [{"date": "2026-07-03", "etf_code": "510050", "temperature": 60, "estimated_net_subscription_100m": 1}]
    )
    out = add_product_temperature_history_features(current, history).iloc[0]
    assert out["temperature_change_1d"] == 5
    assert out["continuous_warming"]


def _weekly_history_sample():
    history = pd.DataFrame(
        [
            {"date": "2026-06-26", "etf_code": "510050", "etf_name": "50ETF", "primary_cluster": "C01", "primary_cluster_name": "上证50", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 100, "latest_shares": 100, "fund_size_100m": 100, "estimated_net_subscription_100m": 0, "temperature": 50},
            {"date": "2026-06-29", "etf_code": "510050", "etf_name": "50ETF", "primary_cluster": "C01", "primary_cluster_name": "上证50", "open": 1.05, "high": 1.2, "low": 0.9, "close": 1.1, "amount": 10, "latest_shares": 101, "fund_size_100m": 101, "estimated_net_subscription_100m": 1, "temperature": 55},
            {"date": "2026-06-30", "etf_code": "510050", "etf_name": "50ETF", "primary_cluster": "C01", "primary_cluster_name": "上证50", "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2, "amount": 20, "latest_shares": 102, "fund_size_100m": 102, "estimated_net_subscription_100m": 2, "temperature": 60},
            {"date": "2026-07-01", "etf_code": "510050", "etf_name": "50ETF", "primary_cluster": "C01", "primary_cluster_name": "上证50", "open": 1.2, "high": 1.4, "low": 1.1, "close": 1.3, "amount": 30, "latest_shares": 103, "fund_size_100m": 103, "estimated_net_subscription_100m": 3, "temperature": 65},
        ]
    )
    extra = history.iloc[[-1]].copy()
    extra["date"] = "2026-07-02"
    extra["amount"] = 0
    extra["estimated_net_subscription_100m"] = 0
    final = extra.copy()
    final["date"] = "2026-07-03"
    combined = pd.concat([history, extra, final], ignore_index=True)
    for column in ["quote_data_date", "share_data_date", "nav_data_date"]:
        combined[column] = combined["date"]
    combined["nav"] = 1.0
    return combined


def test_weekly_ohlc_and_net_subscription_sum_daily_flows():
    weekly = build_weekly_etf("2026-W27", _weekly_history_sample())
    row = weekly.iloc[0]
    assert row["week_open"] == 1.05
    assert row["week_high"] == 1.4
    assert row["week_low"] == 0.9
    assert row["week_close"] == 1.3
    assert round(row["weekly_pct_change"], 6) == 30.0
    assert row["weekly_net_subscription_100m"] == 6


def test_weekly_cluster_flow_uses_one_row_per_etf_without_duplicate_share_sum():
    weekly = build_weekly_etf("2026-W27", _weekly_history_sample())
    cluster = build_weekly_cluster("2026-W27", weekly)
    c01 = cluster[cluster["cluster_id"].eq("C01")].iloc[0]
    assert c01["weekly_net_subscription_100m"] == 6
    assert c01["member_count"] == 1


def test_weekly_flow_is_missing_when_any_trading_day_is_missing():
    partial = _weekly_history_sample()
    partial = partial[~partial["date"].eq("2026-07-03")]

    weekly = build_weekly_etf("2026-W27", partial)

    row = weekly.iloc[0]
    assert row["flow_days"] == 4
    assert pd.isna(row["weekly_net_subscription_100m"])


def test_weekly_flow_is_missing_when_nav_provenance_is_stale():
    stale = _weekly_history_sample()
    stale.loc[stale["date"].eq("2026-07-03"), "nav_data_date"] = "2026-07-02"

    weekly = build_weekly_etf("2026-W27", stale)

    row = weekly.iloc[0]
    assert row["flow_days"] == 4
    assert pd.isna(row["weekly_net_subscription_100m"])


def test_cluster_daily_exposes_flow_concentration_fields():
    daily = pd.DataFrame(
        [
            {"etf_code": "510300", "etf_name": "300ETF-A", "primary_cluster": "C02", "fund_size_100m": 100, "latest_shares": 100, "close": 1, "nav": 1, "amount": 100, "amount_5d_mean": 100, "pct_change": 1, "return_5d": 0, "return_10d": 0, "share_daily_change": 1, "premium_discount": 0, "estimated_net_subscription_100m": 6, "estimated_net_subscription_5d_100m": 6},
            {"etf_code": "159919", "etf_name": "300ETF-B", "primary_cluster": "C02", "fund_size_100m": 100, "latest_shares": 100, "close": 1, "nav": 1, "amount": 100, "amount_5d_mean": 100, "pct_change": 1, "return_5d": 0, "return_10d": 0, "share_daily_change": 1, "premium_discount": 0, "estimated_net_subscription_100m": 3, "estimated_net_subscription_5d_100m": 3},
            {"etf_code": "510310", "etf_name": "300ETF-C", "primary_cluster": "C02", "fund_size_100m": 100, "latest_shares": 100, "close": 1, "nav": 1, "amount": 100, "amount_5d_mean": 100, "pct_change": 1, "return_5d": 0, "return_10d": 0, "share_daily_change": 1, "premium_discount": 0, "estimated_net_subscription_100m": 1, "estimated_net_subscription_5d_100m": 1},
        ]
    )
    cluster = build_cluster_daily(daily)
    c02 = cluster[cluster["cluster_id"].eq("C02")].iloc[0]
    assert c02["max_contributor_code_1d"] == "510300"
    assert c02["max_contributor_name_1d"] == "300ETF-A"
    assert round(c02["max_contributor_ratio_1d"], 6) == 0.6
    assert round(c02["top3_contributor_ratio_1d"], 6) == 1.0


def test_weekly_validation_thresholds(monkeypatch):
    import etf_temperature.weekly as weekly_module

    universe = pd.DataFrame(
        [
            {"etf_code": "510050", "is_a_share_equity_etf": True},
            {"etf_code": "510300", "is_a_share_equity_etf": True},
        ]
    )
    membership = pd.DataFrame(
        [
            {"etf_code": "510050", "etf_name": "50ETF", "tracking_index_code": "000016", "tracking_index_name": "上证50指数", "primary_cluster": "C01", "primary_cluster_name": "上证50", "classification_basis": "index_code:000016", "source": "config/cluster_rules.yaml"},
            {"etf_code": "510300", "etf_name": "300ETF", "tracking_index_code": "000300", "tracking_index_name": "沪深300指数", "primary_cluster": "C02", "primary_cluster_name": "沪深300", "classification_basis": "index_code:000300", "source": "config/cluster_rules.yaml"},
        ]
    )

    def fake_read(path):
        return membership if str(path).endswith("etf_cluster_membership.parquet") else universe

    monkeypatch.setattr(weekly_module, "read_dataframe", fake_read)
    monkeypatch.setattr(weekly_module, "load_core_anchors", lambda: {
        "C01": {"required_codes": ["510050"]},
        "C02": {"required_codes": ["510300"]},
    })
    weekly_etf = pd.DataFrame(
        [
            {"week": "2026-W27", "etf_code": "510050", "etf_name": "50ETF", "primary_cluster": "C01", "trading_days": 5, "week_last_trading_day": "2026-07-03", "week_close": 1.0, "week_end_shares": 100, "week_end_size_100m": 100, "weekly_amount_100m": 10, "weekly_net_subscription_100m": 1.0},
            {"week": "2026-W27", "etf_code": "510300", "etf_name": "300ETF", "primary_cluster": "C02", "trading_days": 5, "week_last_trading_day": "2026-07-03", "week_close": 1.0, "week_end_shares": 100, "week_end_size_100m": 100, "weekly_amount_100m": 10, "weekly_net_subscription_100m": 1.0},
        ]
    )
    weekly_cluster = pd.DataFrame(
        [
            {
                "week": "2026-W27",
                "cluster_id": f"C{i:02d}",
                "cluster_name": f"集群{i:02d}",
                "member_count": 1 if i <= 2 else 0,
                "total_size_100m": 100 if i <= 2 else 0,
                "flow_size_coverage": 1.0 if i <= 2 else 0.0,
            }
            for i in range(1, 37)
        ]
    )
    validation = validate_weekly_data("2026-W27", weekly_etf, weekly_cluster, today=date(2026, 7, 4))
    assert validation["formal_weekly_allowed"]
    incomplete = weekly_etf.assign(trading_days=4, week_last_trading_day="2026-07-02")
    validation = validate_weekly_data("2026-W27", incomplete, weekly_cluster, today=date(2026, 7, 4))
    assert not validation["formal_weekly_allowed"]


def test_weekly_report_renders_trend_sections(tmp_path):
    weekly_cluster = pd.DataFrame(
        [
            {"cluster_id": "C01", "cluster_name": "上证50", "member_count": 1, "total_size_100m": 100, "weekly_net_subscription_100m": 5, "weighted_pct_change": 1, "total_amount_100m": 20, "weekly_amount_vs_4w": 1.1, "share_increase_member_ratio": 1, "cluster_temperature": 62, "weekly_temperature_change": 5, "max_contributor_code_1d": "510050", "max_contributor_name_1d": "50ETF", "max_contributor_ratio_1d": 0.6, "top3_contributor_ratio_1d": 1.0},
            {"cluster_id": "C04", "cluster_name": "中证500", "member_count": 1, "total_size_100m": 80, "weekly_net_subscription_100m": -3, "weighted_pct_change": -1, "total_amount_100m": 15, "weekly_amount_vs_4w": 1.4, "share_increase_member_ratio": 0, "cluster_temperature": 45, "weekly_temperature_change": -4, "max_contributor_code_1d": "510500", "max_contributor_name_1d": "500ETF", "max_contributor_ratio_1d": 0.4, "top3_contributor_ratio_1d": 0.9},
            {"cluster_id": "C11", "cluster_name": "红利", "member_count": 1, "total_size_100m": 60, "weekly_net_subscription_100m": 2, "weighted_pct_change": 0.5, "total_amount_100m": 8, "weekly_amount_vs_4w": 1.0, "share_increase_member_ratio": 1, "cluster_temperature": 55, "weekly_temperature_change": 1, "max_contributor_code_1d": "510880", "max_contributor_name_1d": "红利ETF", "max_contributor_ratio_1d": 0.5, "top3_contributor_ratio_1d": 1.0},
            {"cluster_id": "C16", "cluster_name": "半导体", "member_count": 1, "total_size_100m": 70, "weekly_net_subscription_100m": 4, "weighted_pct_change": 2, "total_amount_100m": 18, "weekly_amount_vs_4w": 1.5, "share_increase_member_ratio": 1, "cluster_temperature": 70, "weekly_temperature_change": 8, "max_contributor_code_1d": "512480", "max_contributor_name_1d": "半导体ETF", "max_contributor_ratio_1d": 0.7, "top3_contributor_ratio_1d": 1.0},
        ]
    )
    weekly_etf = pd.DataFrame(
        [
            {"etf_code": "510050", "etf_name": "50ETF", "primary_cluster_name": "上证50", "weekly_pct_change": 1, "weekly_net_subscription_100m": 5, "trading_days": 5, "flow_positive_days": 5, "flow_negative_days": 0, "first_temperature": 50, "week_end_temperature": 62, "weekly_temperature_change": 12},
            {"etf_code": "510500", "etf_name": "500ETF", "primary_cluster_name": "中证500", "weekly_pct_change": -1, "weekly_net_subscription_100m": -3, "trading_days": 5, "flow_positive_days": 0, "flow_negative_days": 5, "first_temperature": 65, "week_end_temperature": 45, "weekly_temperature_change": -20},
        ]
    )
    snapshot = {
        "market": {"total_size_100m": 310, "weekly_net_subscription_100m": 8, "weekly_amount_100m": 61, "weekly_etf_count": 2},
        "validation": {
            "formal_weekly_allowed": True,
            "trading_days": 5,
            "etf_coverage": 1.0,
            "week_start": "2026-06-29",
            "week_end": "2026-07-05",
            "week_first_trading_day": "2026-06-29",
            "week_last_trading_day": "2026-07-03",
        },
    }
    path = tmp_path / "A股ETF资金温度周报_2026-W27.md"
    generate_weekly_report("2026-W27", snapshot, weekly_etf, weekly_cluster, path)
    text = path.read_text(encoding="utf-8")
    assert "统计区间：2026-06-29 至 2026-07-03" in text
    assert "自然周：2026-06-29 至 2026-07-05" in text
    assert "本周总判断" in text
    assert "单日异动与持续趋势" in text
    assert "集群资金迁移Top5" in text
    assert "观察集群各类周度排名" not in text
    assert "单只ETF周度完整排行榜Top50" not in text
    appendix = path.with_name("A股ETF资金结构周报_技术附录_2026-W27.md").read_text(encoding="utf-8")
    assert "观察集群各类周度排名" in appendix
    assert "周度估算净申购Top50" in appendix
    assert "本周最后一个交易日收盘价 / 上周最后一个交易日收盘价 - 1" in text
    assert "成交额只表示二级市场换手" in text


def test_rebuild_history_failure_is_not_silent(monkeypatch, tmp_path):
    import etf_temperature.pipeline as pipeline

    def fail_run(date_text, client=None, **kwargs):
        raise RuntimeError("boom")

    # rebuild_history() performs one batch prefetch before the per-day loop.
    # Unit tests must not touch the real exchange network.
    monkeypatch.setattr(
        pipeline,
        "prefetch_rebuild_market_data",
        lambda start, end, client=None: {
            "start": start,
            "end": end,
            "quote_cache_hit": True,
            "share_cache_hit": True,
            "fetched_quote_rows": 0,
            "fetched_share_rows": 0,
        },
    )
    monkeypatch.setattr(pipeline, "run_pipeline", fail_run)
    monkeypatch.setattr(pipeline, "_local_snapshot_available", lambda date_text: False)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path)
    with pytest.raises(RuntimeError):
        pipeline.rebuild_history("2026-07-01", "2026-07-01")
    assert list(tmp_path.glob("rebuild_errors_*.json"))
