#!/usr/bin/env python3
"""Probe actual AT-to-native IDI configuration without opening a bearer.

An accepted AT profile is not a payload-transfer pass. Compare the descriptors
built by the SIP endpoint/native shim against the shipped tty driver before
attempting a call. Exit 1 when any selected profile builds the wrong stack.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path

import eicon_idi as idi
import eicon_mips_shim as shim
from eicon_at import AtParser, PROFILES
from eicon_adsp_sip import EiconSipEndpoint

# AT.txt profiles; tty_module/isdn.h and isdn.c:protmap/putcai/assign_nl.
# Tuple: B1, answer-side B2, calling-side B2, B3.
EXPECTED_STACKS = {
    1: (0x05, 0x01, 0x01, 0x04),   # HDLC / X.75 / transparent
    2: (0x0C, 0x03, 0x02, 0x04),   # V.110 synchronous
    3: (0x0D, 0x03, 0x02, 0x04),   # V.110 asynchronous
    5: (0x11, 0x09, 0x0A, 0x04),   # modem control, V.42 LLC
    6: (0x05, 0x08, 0x08, 0x04),   # V.120 64k
    7: (0x08, 0x08, 0x08, 0x04),   # V.120 56k: putcai selects HDLC_56
    8: (0x09, 0x03, 0x02, 0x04),   # bit-transparent
    9: (0x05, 0x03, 0x02, 0x04),   # HDLC / transparent (PPP)
    10: (0x08, 0x03, 0x02, 0x04),  # HDLC 56k / transparent
    15: (0x05, 0x0C, 0x0C, 0x04),  # X.75 with V.42bis
}
NATIVE_PROFILES = (1, 2, 3, 6, 7, 8, 9, 10, 15)


def compare_stack(expected, b1, answer_llc, caller_llc):
    actual = (b1, answer_llc[0], caller_llc[0], answer_llc[1])
    differences = [f'{field}: expected 0x{want:02x}, got 0x{got:02x}'
                   for field, want, got in zip(
                       ('B1', 'B2 answer', 'B2 caller', 'B3 answer'), expected, actual)
                   if want != got]
    if caller_llc[1] != expected[3]:
        differences.append(f'B3 caller: expected 0x{expected[3]:02x}, '
                           f'got 0x{caller_llc[1]:02x}')
    return differences


def probe_profile(profile, user_rate=38400):
    at = AtParser()
    command = f'AT&F{profile}'
    if profile in (2, 3):
        command += '+IB' + str({9600: 5, 19200: 6, 38400: 7}[user_rate])
    response, forwarded = at.feed((command + '\r').encode())
    if at.profile != profile or b'ERROR' in response or forwarded:
        raise RuntimeError(f'profile {profile} was not selected')
    # Use the actual endpoint configuration handoff and native builders.
    # No socket, firmware execution, or fabricated payload success is involved.
    endpoint = EiconSipEndpoint.__new__(EiconSipEndpoint)
    endpoint.at = at
    endpoint.native_mips = True
    previous = shim._MODEM_OPTIONS_OVERRIDE
    previous_isdn = shim._ISDN_OPTIONS_OVERRIDE
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            endpoint.at_apply_options()
        sig = shim.modem_sig_assign_payload()
        call_req = shim.call_req_payload('6001')
        cai = dict(idi.parse_idi_parameters(sig))[idi.IDI_CAI]
        assignments = [shim.modem_nl_assign_payload(answering=answering)
                       for answering in (True, False)]
    finally:
        shim.set_modem_options(previous)
        shim.set_isdn_options(previous_isdn)
    llcs = [dict(idi.parse_idi_parameters(payload))[idi.IDI_LLC]
            for payload in assignments]
    differences = compare_stack(EXPECTED_STACKS[profile], cai[0], *llcs)
    return {
        'profile': profile, 'name': PROFILES[profile][0],
        'profile_accepted': True, 'cai_hex': cai.hex(),
        'call_req_hex': call_req.hex(),
        'user_rate': user_rate if profile in (2, 3) else None,
        'sig_assign_hex': sig.hex(),
        'nl_assign_answer_hex': assignments[0].hex(),
        'nl_assign_caller_hex': assignments[1].hex(),
        'expected_stack': list(EXPECTED_STACKS[profile]),
        'actual_stack': [cai[0], llcs[0][0], llcs[1][0], llcs[0][1]],
        'differences': differences,
        'configuration_matches': not differences,
        'payload_test': 'not_run_wrong_stack' if differences else 'not_run',
        'payload_transfer_verified': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=int, action='append',
                        choices=tuple(EXPECTED_STACKS))
    parser.add_argument('--json', type=Path, help='save all descriptors/results')
    args = parser.parse_args()
    results = [probe_profile(p) for p in (args.profile or (*NATIVE_PROFILES, 5))]
    for result in results:
        status = 'BLOCKED' if result['differences'] else 'CONFIG MATCH'
        print(f"{result['profile']:2} {result['name']}: {status}")
        for difference in result['differences']:
            print(f'   {difference}')
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2) + '\n')
    print('Payload transfer was not run; configuration matches are not data passes.')
    return int(any(result['differences'] for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
