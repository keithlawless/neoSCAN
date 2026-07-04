"""Tests for CTCSS/DCS tone code translation (scanner_model)."""
from app.serial.scanner_model import (
    ctcss_dcs_tone_options,
    parse_tone_to_code,
    tone_label_for_code,
)


def _codes(model=None):
    return {code for code, _ in ctcss_dcs_tone_options(model)}


def test_ctcss_114_8_is_code_80():
    assert parse_tone_to_code("114.8") == "80"
    assert parse_tone_to_code("114.8 PL") == "80"
    assert parse_tone_to_code("114.8Hz") == "80"
    assert parse_tone_to_code("CTCSS 114.8") == "80"
    assert tone_label_for_code("80") == "114.8 PL"


def test_none_and_search():
    assert parse_tone_to_code("") == "0"
    assert parse_tone_to_code("None") == "0"
    assert parse_tone_to_code("off") == "0"
    assert parse_tone_to_code("Search") == "127"
    assert tone_label_for_code("0") == "None"
    assert tone_label_for_code("127") == "Search"


def test_dcs_forms():
    # DCS 023 is the first DCS code (128).
    assert parse_tone_to_code("D023") == "128"
    assert parse_tone_to_code("023 DPL") == "128"
    assert parse_tone_to_code("DCS 023") == "128"
    assert tone_label_for_code("128") == "DCS 023"


def test_valid_code_passthrough():
    assert parse_tone_to_code("80") == "80"
    assert parse_tone_to_code("128") == "128"


def test_truncated_bug_value_not_mismapped():
    # "114" is the truncated result of the old Hz-passthrough bug. It is NOT a
    # valid code and must not be silently mapped to DCS 114 (code 144).
    assert parse_tone_to_code("114") is None
    assert tone_label_for_code("114") is None


def test_model_dcs_differences():
    # CTCSS codes are identical across models; the digital BCD996P2 has 8 extra
    # DCS codes (232-239) the analog radios lack.
    xt = _codes("BCD996XT")
    bct = _codes("BCT15X")
    p2 = _codes("BCD996P2")
    assert xt == bct
    assert p2 - xt == {"232", "233", "234", "235", "236", "237", "238", "239"}
    # 114.8 PL (80) present everywhere.
    assert "80" in xt and "80" in p2


def test_p2_extra_dcs_parses_only_for_p2():
    # DCS 006 is code 232, valid only on BCD996P2.
    assert parse_tone_to_code("D006", "BCD996P2") == "232"
    assert parse_tone_to_code("D006", "BCT15X") is None
    # Its label still resolves (superset) for display robustness.
    assert tone_label_for_code("232") == "DCS 006"
