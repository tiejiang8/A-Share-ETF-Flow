import pandas as pd

from etf_temperature.narrative import validate_narrative


def test_narrative_consistency_blocks_banned_phrases():
    df = pd.DataFrame([{"cluster_id": "C16", "cluster_name": "半导体", "weighted_pct_change": -5, "total_size_100m": 100}])
    result = validate_narrative("成交额显示资金流入，真实申购增强。", df)
    assert not result["narrative_consistency_status"]
    assert "真实申购" in result["narrative_violations"]
