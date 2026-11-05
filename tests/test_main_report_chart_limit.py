from pathlib import Path

import pandas as pd

from etf_temperature.report import generate_report


def _cluster_rows():
    return pd.DataFrame(
        [
            {"cluster_id": f"C{i:02d}", "cluster_name": f"集群{i:02d}", "member_count": 1, "total_size_100m": 100, "estimated_net_subscription_100m": i - 16, "one_day_flow_to_size": (i - 16) / 100, "estimated_net_subscription_5d_100m": i, "weighted_pct_change": i / 10 - 2, "amount_vs_5d": 1, "cluster_temperature": 50 + i % 30, "temperature_change": i % 5 - 2, "temperature_direction": "稳定", "temperature_level": "中性", "temperature_phase": "剧烈分化", "structure_judgement": "结构分化"}
            for i in range(1, 32)
        ]
    )


def test_main_report_embeds_no_more_than_5_kline_charts(tmp_path):
    chart_paths = []
    for i in range(20):
        p = tmp_path / f"510{i:03d}_kline.png"
        p.write_text("x", encoding="utf-8")
        chart_paths.append(p)
    etf_daily = pd.DataFrame({"etf_code": [f"510{i:03d}" for i in range(20)], "etf_name": ["ETF"] * 20, "temperature": [50] * 20})
    path = tmp_path / "A股ETF资金温度日报_2026-07-02.md"
    generate_report("2026-07-02", path, {"a_share_equity_etf_count": 31, "formal_report_allowed": True}, {"etf_total": 31}, _cluster_rows(), etf_daily, pd.DataFrame({"cluster_id": [], "representative_code": [], "representative_name": []}), pd.DataFrame(), chart_paths)
    assert path.read_text(encoding="utf-8").count("![") <= 5
