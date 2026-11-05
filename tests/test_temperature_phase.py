from etf_temperature.temperature import temperature_direction, temperature_phase_from_values


def test_temperature_phase_distinguishes_level_direction_and_stage():
    assert temperature_direction(15, 12, 10) == "快速升温"
    assert temperature_phase_from_values(54, 12, 10, 8) == "中温转强"
    assert temperature_phase_from_values(86, -13, -8, -5, cooling_from_high=True) == "高温降温"
    assert temperature_phase_from_values(25, 8, 6, 5) == "冷启动"
