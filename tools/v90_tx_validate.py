#!/usr/bin/env python3
"""Validate V.90 downstream Phase-3 PCM codewords.

The input is the endpoint's raw PCMU transmit capture (``runNN.ulaw``), not a
linear WAV conversion.  V.90 defines Sd, S-bar-d, and TRN1d in Ucodes, so
validation after G.711 encoding catches polarity-zero and quantisation errors
that a waveform comparison hides.

Example:
    python3 tools/v90_tx_validate.py artifacts/eicon-native-tower/run40.ulaw \
        --uinfo 48

``UINFO`` is INFO1a bits 25:31.  For PCMU, Table 1/V.90 maps Ucode ``u`` to
positive codeword ``0xff-u``; clearing bit 7 gives the negative polarity.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Phase3Result:
    start: int | None
    normalized_start: int | None
    trn1_start: int | None
    trn1_length: int
    trn1_ucode: int | None
    trn1_checked: int
    trn1_sign_errors: int


def pcmu_code(ucode: int, positive: bool) -> int:
    if not 0 <= ucode <= 127:
        raise ValueError("Ucode must be in 0..127")
    code = 0xFF - ucode
    return code if positive else code & 0x7F


def pcmu_ucode(code: int) -> int:
    return 0xFF - ((code & 0xFF) | 0x80)


def sd_patterns(uinfo: int) -> tuple[bytes, bytes]:
    if not 0 <= uinfo <= 111:
        raise ValueError("UINFO must be in 0..111 so 16 + UINFO is a Ucode")
    w = uinfo + 16
    sd = bytes((pcmu_code(w, True), pcmu_code(0, True), pcmu_code(w, True),
                pcmu_code(w, False), pcmu_code(0, False), pcmu_code(w, False)))
    sbar = bytes((pcmu_code(w, False), pcmu_code(0, False), pcmu_code(w, False),
                  pcmu_code(w, True), pcmu_code(0, True), pcmu_code(w, True)))
    return sd, sbar


def phase3_prefix(uinfo: int) -> bytes:
    sd, sbar = sd_patterns(uinfo)
    return sd * 64 + sbar * 8


def _normalize_zero(code: int) -> int:
    return 0xFF if code in (0x7F, 0xFF) else code


def _find_normalized(data: bytes, pattern: bytes) -> int | None:
    normalized_data = bytes(_normalize_zero(code) for code in data)
    normalized_pattern = bytes(_normalize_zero(code) for code in pattern)
    start = normalized_data.find(normalized_pattern)
    return None if start < 0 else start


def trn1_signs(length: int) -> tuple[int, ...]:
    """GPC output for all-one input, zero initial state (V.34 equation 7-1)."""
    output: list[int] = []
    for index in range(length):
        bit = 1
        if index >= 18:
            bit ^= output[index - 18]
        if index >= 23:
            bit ^= output[index - 23]
        output.append(bit)
    return tuple(output)


def analyze(data: bytes, uinfo: int) -> Phase3Result:
    prefix = phase3_prefix(uinfo)
    start = data.find(prefix)
    normalized = None if start >= 0 else _find_normalized(data, prefix)
    anchor = start if start >= 0 else normalized
    trn_start = None
    trn_length = 0
    trn_ucode = None
    trn_checked = 0
    trn_errors = 0
    if anchor is not None:
        trn_start = anchor + len(prefix)
        if trn_start < len(data):
            trn_ucode = pcmu_ucode(data[trn_start])
            index = trn_start
            while index < len(data) and pcmu_ucode(data[index]) == trn_ucode:
                index += 1
            trn_length = index - trn_start
            trn_checked = min(2040, trn_length)
            expected_signs = trn1_signs(trn_checked)
            observed_signs = tuple(
                1 if code & 0x80 else 0
                for code in data[trn_start:trn_start + trn_checked])
            trn_errors = sum(a != b for a, b in
                             zip(observed_signs, expected_signs))
    return Phase3Result(
        None if start < 0 else start, normalized, trn_start, trn_length,
        trn_ucode, trn_checked, trn_errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--uinfo", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--strict", action="store_true",
                        help="return failure if the exact prefix or TRN1d Ucode is wrong")
    args = parser.parse_args()

    data = args.capture.read_bytes()
    result = analyze(data, args.uinfo)
    expected_w = args.uinfo + 16
    print(f"{args.capture}: {len(data)} PCMU symbols, UINFO={args.uinfo}, "
          f"Sd W Ucode={expected_w}")
    if result.start is not None:
        print(f"  exact Sd(384T) + S-bar-d(48T): sample {result.start} "
              f"({result.start / 8000:.6f}s)")
    elif result.normalized_start is not None:
        print(f"  polarity-zero-normalized Sd + S-bar-d only: sample "
              f"{result.normalized_start} ({result.normalized_start / 8000:.6f}s)")
        print("  violation: negative-zero 0x7f was emitted as positive-zero 0xff")
    else:
        print("  no complete Sd + S-bar-d sequence found")

    if result.trn1_start is not None and result.trn1_ucode is not None:
        verdict = "correct" if result.trn1_ucode == args.uinfo else "WRONG"
        print(f"  following constant-Ucode run: sample {result.trn1_start}, "
              f"Ucode={result.trn1_ucode}, {result.trn1_length}T ({verdict}; "
              f"TRN1d requires UINFO={args.uinfo})")
        sign_verdict = "correct" if result.trn1_sign_errors == 0 else "WRONG"
        print(f"  GPC TRN1d signs: {result.trn1_checked}T checked, "
              f"{result.trn1_sign_errors} errors ({sign_verdict})")

    valid = (result.start is not None and result.trn1_ucode == args.uinfo
             and result.trn1_checked == 2040 and result.trn1_sign_errors == 0)
    return 1 if args.strict and not valid else 0


if __name__ == "__main__":
    raise SystemExit(main())
