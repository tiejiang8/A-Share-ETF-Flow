"""Tests for the AI-readable summary artifacts.

These cover the guarantees the external ``national-team-etf-flow`` repository
does *not* provide: missing values are never rendered as zero, the economic
date of every input is explicit, coverage always accompanies the numbers, and
unchanged content is not rewritten.
"""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pandas as pd
import pytest

from etf_temperature import summary as summary_module
from etf_temperature.summary import build_summary, write_summary_artifacts


DATE = "2026-11-04"


def _etf_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": DATE,
                "etf_code": "510300",
                "etf_name": "300ETF",
                "primary_cluster": "C02",
                "primary_cluster_name": "大盘宽基",
                "estimated_net_subscription_100m": 10.0,
                "estimated_net_subscription_5d_100m": 20.0,
                "fund_size_100m": 100.0,
                "amount": 1_000_000_000.0,
                "share_daily_change_pct": 0.01,
                "nav": 4.0,
                "nav_data_date": DATE,
                "share_data_date": DATE,
                "quote_data_date": DATE,
                "pct_change": 1.0,
                "flow_validation_status": "ok",
                "share_flow_excluded": False,
            },
            {
                "date": DATE,
                "etf_code": "512480",
                "etf_name": "半导体",
                "primary_cluster": "C16",
                "primary_cluster_name": "半导体/芯片",
                "estimated_net_subscription_100m": -3.0,
                "estimated_net_subscription_5d_100m": -5.0,
                "fund_size_100m": 50.0,
                "amount": 500_000_000.0,
                "share_daily_change_pct": -0.02,
                "nav": 1.0,
                "nav_data_date": DATE,
                "share_data_date": DATE,
                "quote_data_date": DATE,
                "pct_change": -2.0,
                "flow_validation_status": "ok",
                "share_flow_excluded": False,
            },
            {
                # No flow measurement at all: must not be counted as zero.
                "date": DATE,
                "etf_code": "560650",
                "etf_name": "核心50",
                "primary_cluster": "",
                "primary_cluster_name": None,
                "estimated_net_subscription_100m": None,
                "estimated_net_subscription_5d_100m": None,
                "fund_size_100m": 0.08,
                "amount": None,
                "share_daily_change_pct": None,
                "nav": 1.04,
                "nav_data_date": "2026-07-02",
                "share_data_date": None,
                "quote_data_date": None,
                "pct_change": None,
                "flow_validation_status": "unresolved",
                "share_flow_excluded": False,
            },
        ]
    )


def _cluster_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": DATE,
                "cluster_id": "C02",
                "cluster_name": "大盘宽基",
                "member_count": 1,
                "total_size_100m": 100.0,
                "estimated_net_subscription_100m": 10.0,
                "estimated_net_subscription_5d_100m": 20.0,
                "flow_size_coverage": 1.0,
                "one_day_flow_member_coverage": 1.0,
                "cluster_temperature": 74.9,
                "temperature_level": "偏热",
                "temperature_direction": "温和升温",
                "temperature_phase": "持续偏热",
                "flow_structure_label": "头部产品集中流入",
                "structure_judgement": "价格与资金同步增强",
                "gross_inflow": 10.0,
                "gross_outflow": 0.0,
                "max_contributor_name_1d": "300ETF",
                "max_contributor_flow_1d_100m": 10.0,
                "top3_contributor_ratio_1d": 1.0,
                "weighted_pct_change": 1.0,
            },
            {
                "date": DATE,
                "cluster_id": "C16",
                "cluster_name": "半导体/芯片",
                "member_count": 1,
                "total_size_100m": 50.0,
                "estimated_net_subscription_100m": -3.0,
                "estimated_net_subscription_5d_100m": -5.0,
                "flow_size_coverage": 1.0,
                "one_day_flow_member_coverage": 1.0,
                "cluster_temperature": 55.0,
                "temperature_level": "中性",
                "temperature_direction": "降温",
                "temperature_phase": "中温转强",
                "flow_structure_label": "头部产品集中流出",
                "structure_judgement": "结构分化",
                "gross_inflow": 0.0,
                "gross_outflow": -3.0,
                "max_contributor_name_1d": "半导体",
                "max_contributor_flow_1d_100m": -3.0,
                "top3_contributor_ratio_1d": 1.0,
                "weighted_pct_change": -2.0,
            },
        ]
    )


def _metrics() -> dict:
    return {
        "a_share_equity_etf_count": 3,
        "quote_coverage": 2 / 3,
        "share_coverage": 2 / 3,
        "nav_coverage": 1.0,
        "quote_fresh_coverage": 2 / 3,
        "share_fresh_coverage": 2 / 3,
        "nav_fresh_coverage": 2 / 3,
        "flow_member_coverage": 2 / 3,
        "flow_size_coverage": 1.0,
        "weighted_share_coverage": 1.0,
        "missing_flow_size_100m": 0.0,
        "official_ratio": 1.0,
        "browser_fallback_count": 0,
        "third_party_fallback_count": 0,
        "date_mismatch_count": 0,
        "stale_quote_count": 1,
        "stale_share_count": 1,
        "stale_nav_count": 1,
        "max_nav_lag_days": 125,
        "extreme_flow_trigger_count": 1,
        "extreme_flow_unresolved_count": 0,
        "unclassified_count": 1,
        "missing_quote_codes": ["560650"],
        "missing_share_codes": ["560650"],
        "missing_nav_codes": [],
        "formal_blockers": [],
        "formal_report_allowed": True,
    }


def test_missing_flow_is_not_counted_as_zero():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    market = payload["market"]
    assert market["flow_1d_100m"] == 7.0
    assert market["flow_valid_etf_count"] == 2
    assert market["flow_missing_etf_count"] == 1
    # The unresolved row must not leak into the gross flow figures either.
    assert market["gross_inflow_100m"] == 10.0
    assert market["gross_outflow_100m"] == -3.0


def test_coverage_always_accompanies_the_numbers():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    coverage = payload["coverage"]
    assert coverage["quote_coverage"] == 2 / 3
    assert coverage["flow_member_coverage"] == 2 / 3
    assert coverage["missing_quote_codes"] == ["560650"]
    assert coverage["max_nav_lag_days"] == 125
    assert "未以零填充" in coverage["coverage_note"]


def test_economic_dates_are_explicit_and_lagging_nav_is_listed():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    dates = payload["data_dates"]
    assert dates["quote_date"] == DATE
    assert dates["share_date"] == DATE
    assert dates["nav_date"] == DATE
    # The dormant fund carries a stale NAV date; it must be visible, not hidden.
    assert dates["nav_date_mismatch_count"] == 1
    assert "560650" in dates["nav_date_mismatch_codes"]


def test_summary_refuses_to_claim_subscriber_identity():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    note = payload["scope"]["identity_note"]
    assert "无法识别" in note
    assert payload["method"]["net_subscription_formula"].startswith("净申购金额")


def test_preview_status_is_published_when_gate_fails():
    metrics = _metrics()
    metrics["formal_report_allowed"] = False
    metrics["formal_blockers"] = ["行情覆盖率不足"]

    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), metrics)

    assert payload["report_status"] == "预览/数据待补全"
    assert payload["formal_blockers"] == ["行情覆盖率不足"]


def test_cluster_and_unclassified_scope_difference_is_disclosed():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    market = payload["market"]
    # ETF scope sums to 7.0; cluster scope only sees the two classified rows.
    assert market["flow_1d_100m"] == 7.0
    assert market["cluster_scope_flow_1d_100m"] == 7.0
    assert market["unclassified_flow_1d_100m"] is None
    assert "口径差异" in market["scope_note"]


def test_write_artifacts_creates_three_files_and_skips_unchanged_rewrite(tmp_path: Path):
    output_dir = tmp_path / DATE
    outputs_root = tmp_path

    first = write_summary_artifacts(
        output_dir, DATE, _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=outputs_root
    )

    assert first["files_changed"]["summary.json"] is True
    assert first["files_changed"]["llms.txt"] is True
    assert first["files_changed"]["snapshot.md"] is True
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "llms.txt").exists()
    assert (output_dir / "snapshot.md").exists()
    assert (outputs_root / "latest.json").exists()
    assert (outputs_root / "summary_index.json").exists()

    # Second run with identical inputs must not rewrite anything.
    second = write_summary_artifacts(
        output_dir, DATE, _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=outputs_root
    )
    assert second["files_changed"] == {
        "summary.json": False,
        "llms.txt": False,
        "snapshot.md": False,
    }
    assert second["index_changed"] is False
    assert second["latest_pointer_changed"] is False
    assert second["content_hash"] == first["content_hash"]


def test_summary_payload_contains_no_nan_or_infinity():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    text = json.dumps(summary_module._json_safe(payload), ensure_ascii=False, allow_nan=False)
    assert "NaN" not in text
    assert "Infinity" not in text


def test_llms_text_states_missing_count_and_limits():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())

    text = summary_module._render_llms(payload)
    assert "缺失 1 只，未填零" in text
    assert "无法识别申购者" in text
    assert "覆盖率" in text


def test_latest_pointer_never_moves_backwards(tmp_path: Path):
    outputs_root = tmp_path
    newer_dir = outputs_root / "2026-11-04"
    older_dir = outputs_root / "2026-11-03"

    write_summary_artifacts(
        newer_dir, "2026-11-04", _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=outputs_root
    )
    pointer = json.loads((outputs_root / "latest.json").read_text(encoding="utf-8"))
    assert pointer["asof"] == "2026-11-04"

    result = write_summary_artifacts(
        older_dir, "2026-11-03", _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=outputs_root
    )
    pointer = json.loads((outputs_root / "latest.json").read_text(encoding="utf-8"))
    assert pointer["asof"] == "2026-11-04"
    assert result["latest_pointer_changed"] is False


def test_index_appends_one_row_per_date(tmp_path: Path):
    outputs_root = tmp_path

    write_summary_artifacts(
        outputs_root / "2026-11-03", "2026-11-03", _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=outputs_root
    )
    write_summary_artifacts(
        outputs_root / "2026-11-04", "2026-11-04", _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=outputs_root
    )

    index = json.loads((outputs_root / "summary_index.json").read_text(encoding="utf-8"))
    assert [entry["asof"] for entry in index["entries"]] == ["2026-11-03", "2026-11-04"]


def test_missing_columns_do_not_crash_the_summary():
    """A sparse frame must degrade, not raise.

    ``pd.to_numeric(frame.get(column))`` returns a bare NaN when the column is
    absent, which then breaks chained ``.notna()`` calls.  The summary must
    tolerate frames that lack the optional columns entirely.
    """
    sparse = pd.DataFrame({"etf_code": ["510300"], "date": [DATE]})
    payload = build_summary(DATE, sparse, pd.DataFrame(), {"a_share_equity_etf_count": 1})

    assert payload["market"]["flow_1d_100m"] is None
    assert payload["market"]["flow_valid_etf_count"] == 0
    assert payload["clusters"] == []
    assert payload["top_inflow_etfs"] == []


def test_presence_and_freshness_are_distinct_in_json_and_text(tmp_path: Path):
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())
    coverage = payload["coverage"]
    assert coverage["nav_coverage"] == 1.0
    assert coverage["nav_fresh_coverage"] == 2 / 3
    for label in ["quote", "share", "nav"]:
        assert coverage[f"{label}_fresh_coverage"] == 2 / 3
    for text in [summary_module._render_llms(payload), summary_module._render_snapshot(payload)]:
        assert "字段有值率" in text and "净值 100.00%" in text
        assert "时点有效率" in text and "净值 66.67%" in text

    write_summary_artifacts(tmp_path / DATE, DATE, _etf_rows(), _cluster_rows(), _metrics(), outputs_dir=tmp_path)
    pointer = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    entry = json.loads((tmp_path / "summary_index.json").read_text(encoding="utf-8"))["entries"][0]
    for artifact in [pointer, entry]:
        assert artifact["nav_coverage"] == 1.0
        for label in ["quote", "share", "nav"]:
            assert artifact[f"{label}_fresh_coverage"] == 2 / 3


def test_absent_freshness_is_unknown_instead_of_reusing_presence():
    metrics = _metrics()
    for label in ["quote", "share", "nav"]:
        del metrics[f"{label}_fresh_coverage"]
    coverage = build_summary(DATE, _etf_rows(), _cluster_rows(), metrics)["coverage"]
    assert coverage["nav_coverage"] == 1.0
    assert all(coverage[f"{label}_fresh_coverage"] is None for label in ["quote", "share", "nav"])


def test_flow_formula_declares_share_nav_units_and_yuan_conversion():
    payload = build_summary(DATE, _etf_rows(), _cluster_rows(), _metrics())
    method = payload["method"]
    assert method["share_unit"] == "份"
    assert method["nav_unit"] == "元/份"
    assert method["yuan_per_100m"] == 100_000_000
    assert "÷ 1e8" in method["net_subscription_formula"]
    # Known independent example: 25 million shares at 4 yuan is one 100m yuan.
    assert 25_000_000 * 4 / method["yuan_per_100m"] == 1.0
    assert "÷ 1e8" in summary_module._render_llms(payload)


@pytest.mark.parametrize(
    ("flows", "expected_inflow", "expected_outflow"),
    [
        ([10.0, -3.0, 0.0, None], [10.0], [-3.0]),
        ([2.0, 10.0], [10.0, 2.0], []),
        ([-2.0, -10.0], [], [-10.0, -2.0]),
        ([0.0, None], [], []),
    ],
)
def test_directional_rankings_only_include_their_own_sign(flows, expected_inflow, expected_outflow):
    frame = pd.DataFrame({"etf_code": [f"{index:06d}" for index in range(len(flows))], "estimated_net_subscription_100m": flows})
    payload = build_summary(DATE, frame, pd.DataFrame(), {})
    assert [row["flow_1d_100m"] for row in payload["top_inflow_etfs"]] == expected_inflow
    assert [row["flow_1d_100m"] for row in payload["top_outflow_etfs"]] == expected_outflow


def _publish_in_process(outputs_root, report_date, started, writing_files, index_ready=None, release=None):
    """Pause an older writer after reading the index to expose lost updates."""
    original_write = summary_module._write_if_changed

    def observed_write(path, text):
        if path.name == summary_module.SUMMARY_JSON_NAME:
            writing_files.set()
        if index_ready is not None and path.name == summary_module.SUMMARY_INDEX_NAME:
            index_ready.set()
            if not release.wait(15):
                raise TimeoutError("test did not release the older summary writer")
        return original_write(path, text)

    summary_module._write_if_changed = observed_write
    etf = _etf_rows().assign(date=report_date)
    clusters = _cluster_rows().assign(date=report_date)
    started.set()
    write_summary_artifacts(Path(outputs_root) / report_date, report_date, etf, clusters, _metrics(), outputs_dir=Path(outputs_root))


def test_concurrent_publication_preserves_latest_and_both_index_entries(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    older_started, newer_started = context.Event(), context.Event()
    older_writing, newer_writing = context.Event(), context.Event()
    index_ready, release = context.Event(), context.Event()
    older = context.Process(target=_publish_in_process, args=(str(tmp_path), "2026-11-03", older_started, older_writing, index_ready, release))
    newer = context.Process(target=_publish_in_process, args=(str(tmp_path), DATE, newer_started, newer_writing))
    older.start()
    try:
        assert index_ready.wait(15), "older writer never reached its index update"
        newer.start()
        assert newer_started.wait(15), "newer writer did not start"
        # The whole publication, including dated files, must wait for the lock.
        assert not newer_writing.wait(0.5)
    finally:
        release.set()
        for process in [older, newer]:
            if process.pid is not None:
                process.join(timeout=15)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
    assert older.exitcode == 0
    assert newer.exitcode == 0
    pointer = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    entries = json.loads((tmp_path / "summary_index.json").read_text(encoding="utf-8"))["entries"]
    assert pointer["asof"] == DATE
    assert [entry["asof"] for entry in entries] == ["2026-11-03", DATE]
    for entry in entries:
        dated = json.loads((tmp_path / entry["asof"] / "summary.json").read_text(encoding="utf-8"))
        assert entry["content_hash"] == dated["content_hash"]
    assert pointer["content_hash"] == entries[-1]["content_hash"]
