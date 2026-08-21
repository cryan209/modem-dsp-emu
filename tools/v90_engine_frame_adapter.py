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


class Engine:
    def __init__(self, binary: Path, law: int, pty: str, verbose: bool):
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
        self.lib.di_set_callbacks.argtypes = [ctypes.c_void_p] * 4
        self.lib.di_set_callbacks.restype = None
        self.lib.di_open.argtypes = [ctypes.c_char_p]
        self.lib.di_open.restype = ctypes.c_int
        self.lib.di_close.argtypes = []
        self.lib.di_close.restype = None

        self.lib.me_set_verbose(int(verbose))
        self.lib.me_init()
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
    args = parser.parse_args()
    if not args.binary.exists():
        parser.error(f'engine binary does not exist: {args.binary}')

    engine = Engine(args.binary, 1 if args.law == 'pcma' else 0,
                    args.pty, args.verbose)
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
