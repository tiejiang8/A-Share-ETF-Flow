from etf_temperature.anchors import anchor_required_codes, load_core_anchors


def test_core_anchor_config_contains_required_codes():
    anchors = load_core_anchors()
    codes = set(anchor_required_codes(anchors))
    assert {"510050", "510300", "510500", "512100", "159915", "159949", "588000", "510880"}.issubset(codes)
    assert {"512480", "512760", "159995"} & codes
