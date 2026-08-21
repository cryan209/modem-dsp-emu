#!/usr/bin/env python3
"""Drive the sibling V.90 engine one byte-exact G.711 frame at a time.

This is the narrow media seam needed by a future reactive loopback adapter:
each 160-byte input frame is delivered to ``me_rx_g711`` and the matching
160-byte output frame is pulled from ``me_tx_g711``.  The sibling SIP binary
exports those engine symbols, so this keeps the experiment independent of its
SIP/PJMEDIA front end while preserving the engine's live state machine.

The default binary is the locally-built fast-JM control peer.  Override it with
``V90_ENGINE_BINARY`` or ``--binary``.  stdout is binary G.711; diagnostics go
to stderr.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

FRAME_BYTES = 160
DEFAULT_BINARY = Path('/private/tmp/v90a-reactive-peer/sip_v90_modem_fastjm')


class _DiagSnapshot(ctypes.Structure):
    """Prefix-compatible mirror of me_diag_snapshot_t.

    The adapter only reports the fields that describe the V.90 bridge.  Keep
    the complete layout here so ctypes advances through the counters exactly
    as the sibling engine expects when it fills the structure.
    """

    _fields_ = [
        ('state', ctypes.c_int), ('modulation', ctypes.c_int),
        ('law', ctypes.c_int), ('calling_party', ctypes.c_int),
        ('v34_rx_stage', ctypes.c_int), ('v34_tx_stage', ctypes.c_int),
        ('v90_bridge_rx_stage', ctypes.c_int),
        ('v90_bridge_tx_stage', ctypes.c_int),
        ('v90_bridge_rx_event', ctypes.c_int),
        ('v90_phase3_started', ctypes.c_int),
        ('v90_phase3_s_events', ctypes.c_int),
        ('v90_dil_valid', ctypes.c_int),
        ('v90_cp_input_bits', ctypes.c_uint64),
        ('v90_cp_valid_frames', ctypes.c_uint32),
        ('v90_cp_rejected_frames', ctypes.c_uint32),
        ('v92_active', ctypes.c_int), ('v92_trn2u_active', ctypes.c_int),
        ('v92_trn2u_symbols', ctypes.c_uint64),
        ('v92_trn2u_longest_ones', ctypes.c_uint32),
        ('v92_cp_input_bits', ctypes.c_uint64),
        ('v92_cp_valid_frames', ctypes.c_uint32),
        ('v92_cp_rejected_frames', ctypes.c_uint32),
        ('phase_elapsed_ms', ctypes.c_uint64),
        ('g711_rx_octets', ctypes.c_uint64),
        ('g711_tx_octets', ctypes.c_uint64),
        ('g711_raw_v90_tx_octets', ctypes.c_uint64),
        ('g711_linear_tx_octets', ctypes.c_uint64),
    ]


class Engine:
    def __init__(self, binary: Path, law: int, pty: str, verbose: bool,
                 role: str = ''):
        self.lib = ctypes.CDLL(str(binary))
        self.lib.me_init.argtypes = []
        self.lib.me_init.restype = None
        self.lib.me_destroy.argtypes = []
        self.lib.me_destroy.restype = None
        self.lib.me_set_law.argtypes = [ctypes.c_int]
        self.lib.me_set_law.restype = None
        self.lib.me_set_verbose.argtypes = [ctypes.c_int]
        self.lib.me_set_verbose.restype = None
        self.lib.me_on_sip_connected.argtypes = []
        self.lib.me_on_sip_connected.restype = None
        self.lib.me_rx_g711.argtypes = [ctypes.POINTER(ctypes.c_uint8),
                                        ctypes.c_int]
        self.lib.me_rx_g711.restype = None
        self.lib.me_tx_g711.argtypes = [ctypes.POINTER(ctypes.c_uint8),
                                        ctypes.c_int]
        self.lib.me_tx_g711.restype = ctypes.c_int
        self._diag_enabled = verbose
        self._last_diag = None
        if self._diag_enabled and hasattr(self.lib, 'me_get_diag_snapshot'):
            self.lib.me_get_diag_snapshot.argtypes = [
                ctypes.POINTER(_DiagSnapshot)]
            self.lib.me_get_diag_snapshot.restype = None
        self.lib.di_set_callbacks.argtypes = [ctypes.c_void_p] * 4
        self.lib.di_set_callbacks.restype = None
        self.lib.di_open.argtypes = [ctypes.c_char_p]
        self.lib.di_open.restype = ctypes.c_int
        self.lib.di_close.argtypes = []
        self.lib.di_close.restype = None

        self.lib.me_set_verbose(int(verbose))
        # The adapter is loaded inside the Eicon endpoint process, whose own
        # ME_V90_ROLE must remain untouched.  The sibling library reads its
        # role during me_init(), so scope the opt-in role to that call only.
        saved_role = os.environ.get('ME_V90_ROLE')
        if role:
            os.environ['ME_V90_ROLE'] = role
        try:
            self.lib.me_init()
        finally:
            if saved_role is None:
                os.environ.pop('ME_V90_ROLE', None)
            else:
                os.environ['ME_V90_ROLE'] = saved_role
        # The adapter has no AT/DTE owner.  Null callbacks are safe until the
        # engine reports data mode; they also keep this seam media-only.
        self.lib.di_set_callbacks(None, None, None, None)
        if self.lib.di_open(os.fsencode(pty)) != 0:
            self.lib.me_destroy()
            raise RuntimeError(f'di_open failed for {pty}')
        self.lib.me_set_law(law)
        self.lib.me_on_sip_connected()

    def exchange(self, frame: bytes) -> bytes:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f'expected {FRAME_BYTES} bytes, got {len(frame)}')
        rx = (ctypes.c_uint8 * FRAME_BYTES).from_buffer_copy(frame)
        tx = (ctypes.c_uint8 * FRAME_BYTES)()
        self.lib.me_rx_g711(rx, FRAME_BYTES)
        if self.lib.me_tx_g711(tx, FRAME_BYTES) != FRAME_BYTES:
            raise RuntimeError('me_tx_g711 returned a short frame')
        if self._diag_enabled and hasattr(self.lib, 'me_get_diag_snapshot'):
            diag = _DiagSnapshot()
            self.lib.me_get_diag_snapshot(ctypes.byref(diag))
            current = (diag.state, diag.modulation,
                       diag.v90_bridge_rx_stage, diag.v90_bridge_tx_stage,
                       diag.v90_bridge_rx_event, diag.v90_phase3_started,
                       diag.v90_phase3_s_events, diag.v90_dil_valid,
                       diag.v90_cp_valid_frames, diag.v90_cp_rejected_frames)
            if current != self._last_diag:
                self._last_diag = current
                print('[v90-diag] state=%d mod=%d rx=%d tx=%d event=%d '
                      'phase3=%d s=%d dil=%d cp=%d/%d' % current,
                      file=sys.stderr, flush=True)
        return bytes(tx)

    def close(self) -> None:
        self.lib.di_close()
        self.lib.me_destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path,
                        default=Path(os.environ.get('V90_ENGINE_BINARY',
                                                    DEFAULT_BINARY)))
    parser.add_argument('--law', choices=('pcmu', 'pcma'), default='pcmu')
    parser.add_argument('--pty', default='/tmp/v90-engine-frame-adapter')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--role', choices=('digital', 'analogue'), default='',
                        help='role for the sibling engine only; scoped to '
                             'its initialization')
    args = parser.parse_args()
    if not args.binary.exists():
        parser.error(f'engine binary does not exist: {args.binary}')

    engine = Engine(args.binary, 1 if args.law == 'pcma' else 0,
                    args.pty, args.verbose, args.role)
    try:
        while True:
            frame = sys.stdin.buffer.read(FRAME_BYTES)
            if not frame:
                break
            if len(frame) != FRAME_BYTES:
                raise RuntimeError('truncated input frame')
            sys.stdout.buffer.write(engine.exchange(frame))
            sys.stdout.buffer.flush()
    finally:
        engine.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
