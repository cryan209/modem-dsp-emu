#!/usr/bin/env python3
"""Exercise native call setup and N_DATA acceptance in the original MIPS.

No physical line or remote peer is present. N_DATA acceptance verifies the
firmware request path, not wire delivery, received payload, or PPP.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import eicon_mips_shim as firmware


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    original = firmware.run_mainloop
    result = {'profile': args.profile, 'firmware_executed': True,
              'physical_line': False, 'peer_payload_verified': False}

    def probe(shim, config):
        original(shim, config)
        sr, gp, sp = shim.idi_context
        result['bearer_connected'] = shim.nl_connected
        payload = bytes(range(256))
        result['payload_sha256'] = hashlib.sha256(payload).hexdigest()
        firmware.post_request(shim, sr, firmware.eicon_idi.N_DATA,
                              shim.nl_entity_id, 2, payload, reference=17)
        codes = firmware.run_until_rc(shim, sr, gp, sp, iterations=128,
                                       phase='isdn-payload')
        result['n_data_return_codes'] = codes
        result['n_data_accepted'] = any(
            rc == firmware.eicon_idi.RC_OK and entity == shim.nl_entity_id
            and ref == 17 for rc, entity, channel, ref in codes)
        result['accepted_octets'] = len(payload) if result['n_data_accepted'] else 0
        print('[firmware-probe] ' + json.dumps(result, sort_keys=True))

    firmware.run_mainloop = probe
    saved_argv = sys.argv
    sys.argv = ['eicon_mips_shim', '--kernel',
                'artifacts/eicon-dsp/build-117-926/0009-diva-server-pri-30m-kernel',
                '--tikrnl', 'artifacts/eicon-dsp/build-117-926/0258-tikrnl81.f34-task',
                '--mainloop', '--isdn-profile', str(args.profile),
                '--simulate-outgoing-call', '--call-steps', '128']
    try:
        firmware.main()
    finally:
        firmware.run_mainloop = original
        sys.argv = saved_argv
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result.get('bearer_connected') and result.get('n_data_accepted') else 1


if __name__ == '__main__':
    raise SystemExit(main())
