from tools.v90_tx_validate import (
    analyze,
    pcmu_code,
    pcmu_ucode,
    phase3_prefix,
    sd_patterns,
    trn1_signs,
)


def test_pc_mu_ucode_table_mapping():
    assert pcmu_code(0, True) == 0xFF
    assert pcmu_code(0, False) == 0x7F
    assert pcmu_code(48, True) == 0xCF
    assert pcmu_code(48, False) == 0x4F
    assert pcmu_ucode(0xCF) == 48
    assert pcmu_ucode(0x4F) == 48


def test_phase3_prefix_matches_v90_lengths_and_patterns():
    sd, sbar = sd_patterns(48)
    assert sd == bytes.fromhex("bfffbf3f7f3f")
    assert sbar == bytes.fromhex("3f7f3fbfffbf")
    prefix = phase3_prefix(48)
    assert len(prefix) == 432
    assert prefix == sd * 64 + sbar * 8


def test_analyze_accepts_exact_prefix_and_trn1_ucode():
    prefix = phase3_prefix(48)
    data = b"\xff" * 19 + prefix + bytes((0xCF, 0x4F)) * 1100 + b"\xff"
    result = analyze(data, 48)
    assert result.start == 19
    assert result.normalized_start is None
    assert result.trn1_start == 451
    assert result.trn1_ucode == 48
    assert result.trn1_length == 2200
    assert result.trn1_checked == 2040
    # The synthetic alternating signs are deliberately not GPC.
    assert result.trn1_sign_errors > 0


def test_analyze_distinguishes_zero_polarity_and_wrong_trn1_ucode():
    prefix = phase3_prefix(48).replace(b"\x7f", b"\xff")
    data = b"\xff" * 7 + prefix + bytes((0xCE, 0x4E)) * 20
    result = analyze(data, 48)
    assert result.start is None
    assert result.normalized_start == 7
    assert result.trn1_ucode == 49
    assert result.trn1_length == 40


def test_trn1_gpc_signs_match_v34_equation_7_1():
    assert trn1_signs(24) == tuple(
        int(bit) for bit in "111111111111111111000001")
    prefix = phase3_prefix(48)
    signs = trn1_signs(2040)
    trn = bytes(pcmu_code(48, bool(sign)) for sign in signs)
    result = analyze(prefix + trn, 48)
    assert result.trn1_checked == 2040
    assert result.trn1_sign_errors == 0
