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
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

FRAME_BYTES = 160
DATA_BYTES = 256
DATA_BITS = DATA_BYTES * 8
DATA_HEADER = struct.Struct('<HH')
# The coupled V.90A sideband stores 3 bits in each of the 152 payload
# samples of a 160-sample frame.  Do not feed the analogue bridge faster than
# that consumer rate; otherwise its bounded queue eventually drops the
# middle of a long TCP stream.
V90A_SIDEBAND_BITS_PER_FRAME = (160 - 8) * 3
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


class ProcessEngine:
    """Run the same frame adapter out of the Eicon media process.

    The ctypes mode is useful for low-overhead probes, but a sibling modem
    does substantial independent handshake work.  Keeping it in this process
    can delay the Eicon RTP clock before the phase gate is active.  This mode
    preserves the exact 160-byte exchange while isolating that work in a
    separate process.
    """

    def __init__(self, binary: Path, law: int, pty: str, verbose: bool,
                 role: str = ''):
        command = [sys.executable, str(Path(__file__).resolve()),
                   '--binary', str(binary), '--law',
                   'pcma' if law else 'pcmu', '--pty', pty]
        if verbose:
            command.append('--verbose')
        if role:
            command += ['--role', role]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, bufsize=0)

    def exchange(self, frame: bytes) -> bytes:
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError('reactive adapter pipes are unavailable')
        self.proc.stdin.write(frame)
        self.proc.stdin.flush()
        result = self.proc.stdout.read(FRAME_BYTES)
        if len(result) != FRAME_BYTES:
            raise RuntimeError('reactive adapter process returned a short frame')
        return result

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        self.proc.wait(timeout=5)


class Phase3ProcessEngine:
    """Stream a coupled sibling V.90A Phase-3 generator frame by frame.

    Unlike ``ProcessEngine`` this child is not a complete SIP modem.  It
    consumes the Eicon endpoint's received PCMU and returns the sibling
    analogue Phase-3 response, allowing the endpoint to keep V.8/INFO native
    while testing a protocol-aware late source.
    """

    def __init__(self, binary: Path, *_args, data_link=None):
        fd, reset_name = tempfile.mkstemp(prefix='eicon-v90a-phase3-reset-',
                                          dir='/tmp')
        os.close(fd)
        self.reset_path = Path(reset_name)
        self.reset_path.unlink()
        self.data_link = data_link
        self._last_consumed = DATA_BITS if data_link is not None else 0
        self._data_ready = False
        self._status_reported = False
        capture = os.environ.get('EICON_REACTIVE_DATA_CAPTURE_PREFIX', '')
        self._data_tx_capture = (open(capture + '.tx-bits.bin', 'wb')
                                 if capture else None)
        self._data_rx_capture = (open(capture + '.rx-bits.bin', 'wb')
                                 if capture else None)
        command = [str(binary), '--stream', '--reset-file',
                   str(self.reset_path)]
        if data_link is not None:
            command.append('--data-stream')
        self.proc = subprocess.Popen(command,
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, bufsize=0)
        # Load the child and its DSP libraries before the Eicon media loop
        # starts pacing real RTP.  This frame never reaches the wire and the
        # stream is reset again when the V90A overlay becomes active.  Without
        # the warm-up, a newly linked bridge can spend its first media quantum
        # in dyld/initialization and make the parent drop hundreds of samples;
        # that is especially visible for the opt-in zero-length-DIL probe.
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError('phase-3 adapter warm-up pipes unavailable')
        self.proc.stdin.write(self._request(bytes([0xff]) * FRAME_BYTES,
                                            warmup=True))
        self.proc.stdin.flush()
        warmup = self.proc.stdout.read(self._response_size)
        if len(warmup) != self._response_size:
            raise RuntimeError('phase-3 adapter warm-up returned a short frame')

    @property
    def _response_size(self) -> int:
        return FRAME_BYTES + (DATA_HEADER.size + DATA_BYTES
                              if self.data_link is not None else 0)

    def _request(self, frame: bytes, *, warmup: bool = False) -> bytes:
        if self.data_link is None:
            return frame
        # A zero consumption report is meaningful: the sibling is still
        # draining its receive-side state and must not be fed a synthetic bit
        # just to keep the pipe moving.  Forcing one bit here slowly corrupts
        # the synchronous V.42 stream during carrier transitions.
        count = 0
        bits = None
        if not warmup and self._data_ready:
            count = min(DATA_BITS, max(0, self._last_consumed))
            if (os.environ.get('EICON_V90A_DATA_SIDEBAND', '0') != '0'
                    and not getattr(self.data_link, 'raw_mode', False)):
                try:
                    sideband_cap = int(os.environ.get(
                        'EICON_V90A_SIDEBAND_BITS_PER_FRAME',
                        str(V90A_SIDEBAND_BITS_PER_FRAME)), 0)
                except ValueError:
                    sideband_cap = V90A_SIDEBAND_BITS_PER_FRAME
                count = min(count, max(0, sideband_cap))
            # Do not call take(0) on a live data link when this frame has no
            # sideband budget.  That boundary call can advance its protocol
            # timers without moving any payload bits.
            if count == 0:
                return frame + DATA_HEADER.pack(0, 0) + bytes(DATA_BYTES)
            # During protocol establishment, take() must continue to emit
            # detection/handshake flags.  Once LAPM is connected, however,
            # forwarding its idle flags into the V.90 sideband creates a
            # backlog faster than the analogue carrier drains it.  Service
            # timers once, then only transfer a real queued frame.
            if getattr(self.data_link, 'connected', False):
                self.data_link.take(0)
                pending = (bool(getattr(self.data_link, 'tx', None))
                           or bool(getattr(self.data_link, 'tx_stream', None))
                           or bool(getattr(self.data_link, '_tx_transfer', None)))
                if not pending:
                    count = 0
                else:
                    # The zero-count take above already advanced LAPM's
                    # timers for this media frame.  Consume the queued bits
                    # without servicing a second time.
                    bits = self.data_link.take(count, service=False, idle=False)
        if bits is None:
            bits = [] if count == 0 else self.data_link.take(count)
        if self._data_tx_capture is not None and bits:
            self._data_tx_capture.write(bytes(bit & 1 for bit in bits))
        packed = bytearray(DATA_BYTES)
        for i, bit in enumerate(bits[:DATA_BITS]):
            packed[i >> 3] |= (bit & 1) << (i & 7)
        return frame + DATA_HEADER.pack(len(bits), 0) + packed

    def exchange(self, frame: bytes) -> bytes:
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError('phase-3 adapter pipes are unavailable')
        if len(frame) != FRAME_BYTES:
            raise ValueError(f'expected {FRAME_BYTES} bytes, got {len(frame)}')
        try:
            self.proc.stdin.write(self._request(frame))
        except BrokenPipeError:
            print(f'[reactive-data] phase-3 child exited '
                  f'returncode={self.proc.poll()}', file=sys.stderr,
                  flush=True)
            raise
        self.proc.stdin.flush()
        result = self.proc.stdout.read(self._response_size)
        if len(result) != self._response_size:
            raise RuntimeError('phase-3 adapter returned a short frame')
        if self.data_link is not None:
            consumed, received = DATA_HEADER.unpack_from(result, FRAME_BYTES)
            self._data_ready = bool(received & 0x8000)
            received &= 0x7fff
            if self._data_ready and not self._status_reported:
                self._status_reported = True
                print(f'[reactive-data] synchronous bit stream ready; '
                      f'consumed={consumed} received={received}', flush=True)
            self._last_consumed = min(DATA_BITS, max(0, consumed))
            payload = result[FRAME_BYTES + DATA_HEADER.size:]
            self.data_link.feed([
                (payload[i >> 3] >> (i & 7)) & 1
                for i in range(min(received, DATA_BITS))
            ])
            if self._data_rx_capture is not None and received:
                self._data_rx_capture.write(bytes(
                    (payload[i >> 3] >> (i & 7)) & 1
                    for i in range(min(received, DATA_BITS))))
        return result[:FRAME_BYTES]

    def reset(self) -> None:
        """Restart the sibling state machine at a live Eicon gate."""
        # The reset is a protocol reset, not just a DSP reset.  Do not carry a
        # previous DATA indication or consumption estimate into retraining.
        self._data_ready = False
        self._last_consumed = DATA_BITS if self.data_link is not None else 0
        self._status_reported = False
        self.reset_path.write_text('reset\n')

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        self.proc.wait(timeout=5)
        if self._data_tx_capture is not None:
            self._data_tx_capture.close()
        if self._data_rx_capture is not None:
            self._data_rx_capture.close()
        try:
            self.reset_path.unlink()
        except FileNotFoundError:
            pass


class DigitalPhase3ProcessEngine(Phase3ProcessEngine):
    """Run the stateful digital V.90D event bridge.

    The digital bridge must observe the caller's complete live training
    stream, including the analogue Ja that arms its transmitter.  The default
    retains pre-gate history for that reason.  Some mixed-firmware pairings
    expose V.8/INFO patterns that the Phase-3-only segmenter can classify as
    early J/S events, though; the opt-in gate reset starts classification from
    the answerer's V.90 page entry, before the caller begins its Ja.
    """

    def reset(self) -> None:
        """Optionally discard pre-Phase-3 classifications at the live gate."""
        if os.environ.get('EICON_V90D_PHASE3_RESET_AT_GATE', '0') != '0':
            super().reset()


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
