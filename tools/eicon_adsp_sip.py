#!/usr/bin/env python3
"""Minimal G.711 SIP/RTP endpoint backed by the emulated Eicon ADSP modem.

This deliberately implements only the small SIP subset needed for direct test
calls: UDP INVITE/ACK/BYE, one PCMU/8000 or PCMA/8000 media stream, and 20 ms RTP packets.
There is no PRI/BRI, CAPI/IDI, MIPS call object, audio device, transcoder, PLC,
VAD, echo canceller, gain control, or resampler in the path.

Example:
    python3 tools/eicon_adsp_sip.py --bind 0.0.0.0 --sip-port 5060

Then direct an INVITE containing PCMA (static payload type 8) to this host.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import random
import re
import selectors
import signal
import socket
import struct
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from dial_tikrnl_drive import ADSP, Card

SAMPLES_PER_PACKET = 160
TICK_SECONDS = SAMPLES_PER_PACKET / 8000
LAW_INFO = {'pcmu': (0, 0xFF, 'PCMU'), 'pcma': (8, 0xD5, 'PCMA')}
PAGE_NAMES = {0: 'DIAL', 1: 'V.22', 2: 'V.32', 3: 'FSK', 4: 'FAX',
              6: 'V.8', 7: 'INFO (V.34/V.90 phase 2)', 8: 'V.34',
              10: 'protocol', 11: 'AT offline', 12: 'AT online',
              13: 'V.90 APCM', 14: 'V.90 DPCM', 15: 'fax protocol',
              16: 'low-level/FAX partial'}
RSTATUS_CH_BITS = {15: 'change_h', 13: 'speed_tx', 12: 'ratechange',
                   11: 'speed_rx', 10: 'CTS', 9: 'DSR', 8: 'DCD',
                   7: 'change_l', 4: 'sq_alarm', 3: 'dial_pending',
                   2: 'sec_tx_request', 1: 'sec_rx_present', 0: 'sec_rx_data'}
RSTATUS_BITS = {13: 'CI', 12: 'online', 11: 'ring_valid', 10: 'core',
                9: 'autodial_done', 8: 'boot_request', 7: 'flow_blocked',
                6: 'booting', 5: 'test', 4: 'loop2', 3: 'ring',
                2: '1300Hz', 1: 'energy', 0: 'zero_cross'}
DI_CONTROL_BITS = {15: 'tx_request', 14: 'rx1_valid', 13: 'rx0_valid',
                   11: 'codec_clocking', 10: 'slave', 9: 'sync'}
CHANGE_BITS = {15: 'rstatus_ch', 14: 'rstatus', 13: 'trnprogress'}
WSTATUS_BITS = {15: 'secondary_tx_present', 14: 'secondary_tx_data',
                13: 'change_wdb', 12: 'boot_finished', 4: 'txdog_changed'}


def flag_names(value: int, definitions: dict[int, str]) -> str:
    names = [name for bit, name in definitions.items() if value & (1 << bit)]
    return '|'.join(names) if names else '-'


def parse_sip(data: bytes) -> tuple[str, dict[str, str], str]:
    text = data.decode('latin1', errors='replace')
    head, _, body = text.partition('\r\n\r\n')
    lines = head.split('\r\n')
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ':' in line:
            name, value = line.split(':', 1)
            key = name.strip().lower()
            headers[key] = headers.get(key, '') + (',' if key in headers else '') + value.strip()
    return lines[0], headers, body


def sip_tagged_to(value: str, tag: str) -> str:
    return value if re.search(r'(?:^|;)\s*tag=', value, re.I) else f'{value};tag={tag}'


def local_address_for(peer: tuple[str, int], bound: str, advertised: str | None) -> str:
    if advertised:
        return advertised
    if bound not in ('0.0.0.0', '::'):
        return bound
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(peer)
        return probe.getsockname()[0]
    finally:
        probe.close()


def parse_g711_sdp(body: str, sip_peer: tuple[str, int], payload_type: int) -> tuple[str, int] | None:
    address = sip_peer[0]
    session_c = re.search(r'(?im)^c=IN IP4\s+([^\s]+)', body)
    if session_c:
        address = session_c.group(1)
    media = re.search(r'(?im)^m=audio\s+(\d+)\s+RTP/AVP\s+([^\r\n]+)', body)
    if not media:
        return None
    payloads = {int(x) for x in re.findall(r'\d+', media.group(2))}
    if payload_type not in payloads:
        return None
    media_tail = body[media.end():]
    media_c = re.search(r'(?im)^c=IN IP4\s+([^\s]+)', media_tail)
    if media_c:
        address = media_c.group(1)
    return address, int(media.group(1))


def rtp_payload(packet: bytes, payload_type: int) -> tuple[int, int, bytes] | None:
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return None
    cc = packet[0] & 0x0F
    offset = 12 + 4 * cc
    if len(packet) < offset:
        return None
    if packet[0] & 0x10:
        if len(packet) < offset + 4:
            return None
        words = struct.unpack_from('!H', packet, offset + 2)[0]
        offset += 4 + 4 * words
    if len(packet) < offset:
        return None
    end = len(packet)
    if packet[0] & 0x20:
        padding = packet[-1]
        if not padding or padding > end - offset:
            return None
        end -= padding
    pt = packet[1] & 0x7F
    seq, timestamp = struct.unpack_from('!HI', packet, 2)
    return seq, timestamp, packet[offset:end] if pt == payload_type else b''


@dataclass
class Call:
    sip_peer: tuple[str, int]
    rtp_peer: tuple[str, int]
    call_id: str
    local_tag: str
    card: Card
    rx: collections.deque[int] = field(default_factory=collections.deque)
    tx_seq: int = field(default_factory=lambda: random.randrange(65536))
    tx_timestamp: int = field(default_factory=lambda: random.randrange(2**32))
    ssrc: int = field(default_factory=lambda: random.randrange(2**32))
    next_tick: float = field(default_factory=time.monotonic)
    packets: int = 0
    samples: int = 0
    bootpage: int = -1
    trn_progress: int = -1
    rstatus_ch: int = -1
    rstatus: int = -1
    logged_overlay_switches: int = 0
    logged_l1l2_injections: int = 0
    di_control: int = -1
    baud_info: int = -1
    info_mode_selector: int = -1
    info_variant: int = -1
    v90d_state_key: tuple[int, ...] | None = None
    # Media pacing. The modem's clock is virtual, so RX jitter is absorbed as
    # latency (hold the clock until the packet lands) rather than as silence
    # substituted into the sequence the far modem is measuring.
    rx_started: bool = False
    rx_hold_until: float | None = None
    rx_holds: int = 0
    rx_substituted: int = 0
    rx_dropped: int = 0
    hold_time: float = 0.0
    catchup_deferrals: int = 0
    over_budget_ticks: int = 0
    worst_tick: float = 0.0
    reported_second: int = -1
    reported_counters: tuple[int, ...] = ()
    dil_reported: bool = False
    at_connected: bool = False


class CrashSafeWave:
    """Streaming PCM WAV whose header and payload survive an abrupt exit."""

    def __init__(self, path: Path):
        self.file = path.open('w+b', buffering=0)
        self.data_bytes = 0
        self.writes = 0
        self._patch_header()

    def _patch_header(self) -> None:
        self.file.seek(0)
        self.file.write(struct.pack('<4sI4s4sIHHIIHH4sI',
                                    b'RIFF', 36 + self.data_bytes, b'WAVE',
                                    b'fmt ', 16, 1, 1, 8000, 16000, 2, 16,
                                    b'data', self.data_bytes))
        self.file.seek(0, 2)

    def write(self, pcm: bytes) -> None:
        self.file.write(pcm)
        self.data_bytes += len(pcm)
        self.writes += 1
        # Patch once per second. At worst an unclean exit leaves a valid WAV
        # header one second short; the raw PCM remains present after it.
        if self.writes % 50 == 0:
            self._patch_header()

    def close(self) -> None:
        self._patch_header()
        self.file.close()


class RtpCapture:
    """Write both RTP directions as PCAP, raw G.711, and listenable WAV."""

    def __init__(self, prefix: Path, law: str):
        prefix.parent.mkdir(parents=True, exist_ok=True)
        self.pcap = prefix.with_suffix('.rtp.pcap').open('wb', buffering=0)
        self.pcap.write(struct.pack('<IHHIIII', 0xA1B2C3D4, 2, 4, 0, 0,
                                    65535, 101))  # LINKTYPE_RAW
        law_suffix = '.ulaw' if law == 'pcmu' else '.alaw'
        self.alaw = prefix.with_suffix(law_suffix).open('wb', buffering=0)
        self.wav = CrashSafeWave(prefix.with_suffix('.wav'))
        self.rx_alaw = prefix.with_suffix('.rx' + law_suffix).open('wb', buffering=0)
        self.rx_wav = CrashSafeWave(prefix.with_suffix('.rx.wav'))
        self.law_suffix = law_suffix
        self.diag = prefix.with_suffix('.adsp.csv').open('w', buffering=1)
        self.diag_dm = prefix.with_suffix('.adsp-dm.bin').open('wb', buffering=0)
        # V2 retains the complete 256-word memory-mapped data-pump interface:
        # write database 0x3EE0-0x3F5F followed by read database
        # 0x3F60-0x3FDF (ADDSP guide §§5.3 and 6.5).
        self.diag_dm.write(b'EADSPDM2')  # uint64 sample + 256 uint16 LE per record
        self.diag_scc = prefix.with_suffix('.adsp-scc.bin').open('wb', buffering=0)
        # Per record: sample, SCC ptr, 0x50 SCC words, then 16 x (ptr, 64 words).
        self.diag_scc.write(b'EADSPSCC1')
        self.diag.write('sample,seconds,bootpage,overlay,trnprogress,rstatus_ch,rstatus,'
                        'change_flags,dbs_rstatus_ch,dbs_rstatus,dbs_trnprogress,'
                        'eye0,eye1,eye2,info_rx_event,info_rx_complete,info_rx_parser,'
                        'v8_result,v8_line_result,v8_pending_page,v8_tx_complete,v8_handoff_count,'
                        'event_struct_ptr,data_struct_ptr,dce_scc_struct_ptr,'
                        'info_timer_hi,info_timer_lo,info_internal_progress,info_state_vector,'
                        'info_test0,info_test1,info_test2,info_test3,info_test4,'
                        'rx_ptr,rx_value,tx_ptr,tx_value,'
                        'datagram_rate,gen_control,di_control,rxd0,rxd1,baud_info,'
                        'info_mode_selector,info_variant,info_fft_span,info_fft_count,'
                        'info_fft_stride,info_result_ptr,info_sequence_reset,'
                        'detector_bit,detector_event,detector_word,detector_count,'
                        'detector_parser,wstatus,'
                        'v90d_outer_ptr,v90d_outer_state,v90d_outer_dwell,'
                        'v90d_outer_next0,v90d_outer_next1,v90d_outer_next2,v90d_outer_next3,'
                        'v90d_outer_test0,v90d_outer_test1,v90d_outer_test2,v90d_outer_test3,'
                        'v90d_outer_pretest,v90d_inner_ptr,v90d_inner_state,v90d_inner_dwell,'
                        'v90d_outer_mode,v90d_inner_flag,v90d_flag_source,'
                        'v90d_flag_input,v90d_flag_scale,v90d_flag_decoded,'
                        'v90d_result_lo,v90d_result_hi,v90d_global_countdown,'
                        'dil_flag,dil_count,dil_measure\n')
        self.ip_id = 0
        self.prefix = prefix
        self.law = law

    @staticmethod
    def ip_checksum(header: bytes) -> int:
        total = sum(struct.unpack(f'!{len(header) // 2}H', header))
        total = (total & 0xFFFF) + (total >> 16)
        total = (total & 0xFFFF) + (total >> 16)
        return (~total) & 0xFFFF

    @staticmethod
    def decode_alaw(code: int) -> int:
        value = code ^ 0x55
        sample = (value & 0x0F) << 4
        segment = (value & 0x70) >> 4
        if segment == 0:
            sample += 8
        elif segment == 1:
            sample += 0x108
        else:
            sample = (sample + 0x108) << (segment - 1)
        return sample if value & 0x80 else -sample

    @staticmethod
    def decode_ulaw(code: int) -> int:
        value = (~code) & 0xFF
        sample = (((value & 0x0F) << 3) + 0x84) << ((value & 0x70) >> 4)
        sample -= 0x84
        return -sample if value & 0x80 else sample

    def write(self, rtp: bytes, payload: bytes, source: tuple[str, int],
              destination: tuple[str, int], outbound: bool) -> None:
        udp_len = 8 + len(rtp)
        ip_len = 20 + udp_len
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, ip_len, self.ip_id,
                         0, 64, socket.IPPROTO_UDP, 0,
                         socket.inet_aton(source[0]),
                         socket.inet_aton(destination[0]))
        checksum = self.ip_checksum(ip)
        ip = ip[:10] + struct.pack('!H', checksum) + ip[12:]
        udp = struct.pack('!HHHH', source[1], destination[1], udp_len, 0)
        packet = ip + udp + rtp
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        record = struct.pack('<IIII', sec, usec, len(packet), len(packet)) + packet
        self.pcap.write(record)
        alaw = self.alaw if outbound else self.rx_alaw
        wav = self.wav if outbound else self.rx_wav
        alaw.write(payload)
        decode = self.decode_ulaw if self.law == 'pcmu' else self.decode_alaw
        pcm = b''.join(struct.pack('<h', decode(code)) for code in payload)
        wav.write(pcm)
        self.ip_id = (self.ip_id + 1) & 0xFFFF

    def write_diag(self, sample: int, card: Card) -> None:
        dm = card.dm
        values = (sample, sample / 8000, dm[0x3FB0], card.resident,
                  dm[0x3FC2], dm[0x3FC0], dm[0x3FC1], dm[0x3FA2],
                  dm[0x3FA3], dm[0x3FA4], dm[0x3FA5],
                  dm[0x3F9C], dm[0x3F9D], dm[0x3F9E],
                  dm[0x0685], dm[0x0686], dm[0x16BD],
                  dm[0x3EAA], dm[0x3FC4], dm[0x0491], dm[0x075B], dm[0x06B3],
                  dm[0x3F70], dm[0x3F71], dm[0x3F76],
                  dm[0x1647], dm[0x1650], dm[0x1652], dm[0x1679],
                  dm[0x1696], dm[0x1697], dm[0x1698], dm[0x1699], dm[0x169A],
                  dm[0x3F0F], dm[dm[0x3F0F] & 0x3fff] if dm[0x3F0F] else 0,
                  dm[0x3FB4], dm[dm[0x3FB4] & 0x3fff] if dm[0x3FB4] else 0,
                  dm[0x3F60], dm[0x3F9F], dm[0x3FAD], dm[0x3FAE], dm[0x3FAF],
                  dm[0x3FBB], dm[0x3F94], dm[0x16B6], dm[0x16C5], dm[0x16C6],
                  dm[0x16C7], dm[0x15F3], dm[0x0E4C], dm[0x060F], dm[0x198E],
                  dm[0x198F], dm[0x19CD], dm[0x19CF], dm[0x3EEE],
                  dm[0x120F], dm[0x1FF7], dm[0x1FF6],
                  dm[0x1FF8], dm[0x1FF9], dm[0x1FFA], dm[0x1FFB],
                  dm[0x1FFC], dm[0x1FFD], dm[0x1FFE], dm[0x1FFF], dm[0x2000],
                  dm[0x204A], dm[0x2008], dm[0x2007],
                  dm[0x1FE9], dm[0x2004], dm[0x0AD5], dm[0x0DD7], dm[0x0ACF],
                  dm[0x0A56], dm[0x206D], dm[0x206E], dm[0x20E0],
                  dm[0x3F8B], dm[0x3F87], dm[0x3F8E])
        self.diag.write(f'{values[0]},{values[1]:.6f},' +
                        ','.join(f'0x{value:04x}' for value in values[2:]) + '\n')
        # Preserve every defined, reserved and spare word in the complete
        # memory-mapped interface.  Reserved words are especially useful when
        # reverse-engineering firmware because they are undocumented but live;
        # spare words establish that no hidden state was present.
        snapshot = [dm[0x3EE0 + offset] for offset in range(256)]
        self.diag_dm.write(struct.pack('<Q256H', sample, *snapshot))
        scc_ptr = dm[0x3F76] & 0x3fff
        valid_scc = scc_ptr != 0 and scc_ptr + 0x50 <= 0x4000
        scc = ([dm[scc_ptr + offset] for offset in range(0x50)]
               if valid_scc else [0] * 0x50)
        record = bytearray(struct.pack('<QH80H', sample, scc_ptr, *scc))
        descriptor_offsets = ([0x06 + 3 * i for i in range(8)] +
                              [0x1E + 3 * i for i in range(8)])
        for descriptor in descriptor_offsets:
            pointer = scc[descriptor + 2] & 0x3fff if valid_scc else 0
            data = ([dm[pointer + i] if pointer + i < 0x4000 else 0
                     for i in range(64)] if pointer else [0] * 64)
            record.extend(struct.pack('<H64H', pointer, *data))
        self.diag_scc.write(record)

    def close(self) -> None:
        self.pcap.close()
        self.alaw.close()
        self.rx_alaw.close()
        self.wav.close()
        self.rx_wav.close()
        self.diag.close()
        self.diag_dm.close()
        self.diag_scc.close()
        print(f'[capture] wrote {self.prefix}.rtp.pcap/.adsp.csv/.adsp-dm.bin/'
              f'.adsp-scc.bin, TX '
              f'{self.law_suffix}/.wav and RX .rx{self.law_suffix}/.rx.wav')


class EiconSipEndpoint:
    def __init__(self, bind: str, sip_port: int, rtp_port: int,
                 advertised: str | None, verbose: bool = False,
                 capture_prefix: Path | None = None, law: str = 'pcmu',
                 registrar: str | None = None, username: str | None = None,
                 password: str = '', rx_guard_ms: int = 1000,
                 force_info_after_v8: bool = False,
                 kernel_dispatch: bool = False,
                 init_info_detector_at_24: bool = False,
                 watch_exec: tuple[int, ...] = (),
                 watch_dm: tuple[int, ...] = (),
                 info_actions: dict[int, int] | None = None,
                 db_words: dict[int, int] | None = None,
                 native_mips: bool = False,
                 tx_prbs: bool = False,
                 tx_v42: bool = False,
                 tx_v42bis: bool = False,
                 mips_kernel: Path | None = None,
                 mips_tikrnl: Path | None = None,
                 mips_image: Path = Path('docs/firmware/te_dmlt.pm'),
                 mips_combifile: Path = Path('docs/firmware/dspdload.bin'),
                 trace_v90d_state: bool = False,
                 prime_v90d_bulk_cursor: bool = False,
                 native_bearer_activation: bool = False,
                 trace_file: Path | None = None,
                 rx_jitter_ms: int = 40, rx_hold_ms: int = 60,
                 rx_depth_ms: int = 500, catchup_quanta: int = 2,
                 tick_budget_ms: float = 18.0,
                 mips_interval: int = 160,
                 realtime: bool = False,
                 v42_pty: bool = False, at_terminal: bool = False,
                 ring_seconds: float = 2.0,
                 modem_role: str = 'answer',
                 originate_line_ready: bool | None = None,
                 originate_v8: bool | None = None,
                 dial_number: str = '', dial_target: str = ''):
        self.bind = bind
        self.advertised = advertised
        self.law = law
        self.payload_type, self.silence, self.codec_name = LAW_INFO[law]
        self.registrar = registrar
        self.username = username
        self.password = password
        self.register_cseq = 0
        self.register_call_id = f'eicon-{random.randrange(2**64):016x}'
        self.rx_guard_samples = max(0, rx_guard_ms * 8)
        self.force_info_after_v8 = force_info_after_v8
        self.kernel_dispatch = kernel_dispatch
        self.init_info_detector_at_24 = init_info_detector_at_24
        self.watch_exec = watch_exec
        self.watch_dm = watch_dm
        self.info_actions = dict(info_actions or {})
        self.db_words = dict(db_words or {})
        self.native_mips = native_mips
        # Which side of the modem handshake this instance takes. The
        # signalling role is always answer; see build_card().
        self.modem_role = modem_role
        # None means "let the backend decide": the native MIPS backend defaults
        # to EICON_ORIGINATE_LINE_READY, which pins DM(0x0554) for the calling
        # side so the dial page does not wait on a tone detector a PRI never
        # arms (Sessions 95-96). The CLI flag overrides the env var for A/B.
        self.originate_line_ready = originate_line_ready
        self.originate_v8 = originate_v8
        self.outgoing: dict | None = None
        self.dial_number = dial_number
        self.dial_target = dial_target
        self.tx_prbs = tx_prbs
        self.tx_v42 = tx_v42
        self.tx_v42bis = tx_v42bis
        # Allocated at startup rather than on answer: the point of printing the
        # path is that a terminal can already be attached when the call lands.
        self.pty = None
        self.at = None
        # Pending incoming INVITE that is ringing, not yet answered. The
        # answerer sends 180 Ringing and presents RING to the terminal, then
        # either auto-answers after `ring_seconds` (S0 >= 1) or waits for ATA
        # (S0 = 0 or 255). None when no call is ringing.
        self.pending_invite = None
        self.ring_seconds = ring_seconds
        if v42_pty:
            # The PTY is the terminal a user attaches to. With --tx-v42 it is
            # also the data link (LAPM I frames); without --tx-v42 it is an
            # AT command console only -- call control (ATD/ATA/ATH) with no
            # error-correcting data path underneath, which is what the calling
            # side of a loopback needs to dial.
            from v42_pty import PtyLink
            if at_terminal:
                from eicon_at import AtParser
                self.at = AtParser()
                # S0=1: auto-answer after one ring cadence. The endpoint no
                # longer answers the INVITE synchronously -- it rings first --
                # so S0=1 means "ring once, then answer" rather than "answer
                # before the terminal sees RING". S0=0 (set with ATS0=0 on the
                # terminal) leaves the call ringing until ATA.
                self.at.registers[0] = 1
            self.pty = PtyLink(at_parser=self.at,
                               on_action=self.on_at_action)
        self.mips_kernel = mips_kernel
        self.mips_tikrnl = mips_tikrnl
        self.mips_image = mips_image
        self.mips_combifile = mips_combifile
        self.trace_v90d_state = trace_v90d_state
        self.prime_v90d_bulk_cursor = prime_v90d_bulk_cursor
        self.native_bearer_activation = native_bearer_activation
        # Page 14 changes the V90D trace from a few lines per call to one per
        # 3200-Hz symbol. Formatting and writing that costs about 0.5 ms of the
        # 20 ms media budget locally, but a terminal over ssh is not a
        # measurable consumer, so keep the option of a buffered file.
        self.trace_stream = (trace_file.open('w', buffering=1 << 16)
                             if trace_file else None)
        self.rx_prefill_samples = max(0, rx_jitter_ms * 8)
        # Above this the queue is backlog, not jitter margin, and is drained
        # ahead of the wall clock.
        self.rx_drain_samples = self.rx_prefill_samples + SAMPLES_PER_PACKET
        self.rx_hold_seconds = max(0.0, rx_hold_ms / 1000)
        self.rx_depth_samples = max(SAMPLES_PER_PACKET, rx_depth_ms * 8)
        self.catchup_quanta = max(1, catchup_quanta)
        self.realtime = realtime
        self.tick_budget = tick_budget_ms / 1000
        self.mips_interval = mips_interval
        self.native_card = None
        # Card booted at dial time, waiting for the 200 OK. See dial().
        self.dialed_card = None
        self.verbose = verbose
        self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip.bind((bind, sip_port))
        self.sip.setblocking(False)
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind((bind, rtp_port))
        self.rtp.setblocking(False)
        self.sip_port = self.sip.getsockname()[1]
        self.rtp_port = self.rtp.getsockname()[1]
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.sip, selectors.EVENT_READ, self.on_sip)
        self.selector.register(self.rtp, selectors.EVENT_READ, self.on_rtp)
        self.call: Call | None = None
        self.running = True
        self.capture = RtpCapture(capture_prefix, law) if capture_prefix else None

        # Keep firmware companding on an independent ADSP core. PM 0x1810
        # uses DAG registers, while a real SPORT compander would not alter the
        # modem task's register state.
        self.codec = Card()
        self.codec.boot()
        self.codec.configure_g711_law(law)
        if native_mips:
            if mips_kernel is None or mips_tikrnl is None:
                raise ValueError('--native-mips requires kernel and TIKRNL paths')
            # Do not fabricate and connect the firmware-side incoming call at
            # server startup. The SIP INVITE is the network SETUP event; run
            # the synchronous firmware entry/assignment then, and send 200 OK
            # only after the modem task and bearer have finished attaching.
            print('[native-mips] card firmware will start on incoming INVITE')
        if registrar and username:
            self.send_register()

    def send_register(self, challenge: dict[str, str] | None = None) -> None:
        host, _, port_text = self.registrar.partition(':')
        peer = (socket.gethostbyname(host), int(port_text or 5060))
        local_ip = local_address_for(peer, self.bind, self.advertised)
        self.register_cseq += 1
        uri = f'sip:{self.registrar}'
        branch = f'z9hG4bK{random.randrange(2**48):012x}'
        lines = [f'REGISTER {uri} SIP/2.0',
                 f'Via: SIP/2.0/UDP {local_ip}:{self.sip_port};branch={branch};rport',
                 f'From: <sip:{self.username}@{self.registrar}>;tag=eicon',
                 f'To: <sip:{self.username}@{self.registrar}>',
                 f'Call-ID: {self.register_call_id}',
                 f'CSeq: {self.register_cseq} REGISTER',
                 f'Contact: <sip:{self.username}@{local_ip}:{self.sip_port}>',
                 'Expires: 3600']
        if challenge:
            realm, nonce = challenge['realm'], challenge['nonce']
            cnonce = f'{random.randrange(2**64):016x}'
            nc = '00000001'
            ha1 = hashlib.md5(f'{self.username}:{realm}:{self.password}'.encode()).hexdigest()
            ha2 = hashlib.md5(f'REGISTER:{uri}'.encode()).hexdigest()
            if challenge.get('qop'):
                digest = hashlib.md5(f'{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}'.encode()).hexdigest()
                auth = (f'Digest username="{self.username}", realm="{realm}", '
                        f'nonce="{nonce}", uri="{uri}", response="{digest}", '
                        f'algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}"')
            else:
                digest = hashlib.md5(f'{ha1}:{nonce}:{ha2}'.encode()).hexdigest()
                auth = (f'Digest username="{self.username}", realm="{realm}", '
                        f'nonce="{nonce}", uri="{uri}", response="{digest}", algorithm=MD5')
            if challenge.get('opaque'):
                auth += f', opaque="{challenge["opaque"]}"'
            lines.append('Authorization: ' + auth)
        lines.extend(['Content-Length: 0', '', ''])
        self.sip.sendto('\r\n'.join(lines).encode(), peer)

    @staticmethod
    def digest_challenge(value: str) -> dict[str, str]:
        result = {}
        for key, quoted, bare in re.findall(
                r'(\w+)=(?:"([^"]*)"|([^,\s]+))', value):
            result[key.lower()] = quoted or bare
        return result

    def response(self, code: int, reason: str, headers: dict[str, str],
                 peer: tuple[str, int], body: str = '', extra: list[str] | None = None,
                 tag: str | None = None) -> None:
        lines = [f'SIP/2.0 {code} {reason}']
        for name in ('via', 'from'):
            if headers.get(name):
                lines.append(f'{name.title()}: {headers[name]}')
        if headers.get('to'):
            to = sip_tagged_to(headers['to'], tag) if tag else headers['to']
            lines.append(f'To: {to}')
        for name in ('call-id', 'cseq'):
            if headers.get(name):
                lines.append(f'{name.title()}: {headers[name]}')
        if extra:
            lines.extend(extra)
        payload = body.encode('ascii')
        lines.append(f'Content-Length: {len(payload)}')
        wire = ('\r\n'.join(lines) + '\r\n\r\n').encode('ascii') + payload
        self.sip.sendto(wire, peer)

    def on_sip(self) -> None:
        data, peer = self.sip.recvfrom(65535)
        first, headers, body = parse_sip(data)
        parts = first.split()
        if not parts:
            return
        if first.startswith('SIP/2.0'):
            parts = first.split()
            cseq = headers.get('cseq', '')
            if len(parts) > 1 and cseq.upper().endswith('REGISTER'):
                if parts[1] in ('401', '407'):
                    value = headers.get('www-authenticate') or headers.get('proxy-authenticate', '')
                    challenge = self.digest_challenge(value)
                    if challenge.get('realm') and challenge.get('nonce'):
                        self.send_register(challenge)
                elif parts[1] == '200':
                    print(f'[sip] registered {self.username}@{self.registrar}')
                return
            if len(parts) > 1 and cseq.upper().endswith('INVITE'):
                try:
                    status = int(parts[1])
                except ValueError:
                    return
                self.on_invite_response(status, headers, body, peer)
            return
        method = parts[0].upper()
        if self.verbose:
            print(f'[sip] {method} from {peer[0]}:{peer[1]}')

        if method == 'OPTIONS':
            self.response(200, 'OK', headers, peer, extra=['Allow: INVITE, ACK, BYE, OPTIONS'])
            return
        if method == 'INVITE':
            media = parse_g711_sdp(body, peer, self.payload_type)
            if media is None:
                self.response(488, 'Not Acceptable Here', headers, peer,
                              extra=['Accept: application/sdp'])
                return
            call_id = headers.get('call-id', '')
            if self.call and self.call.call_id != call_id:
                self.response(486, 'Busy Here', headers, peer)
                return
            if self.call:
                # Retransmission of an INVITE we already answered.
                local_ip = local_address_for(peer, self.bind, self.advertised)
                sdp = self.local_sdp(local_ip)
                self.response(200, 'OK', headers, peer, sdp,
                              [f'Contact: <sip:eicon@{local_ip}:{self.sip_port}>',
                               'Content-Type: application/sdp'],
                              self.call.local_tag)
                return
            if self.pending_invite and self.pending_invite['call_id'] == call_id:
                # Retransmission while still ringing: resend 180 Ringing.
                self.response(180, 'Ringing', headers, peer)
                return
            if self.pending_invite:
                self.response(486, 'Busy Here', headers, peer)
                return
            # A new incoming call: ring first, answer after a cadence (or
            # on ATA). 100 Trying keeps the caller from retransmitting before
            # the 180 lands; 180 Ringing is the SIP progress the caller's
            # terminal sees while the answerer rings.
            self.response(100, 'Trying', headers, peer)
            self.response(180, 'Ringing', headers, peer)
            caller = headers.get('from', '')
            if self.at is not None and self.pty is not None:
                self.at_apply_options()
                self.pty.write_terminal(self.at.ring(caller))
                self.pty.dispatch_actions()
            # S0 controls auto-answer: 1..254 = answer after N rings. One
            # ring is `ring_seconds`; for the loopback that is 2 s by default.
            # S0=0 or 255 = wait for ATA.
            s0 = self.at.registers[0] if self.at is not None else 1
            answer_at = (time.monotonic() + self.ring_seconds
                         if 1 <= s0 <= 254 else None)
            self.pending_invite = {
                'headers': headers, 'peer': peer, 'media': media,
                'call_id': call_id, 'answer_at': answer_at,
            }
            print(f'[call] ringing from {caller.strip()} ({peer[0]}:{peer[1]})'
                  f"; {'auto-answer in %.1fs' % self.ring_seconds if answer_at else 'waiting for ATA'}")
            return
        if method in ('BYE', 'CANCEL'):
            tag = self.call.local_tag if self.call else None
            self.response(200, 'OK', headers, peer, tag=tag)
            # CANCEL of a ringing call before it was answered.
            if self.pending_invite is not None:
                self.pending_invite = None
                print(f'[call] cancelled while ringing')
                if self.at is not None and self.pty is not None:
                    self.pty.write_terminal(self.at.no_carrier())
                return
            if self.call:
                tx_stats = ''
                if hasattr(self.call.card, 'tx_requests'):
                    tx_stats = (f'; TX datagrams {self.call.card.tx_accepted}/'
                                f'{self.call.card.tx_requests} accepted/requested')
                    payload = getattr(self.call.card, 'tx_payload_datagrams', 0)
                    fill = getattr(self.call.card, 'tx_fill_datagrams', 0)
                    if payload or fill:
                        # Fill on a live data connection is silence injected
                        # into a working link, not idle: the DSP transmits
                        # every datagram whether or not the in_sync gate here
                        # let the LAPM stream through.
                        tx_stats += (f', {payload} payload / {fill} mark fill')
                print(f'[call] ended after {self.call.packets} RTP packets, '
                      f'{self.call.samples} samples{tx_stats}')
                lapm = getattr(self.call.card, 'lapm', None)
                if lapm is not None:
                    print(f'[v42] totals: state={'connected' if lapm.connected else 'down'}, '
                          f'HDLC good/bad/abort={lapm.decoder.good}/'
                          f'{lapm.decoder.bad_fcs}/{lapm.decoder.aborts}, '
                          f'XID rx/tx={lapm.stats.xid_rx}/{lapm.stats.xid_tx}, '
                          f'SABME rx={lapm.stats.sabme_rx}, UA tx={lapm.stats.ua_tx}, '
                          f'I rx={lapm.stats.i_rx}, RR tx={lapm.stats.rr_tx}, '
                          f'I tx/retx={lapm.stats.i_tx}/{lapm.stats.i_retx}, '
                          f'REJ rx={lapm.stats.rej_rx}, '
                          f'RNR rx={lapm.stats.rnr_rx}, '
                          f'polls={lapm.stats.poll_tx}, '
                          f'out-of-seq={lapm.stats.out_of_seq}, '
                          f'unacked={lapm.outstanding}, '
                          f'unsent={len(lapm.tx_stream)}, '
                          f'undrained rx bytes={len(lapm.rx_data)}')
                if getattr(self.call.card, 'nl_data_mode', False):
                    card = self.call.card
                    # Acceptance is the return-code count, not the number of
                    # requests submitted: the two were conflated before the
                    # bridge tracked outstanding requests at all.
                    print(f'[nl] N_DATA totals: '
                          f'{card._nl_accepted}/{card._nl_posted} '
                          f'accepted/submitted, rejected={card._nl_rejected}, '
                          f'tx={card._nl_tx_octets} octets, '
                          f'rx={card._nl_rx_octets} octets, '
                          f'elastic store={len(card._nl_tx_bits) // 8} octets, '
                          f'queued={len(card.nl_data_queue)}, '
                          f'busy={card._nl_busy}, fc={card._nl_fc}, '
                          f'bearer={"open" if card.nl_connected else "closed"}')
                print(f'[media] call totals: substituted '
                      f'{self.call.rx_substituted} RX samples, dropped '
                      f'{self.call.rx_dropped}, clock holds {self.call.rx_holds} '
                      f'({self.call.hold_time * 1000:.0f} ms spent waiting), '
                      f'{self.call.over_budget_ticks} ticks over '
                      f'{self.tick_budget * 1000:.0f} ms, worst '
                      f'{self.call.worst_tick * 1000:.1f} ms, '
                      f'{self.call.catchup_deferrals} catch-up deferrals')
                self.call = None
                self.outgoing = None
                if self.at is not None and self.pty is not None:
                    self.pty.write_terminal(self.at.no_carrier())
            return
        if method == 'ACK':
            return
        self.response(405, 'Method Not Allowed', headers, peer,
                      extra=['Allow: INVITE, ACK, BYE, OPTIONS'])

    def on_rtp(self) -> None:
        packet, peer = self.rtp.recvfrom(65535)
        call = self.call
        if not call:
            return
        parsed = rtp_payload(packet, self.payload_type)
        if parsed is None:
            return
        _, _, payload = parsed
        if not payload:
            return
        # NAT symmetric-RTP: reply to the address that actually sent media.
        call.rtp_peer = peer
        if self.capture:
            destination_ip = local_address_for(call.sip_peer, self.bind,
                                                self.advertised)
            self.capture.write(packet, payload, peer,
                               (destination_ip, self.rtp_port), False)
        call.rx_started = True
        if len(call.rx) < self.rx_depth_samples:
            call.rx.extend(payload)
        else:
            # The queue only reaches this depth if the media thread has stopped
            # keeping up; discarding is corruption of the received sequence, so
            # count it rather than hiding it.
            call.rx_dropped += len(payload)

    def trace(self, line: str) -> None:
        if self.trace_stream is not None:
            self.trace_stream.write(line + '\n')
        else:
            print(line)

    def rx_ready(self, call: Call, now: float) -> bool:
        """Hold the virtual modem clock instead of feeding it invented silence.

        RTP arrives on the peer's clock and is consumed on ours, and there was
        no jitter buffer between the two: a queue that ran empty for one tick
        put 20 ms of silence into the middle of whatever the far modem was
        measuring. Phase 3 DIL is where that is fatal -- the analogue modem is
        learning the digital impairments from a known sequence, and the way it
        answers a hole in that sequence is to ask for the sequence again. The
        modem's clock here is virtual, so waiting is available and costs bounded
        one-way latency; silence substitution is not recoverable.
        """
        if not call.rx_started or call.samples < self.rx_guard_samples:
            call.rx_hold_until = None
            return True
        # Hysteresis: one packet is enough to keep going, but once the queue has
        # actually run dry, wait for the full jitter margin before resuming --
        # otherwise every tick from then on holds for another late packet.
        needed = (self.rx_prefill_samples
                  if call.packets == 0 or call.rx_hold_until is not None
                  else SAMPLES_PER_PACKET)
        if len(call.rx) >= needed:
            call.rx_hold_until = None
            return True
        if call.rx_hold_until is None:
            call.rx_hold_until = now + self.rx_hold_seconds
        if now < call.rx_hold_until:
            call.rx_holds += 1
            call.next_tick = now + 0.002
            call.hold_time += 0.002
            return False
        # Waited out the whole hold: the peer has genuinely stopped sending, so
        # run the quantum on silence rather than stalling the call.
        call.rx_hold_until = None
        return True

    def report_media(self, call: Call) -> None:
        second = call.samples // 8000
        # Force a report every 10s even if counters haven't changed, so the
        # pacing ratio is visible throughout the call.
        force = second % 10 == 0 and second != call.reported_second
        if second == call.reported_second:
            return
        call.reported_second = second
        # Include wall time so the pacing ratio is visible.
        if not hasattr(call, '_wall_start'):
            call._wall_start = time.monotonic()
        wall = time.monotonic() - call._wall_start
        counters = (call.rx_substituted, call.rx_dropped, call.rx_holds,
                    call.over_budget_ticks, call.catchup_deferrals)
        if counters == call.reported_counters and not force:
            return
        call.reported_counters = counters
        print(f'[media] {second} s: rx queue {len(call.rx)}, substituted '
              f'{call.rx_substituted}, dropped {call.rx_dropped}, clock holds '
              f'{call.rx_holds} ({call.hold_time * 1000:.0f} ms waiting), '
              f'ticks over {self.tick_budget * 1000:.0f} ms '
              f'{call.over_budget_ticks} (worst {call.worst_tick * 1000:.1f} ms), '
              f'catch-up deferrals {call.catchup_deferrals}, '
              f'wall {wall:.1f}s (ratio {second / wall:.2f}x)')

    def next_wakeup(self, now: float) -> float:
        """Selector timeout. A backlogged receive queue means do not sleep: the
        catch-up cap deliberately returns here between batches of quanta, and
        sleeping until the next scheduled tick would pace the drain back to real
        time and leave the backlog standing as latency for the rest of the call.
        """
        call = self.call
        if not call:
            return 0.25
        if not self.realtime and len(call.rx) > self.rx_drain_samples:
            return 0.0
        return max(0.0, min(0.25, call.next_tick - now))

    def media_tick(self, now: float) -> None:
        call = self.call
        if not call:
            return
        # The media clock starts when the bearer opens, not when the Call
        # object was created. SIP setup, the ring cadence and several seconds
        # of firmware boot all sit in between, and carrying that deficit into
        # the first tick makes the endpoint burst every owed quantum at full
        # CPU speed: measured, the answerer delivered 2.66 s of media in the
        # first wall second, the caller's receive queue hit its high-water and
        # discarded 9440 samples, and what it discarded was the start of
        # ANSam. Start the clock here instead.
        if call.packets == 0 and now > call.next_tick:
            call.next_tick = now
        # Never manufacture or drop modem-clock samples to chase wall time.
        # If the process wakes late, run each elapsed 160-sample quantum -- but
        # only a few per wake-up, so that RTP reads are serviced in between and
        # the receive queue is not driven into its high-water discard while the
        # emulator catches up.
        served = 0
        while self.call is call:
            # Draining a backlog is how accumulated latency is given back. The
            # queue only rises above target because this thread lost wall time,
            # and what is queued is real received audio, so consume it ahead of
            # schedule instead of leaving it as permanent one-way delay.
            # In realtime mode (loopback) the catch-up is disabled so both
            # endpoints process at wall-clock rate, keeping the V.8/V.34
            # handshake synchronized instead of one racing ahead of the other.
            if not self.realtime and len(call.rx) > self.rx_drain_samples:
                call.next_tick = min(call.next_tick, now)
            if now < call.next_tick:
                return
            if served >= self.catchup_quanta:
                call.catchup_deferrals += 1
                return
            if not self.rx_ready(call, now):
                return
            tick_start = time.monotonic()
            linear: list[int] = []
            for _ in range(SAMPLES_PER_PACKET):
                if call.rx:
                    received = call.rx.popleft()
                else:
                    received = self.silence
                    if call.samples >= self.rx_guard_samples:
                        call.rx_substituted += 1
                # Ignore the FXS off-hook transient before presenting the
                # seized bearer to the modem. The Courier/ATA produces a
                # near-full-scale pulse about 100 ms after SIP answer; without
                # this guard DIAL falsely selects V.OWN before ANSam starts.
                code = self.silence if call.samples < self.rx_guard_samples else received
                linear.append(call.card.frame_fast(code, call.samples))
                call.samples += 1
                if self.trace_v90d_state and call.card.resident == 0x026A:
                    dm = call.card.dm
                    key = (dm[0x120F], dm[0x1FF7], dm[0x204A], dm[0x2008],
                           dm[0x2004], dm[0x206D], dm[0x206E], dm[0x3FB3],
                           dm[0x3FBC], dm[0x3FBD], dm[0x32F7],
                           *(dm[index] for index in range(8)))
                    if dm[0x1FF7] >= 0x0078:
                        key += (dm[0x11E8], dm[0x11E9], dm[0x11EB],
                                dm[0x0EE6], dm[0x2055],
                                dm[0x11F5], dm[0x11F6])
                    if key != call.v90d_state_key:
                        self.trace(f'[v90d] sample {call.samples} '
                              f'({call.samples / 8000:.6f}s): '
                              f'optr={dm[0x120F]:04x} state={dm[0x1FF7]:04x} '
                              f'dwell={dm[0x1FF6]:04x} '
                              f'next={dm[0x1FF8]:04x}/{dm[0x1FF9]:04x}/'
                              f'{dm[0x1FFA]:04x}/{dm[0x1FFB]:04x} '
                              f'test={dm[0x1FFC]:04x}/{dm[0x1FFD]:04x}/'
                              f'{dm[0x1FFE]:04x}/{dm[0x1FFF]:04x} '
                              f'pre={dm[0x2000]:04x} '
                              f'iptr={dm[0x204A]:04x} istate={dm[0x2008]:04x} '
                              f'idwell={dm[0x2007]:04x} mode={dm[0x1FE9]:04x} '
                              f'iflag={dm[0x2004]:04x} source={dm[0x0AD5]:04x} '
                              f'input={dm[0x0DD7]:04x} scale={dm[0x0ACF]:04x} '
                              f'decoded={dm[0x0A56]:04x} '
                              f'result={dm[0x206D]:04x}/{dm[0x206E]:04x} '
                              f'global={dm[0x20E0]:04x} '
                              f'core8k={dm[0x3FB3]:04x} '
                              f'bulk={dm[0x3FBC]:04x}/{dm[0x3FBD]:04x} '
                              f'bulksel={dm[0x32F7]:04x} '
                              f'bulkdesc={"/".join(f"{dm[index]:04x}" for index in range(8))} '
                              f'phase={dm[0x11E8]:04x} diff={dm[0x11E9]:04x} '
                              f'dhist={dm[0x11EB]:04x} raw={dm[0x0EE6]:04x} '
                              f'bits={dm[0x2055]:04x} '
                              f'eq={dm[0x11F5]:04x}/{dm[0x11F6]:04x}')
                        call.v90d_state_key = key
            switches = call.card.switches[call.logged_overlay_switches:]
            for sample, page, wanted in switches:
                overlay = call.card.overlays.get(wanted)
                overlay_name = overlay[1].split(' Version')[0] if overlay else '?'
                forced = ' [FORCED post-V.8 fallback]' if sample in call.card.forced_info_samples else ''
                print(f'[adsp] sample {sample} ({sample / 8000:.3f}s): '
                      f'overlay request page {page} {PAGE_NAMES.get(page, "?")} '
                      f'-> 0x{wanted:04x} {overlay_name} served{forced}')
            call.logged_overlay_switches = len(call.card.switches)
            injections = getattr(call.card, 'l1l2_forced_samples', [])
            for sample in injections[call.logged_l1l2_injections:]:
                print(f'[adsp] sample {sample} ({sample / 8000:.3f}s): '
                      'confirmed 2400-Hz Tone A; injected INFO post-L2 event 1')
            call.logged_l1l2_injections = len(injections)
            trn_progress = call.card.dm[0x3FC2]
            rstatus_ch = call.card.dm[0x3FC0]
            rstatus = call.card.dm[0x3FC1]
            if (trn_progress != call.trn_progress
                    or rstatus_ch != call.rstatus_ch or rstatus != call.rstatus):
                info_rx = ''
                if call.card.dm[0x3FB0] == 7:
                    info_rx = (f'; INFO_RX event=0x{call.card.dm[0x0685]:04x} '
                               f'complete=0x{call.card.dm[0x0686]:04x} '
                               f'parser=0x{call.card.dm[0x16BD]:04x}')
                print(f'[adsp] sample {call.samples} ({call.samples / 8000:.3f}s): '
                      f'TrnProgress 0x{call.trn_progress & 0xffff:04x} -> '
                      f'0x{trn_progress:04x}; Rstatus_ch=0x{rstatus_ch:04x}'
                      f'[{flag_names(rstatus_ch, RSTATUS_CH_BITS)}] '
                      f'Rstatus=0x{rstatus:04x}'
                      f'[{flag_names(rstatus, RSTATUS_BITS)}]{info_rx}')
                call.trn_progress = trn_progress
                call.rstatus_ch = rstatus_ch
                call.rstatus = rstatus
                # Session 87: three read-database words settle during DIL,
                # which is where the call is decided. They looked predictive
                # over nine archived captures -- DM(0x3f8b) was 1 for every
                # call that completed and 0 for every call that stalled at
                # 0x00b3 -- and the very next live call had it clear and
                # reached 0x00d0 anyway. So this is not a predictor; it is
                # instrumentation for the phase where the outcome is set, and
                # none of these words appears anywhere else in this log.
                if trn_progress >= 0x007a and not call.dil_reported:
                    call.dil_reported = True
                    dm = call.card.dm
                    print(f'[dil] sample {call.samples} '
                          f'({call.samples / 8000:.3f}s): '
                          f'flag DM(0x3f8b)=0x{dm[0x3F8B]:04x} '
                          f'count DM(0x3f87)=0x{dm[0x3F87]:04x} '
                          f'measure DM(0x3f8e)=0x{dm[0x3F8E]:04x}')
            di_control = call.card.dm[0x3FAD]
            baud_info = call.card.dm[0x3FBB]
            info_mode = call.card.dm[0x3F94]
            info_variant = call.card.dm[0x16B6]
            di_changed = di_control != call.di_control
            # PRBS mode services bit F at the DSP datagram rate. The complete
            # value remains in the binary/CSV capture; avoid synchronous log
            # I/O twice per request on the real-time media thread.
            tx_request_only = ((self.tx_prbs or self.tx_v42)
                               and call.di_control >= 0 and
                               (di_control ^ call.di_control) == 0x8000)
            if ((di_changed and not tx_request_only) or
                    baud_info != call.baud_info or
                    info_mode != call.info_mode_selector or
                    info_variant != call.info_variant):
                print(f'[adsp] sample {call.samples} ({call.samples / 8000:.3f}s): '
                      f'DI_control=0x{di_control:04x}'
                      f'[{flag_names(di_control, DI_CONTROL_BITS)}] '
                      f'BaudInfo=0x{baud_info:04x} INFO_mode=0x{info_mode:04x} '
                      f'INFO_variant=0x{info_variant:04x}')
                call.baud_info = baud_info
                call.info_mode_selector = info_mode
                call.info_variant = info_variant
            call.di_control = di_control
            bootpage = call.card.dm[0x3FB0]
            if bootpage != call.bootpage:
                old = (f'{call.bootpage} {PAGE_NAMES.get(call.bootpage, "?")}'
                       if call.bootpage >= 0 else '-')
                overlay = call.card.overlays.get(call.card.resident)
                overlay_name = overlay[1].split(' Version')[0] if overlay else '?'
                if bootpage in PAGE_NAMES:
                    print(f'[adsp] sample {call.samples} ({call.samples / 8000:.3f}s): '
                          f'bootpage {old} -> {bootpage} '
                          f'{PAGE_NAMES[bootpage]}, overlay=0x{call.card.resident:04x} '
                          f'{overlay_name}')
                else:
                    signed = bootpage - 0x10000 if bootpage & 0x8000 else bootpage
                    print(f'[adsp] sample {call.samples} ({call.samples / 8000:.3f}s): '
                          f'shared boot word {old} -> 0x{bootpage:04x} ({signed}); '
                          'no valid overlay page')
                call.bootpage = bootpage
            if self.capture:
                self.capture.write_diag(call.samples, call.card)
            payload = self.codec.encode_g711(linear)
            marker = 0x80 if call.packets == 0 else 0
            header = struct.pack('!BBHII', 0x80, marker | self.payload_type,
                                 call.tx_seq, call.tx_timestamp, call.ssrc)
            packet = header + payload
            self.rtp.sendto(packet, call.rtp_peer)
            if self.capture:
                source_ip = local_address_for(call.sip_peer, self.bind, self.advertised)
                self.capture.write(packet, payload, (source_ip, self.rtp_port),
                                   call.rtp_peer, True)
            if self.pty is not None:
                # Once per 20 ms quantum, not per sample: a terminal does not
                # need 8 kHz service, and the LAPM window is what actually
                # paces it.
                self.at_watch(call)
                self.pty.pump(getattr(call.card, 'lapm', None))
            call.tx_seq = (call.tx_seq + 1) & 0xFFFF
            call.tx_timestamp = (call.tx_timestamp + SAMPLES_PER_PACKET) & 0xFFFFFFFF
            call.packets += 1
            call.next_tick += TICK_SECONDS
            served += 1
            elapsed = time.monotonic() - tick_start
            call.worst_tick = max(call.worst_tick, elapsed)
            if elapsed > self.tick_budget:
                call.over_budget_ticks += 1
            self.report_media(call)
            now = time.monotonic()

    def build_card(self):
        """Boot an emulated card for one call, whichever way it was set up.

        The signalling role is always 'answer' -- the card is driven through
        its incoming-call path in both directions. Which side of the *modem*
        handshake this instance takes is `--modem-role`, published in
        GEN_SETUP1, and is the only thing that has to differ between the two
        ends of a loopback.
        """
        if self.native_mips:
            if self.native_card is None:
                from eicon_mips_shim import create_native_mips_modem
                self.native_card = create_native_mips_modem(
                    self.mips_kernel, self.mips_tikrnl, self.law,
                    self.mips_image, self.mips_combifile,
                    force_info_after_v8=self.force_info_after_v8,
                    tx_prbs=self.tx_prbs, tx_v42=self.tx_v42,
                    tx_v42bis=self.tx_v42bis,
                    prime_v90d_bulk_cursor=self.prime_v90d_bulk_cursor,
                    native_bearer_activation=self.native_bearer_activation,
                    mips_interval=self.mips_interval,
                    originate_line_ready=self.originate_line_ready,
                    originate_v8=self.originate_v8,
                    modem_role=self.modem_role)
            card = self.native_card
            self.native_card = None
            print('[native-mips] firmware entry and bearer attachment '
                  'complete')
        elif self.kernel_dispatch:
            from dial_kernel_dispatch import LiveKernelModem
            card = LiveKernelModem(
                init_info_detector_at_24=self.init_info_detector_at_24,
                info_actions=self.info_actions)
        else:
            card = Card(force_info_after_v8=self.force_info_after_v8)
        card.boot()
        # Where the role can be selected here it is, and where it cannot the
        # backend took it at construction. Plain Card writes GEN_SETUP1
        # directly (dial_tikrnl_drive.py:402); NativeMipsModem publishes it in
        # the answer WDB and LiveKernelModem only answers.
        if self.native_mips or self.kernel_dispatch:
            card.configure_modem('answer', self.law)
        else:
            card.configure_modem(self.modem_role, self.law)
        # LiveKernelModem wraps a Card; both expose the same emulator.
        for address, value in self.db_words.items():
            getattr(card, 'card', card).dm[address] = value
        cpu = getattr(card, 'card', card).cpu
        for address in self.watch_exec:
            ADSP.adsp2181_watch_exec(cpu, address, 1)
        from eicon_mips_shim import ADSP as _ADSP
        for address in self.watch_dm:
            _ADSP.adsp2181_watch_dm(cpu, address, 1)
        return card

    def _complete_answer(self) -> None:
        """Finish a ringing incoming call: build the card and send 200 OK.

        Called either when the ring cadence expires (S0 auto-answer) or when
        ATA is typed on the terminal. The INVITE was already provisionally
        answered with 180 Ringing; this sends the final 200 OK with SDP and
        establishes the call.
        """
        inv = self.pending_invite
        if inv is None:
            return
        self.pending_invite = None
        headers = inv['headers']
        peer = inv['peer']
        media = inv['media']
        call_id = inv['call_id']
        card = self.build_card()
        self.call = Call(peer, media, call_id,
                         f'{random.randrange(2**32):08x}', card)
        local_ip = local_address_for(peer, self.bind, self.advertised)
        sdp = self.local_sdp(local_ip)
        self.response(200, 'OK', headers, peer, sdp,
                      [f'Contact: <sip:eicon@{local_ip}:{self.sip_port}>',
                       'Content-Type: application/sdp'], self.call.local_tag)
        print(f'[call] answering {peer[0]}:{peer[1]}, RTP -> '
              f'{media[0]}:{media[1]}, {self.codec_name}/8000')

    def local_sdp(self, local_ip: str) -> str:
        return (f'v=0\r\no=eicon 0 0 IN IP4 {local_ip}\r\n'
                f's=Eicon ADSP modem\r\nc=IN IP4 {local_ip}\r\n'
                f't=0 0\r\nm=audio {self.rtp_port} RTP/AVP {self.payload_type}\r\n'
                f'a=rtpmap:{self.payload_type} {self.codec_name}/8000\r\n'
                f'a=sendrecv\r\na=ptime:20\r\n')

    # -- origination ------------------------------------------------------
    def dial(self, number: str, target: "str | None" = None) -> bool:
        """Place an outgoing call.

        `target` is host[:port]; without one the call goes to the registrar,
        which is the normal case when this instance registered. A loopback
        passes the other instance's address directly and needs no registrar
        at all.
        """
        if self.call or self.outgoing:
            print('[sip] already on a call; not dialling')
            return False
        host = target or self.registrar
        if not host:
            print('[sip] no dial target and no registrar')
            return False
        if ':' in host:
            name, _, port = host.partition(':')
            peer = (socket.gethostbyname(name), int(port))
        else:
            peer = (socket.gethostbyname(host), 5060)
        self.outgoing = {
            'number': number,
            'peer': peer,
            'host': host,
            'call_id': f'eicon-{random.randrange(2**64):016x}',
            'cseq': 1,
            'tag': f'{random.randrange(2**32):08x}',
            'branch': f'z9hG4bK{random.randrange(2**48):012x}',
            'authorized': False,
        }
        print(f'[sip] dialling {number} at {peer[0]}:{peer[1]}')
        # Boot before the INVITE goes out, not when the answer comes back.
        # Firmware entry is several seconds of wall time, and doing it after
        # the 200 OK means the answerer has already been sending media for
        # that long: measured on loopback, the caller started its media loop
        # 1.66 s late, took the backlog as one burst, and discarded 1.18 s of
        # the answerer's ANSam at the receive high-water. A real modem is
        # initialised before it dials.
        if self.at is not None:
            self.at_apply_options()
        self.dialed_card = self.build_card()
        self.send_invite()
        return True

    def send_invite(self, challenge: "dict[str, str] | None" = None) -> None:
        out = self.outgoing
        peer = out['peer']
        local_ip = local_address_for(peer, self.bind, self.advertised)
        uri = f'sip:{out["number"]}@{out["host"]}'
        sdp = self.local_sdp(local_ip)
        user = self.username or 'eicon'
        lines = [f'INVITE {uri} SIP/2.0',
                 f'Via: SIP/2.0/UDP {local_ip}:{self.sip_port};'
                 f'branch={out["branch"]};rport',
                 f'From: <sip:{user}@{out["host"]}>;tag={out["tag"]}',
                 f'To: <{uri}>',
                 f'Call-ID: {out["call_id"]}',
                 f'CSeq: {out["cseq"]} INVITE',
                 f'Contact: <sip:{user}@{local_ip}:{self.sip_port}>',
                 'Allow: INVITE, ACK, BYE, CANCEL, OPTIONS',
                 'Content-Type: application/sdp',
                 f'Content-Length: {len(sdp)}']
        if challenge:
            lines.append('Authorization: ' + self.digest_authorization(
                challenge, 'INVITE', uri))
        lines.extend(['', sdp])
        self.sip.sendto('\r\n'.join(lines).encode(), peer)

    def digest_authorization(self, challenge: dict, method: str,
                             uri: str) -> str:
        """The same digest send_register() computes, for any method."""
        realm, nonce = challenge['realm'], challenge['nonce']
        cnonce = f'{random.randrange(2**64):016x}'
        nc = '00000001'
        ha1 = hashlib.md5(
            f'{self.username}:{realm}:{self.password}'.encode()).hexdigest()
        ha2 = hashlib.md5(f'{method}:{uri}'.encode()).hexdigest()
        if challenge.get('qop'):
            digest = hashlib.md5(
                f'{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}'.encode()).hexdigest()
            auth = (f'Digest username="{self.username}", realm="{realm}", '
                    f'nonce="{nonce}", uri="{uri}", response="{digest}", '
                    f'algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}"')
        else:
            digest = hashlib.md5(f'{ha1}:{nonce}:{ha2}'.encode()).hexdigest()
            auth = (f'Digest username="{self.username}", realm="{realm}", '
                    f'nonce="{nonce}", uri="{uri}", response="{digest}", '
                    'algorithm=MD5')
        if challenge.get('opaque'):
            auth += f', opaque="{challenge["opaque"]}"'
        return auth

    def send_ack(self, headers: dict) -> None:
        out = self.outgoing
        peer = out['peer']
        local_ip = local_address_for(peer, self.bind, self.advertised)
        uri = f'sip:{out["number"]}@{out["host"]}'
        lines = [f'ACK {uri} SIP/2.0',
                 f'Via: SIP/2.0/UDP {local_ip}:{self.sip_port};'
                 f'branch={out["branch"]};rport',
                 f'From: <sip:{self.username or "eicon"}@{out["host"]}>;'
                 f'tag={out["tag"]}',
                 f'To: {headers.get("to", f"<{uri}>")}',
                 f'Call-ID: {out["call_id"]}',
                 f'CSeq: {out["cseq"]} ACK',
                 'Content-Length: 0', '', '']
        self.sip.sendto('\r\n'.join(lines).encode(), peer)

    def send_bye(self) -> None:
        """Hang up a call this side originated."""
        out = self.outgoing
        if not out or not self.call:
            return
        peer = out['peer']
        local_ip = local_address_for(peer, self.bind, self.advertised)
        uri = f'sip:{out["number"]}@{out["host"]}'
        out['cseq'] += 1
        lines = [f'BYE {uri} SIP/2.0',
                 f'Via: SIP/2.0/UDP {local_ip}:{self.sip_port};'
                 f'branch=z9hG4bK{random.randrange(2**48):012x};rport',
                 f'From: <sip:{self.username or "eicon"}@{out["host"]}>;'
                 f'tag={out["tag"]}',
                 f'To: {out.get("remote_to", f"<{uri}>")}',
                 f'Call-ID: {out["call_id"]}',
                 f'CSeq: {out["cseq"]} BYE',
                 'Content-Length: 0', '', '']
        self.sip.sendto('\r\n'.join(lines).encode(), peer)

    def on_invite_response(self, status: int, headers: dict, body: str,
                           peer: tuple) -> None:
        """Drive the originating call through its response codes."""
        out = self.outgoing
        if not out:
            return
        if status < 200:
            if self.verbose or status in (180, 183):
                print(f'[sip] outgoing call: {status}')
            return
        if status in (401, 407):
            if out['authorized']:
                print('[sip] outgoing call rejected: authentication failed')
                self.fail_outgoing()
                return
            value = (headers.get('www-authenticate')
                     or headers.get('proxy-authenticate', ''))
            challenge = self.digest_challenge(value)
            if not (challenge.get('realm') and challenge.get('nonce')):
                print('[sip] outgoing call: unusable authentication challenge')
                self.fail_outgoing()
                return
            out['authorized'] = True
            out['cseq'] += 1
            out['branch'] = f'z9hG4bK{random.randrange(2**48):012x}'
            self.send_invite(challenge)
            return
        if status >= 300:
            print(f'[sip] outgoing call failed: {status}')
            self.send_ack(headers)
            self.fail_outgoing()
            return

        # 2xx: the far end answered.
        media = parse_g711_sdp(body, peer, self.payload_type)
        if media is None:
            print('[sip] answer carried no usable G.711 media; hanging up')
            self.send_ack(headers)
            self.fail_outgoing()
            return
        out['remote_to'] = headers.get('to', '')
        self.send_ack(headers)
        if self.call:
            return
        card = self.dialed_card
        self.dialed_card = None
        if card is None:
            if self.at is not None:
                self.at_apply_options()
            card = self.build_card()
        self.call = Call(peer, media, out['call_id'], out['tag'], card)
        print(f'[call] connected to {media[0]}:{media[1]}, '
              f'{self.codec_name}/8000, modem role {self.modem_role}')

    def fail_outgoing(self) -> None:
        self.outgoing = None
        self.dialed_card = None
        if self.at is not None and self.pty is not None:
            self.pty.write_terminal(self.at.no_carrier())

    def on_at_action(self, action) -> bytes:
        """Perform what the AT parser asked for, and answer the terminal.

        Only the verbs this endpoint can honour are wired.  ATD is not: the
        endpoint answers INVITEs, it does not place calls, and pretending
        otherwise would leave a dial script waiting for a CONNECT that cannot
        arrive.
        """
        from eicon_at import ActionKind
        if self.at is None:
            return b''
        if action.kind is ActionKind.DIAL:
            if not self.dial(action.number, self.dial_target or None):
                return self.at.respond('NO CARRIER')
            return b''
        if action.kind is ActionKind.ANSWER:
            if self.pending_invite is not None:
                print('[at] ATA: answering the ringing call')
                self._complete_answer()
                return b''
            if self.call is None:
                return self.at.respond('NO CARRIER')
            # The INVITE was answered when it arrived; ATA has nothing left to
            # do but confirm, and the CONNECT follows from the rate word.
            print('[at] ATA: call already answered at INVITE')
            return b''
        if action.kind in (ActionKind.HANGUP, ActionKind.RESET):
            if self.call is None and not self.outgoing:
                return b''
            print(f'[at] {action}: dropping the call')
            if self.outgoing:
                self.send_bye()
            self.end_call('AT command')
            return b''
        if action.kind is ActionKind.ONLINE:
            return b''
        return b''

    def at_apply_options(self) -> None:
        """Push the terminal's +IE selection into the next call's CAI."""
        if self.at is None or not self.native_mips:
            return
        import eicon_mips_shim
        options = self.at.options()
        eicon_mips_shim.set_modem_options(options)
        import eicon_idi
        print(f'[at] next call: {eicon_idi.describe_cai(eicon_idi.build_cai(options))}')

    def at_watch(self, call: 'Call') -> None:
        """Emit CONNECT once the card publishes a negotiated rate.

        The read-database rate word (WDB +0x01, DM 0x3EE1) is the card's own
        statement that the connection came up: bit 5 means V.90 and the low
        five bits are bits per datagram, at 8000/6 datagrams a second.

        But +0x01 starts as GEN_SETUP1 (0x0484 answer / 0x048c calling),
        published in the answer WDB before training begins, and the firmware
        may tweak it slightly (0x048c -> 0x048e) without that being a
        negotiated rate. A real rate word only uses bits 0-5 and bit 13
        (e.g. 0x2028 -> 38666 bit/s); GEN_SETUP1 has bits 7 and 10 set
        (mask 0x1FC0 is non-zero). So fire CONNECT only when the setup bits
        are clear -- that is the firmware's statement that it overwrote
        GEN_SETUP1 with the negotiated rate.
        """
        if self.at is None or call.at_connected:
            return
        card = getattr(call.card, 'card', call.card)
        rate = card.dm[0x3EE0 + 0x01]
        lapm = getattr(call.card, 'lapm', None)
        if not rate or (rate & 0x1FC0):
            # In raw fallback the WDB rate word may never be republished,
            # although DATASTATE has already established the bearer. Without
            # CONNECT the AT parser stays in command mode and silently
            # consumes terminal text, which explains a PTY with zero bytes
            # sent to the link. Use the live V.90 data-state rate only after
            # the V.42 endpoint has explicitly entered raw mode.
            if lapm is None or not lapm.raw_mode:
                return
            tx_bits = (card._v90d_tx_bits()
                       if hasattr(card, '_v90d_tx_bits') else None)
            if tx_bits is None:
                return
            bits = tx_bits
            speed = int(bits * 8000 / 6)
            carrier = 'V90'
            protocol = 'NONE'
            rate = 0
        else:
            bits = 21 + (rate & 0x1F)
            speed = int(bits * 8000 / 6)
            carrier = 'V90' if rate & 0x20 else 'V34'
            protocol = 'LAPM' if lapm is not None and lapm.connected else 'NONE'
        call.at_connected = True
        print(f'[at] CONNECT {carrier} {speed} (rate word 0x{rate:04x})')
        if self.pty is not None:
            self.pty.write_terminal(
                self.at.connected(speed, speed, carrier, protocol, 'NONE'))

    def end_call(self, reason: str) -> None:
        """Drop the current call, telling the terminal if one is attached."""
        if self.call is None:
            return
        print(f'[call] ended by {reason}')
        self.call = None
        self.outgoing = None
        if self.at is not None and self.pty is not None:
            self.pty.write_terminal(self.at.no_carrier())

    def fail_call(self) -> None:
        """Report a media-path fault against the modem state that produced it.

        The traceback alone does not say what the firmware was doing, and the
        page is the first thing worth knowing: a fault on the V.34 handoff and
        one in steady-state V.90 have nothing in common.
        """
        call = self.call
        traceback.print_exc()
        if call is None:
            return
        card = call.card
        print(f'[call] media fault at sample {call.samples} '
              f'({call.samples / 8000:.3f}s), overlay=0x'
              f'{getattr(card, "resident", 0):04x}, bootpage='
              f'{card.dm[0x3FB0]}, TrnProgress=0x{card.dm[0x3FC2]:04x}, '
              f'Rstatus=0x{card.dm[0x3FC1]:04x}; dropping the call and '
              'staying up for the next INVITE')
        # Leave self.capture open: it belongs to the endpoint, and its files are
        # the evidence for the fault that just happened.
        self.call = None

    def run(self) -> None:
        print(f'[sip] listening on {self.bind}:{self.sip_port}; RTP '
              f'{self.bind}:{self.rtp_port}; {self.codec_name} only; '
              f'modem role {self.modem_role}')
        dial_at = None
        if self.dial_number:
            # A moment's grace so the far instance is listening and any
            # REGISTER has been answered before the INVITE goes out.
            dial_at = time.monotonic() + 1.0
        try:
            while self.running:
                if dial_at is not None and time.monotonic() >= dial_at:
                    dial_at = None
                    self.dial(self.dial_number, self.dial_target or None)
                # Auto-answer a ringing call when the ring cadence expires.
                if (self.pending_invite is not None
                        and self.pending_invite['answer_at'] is not None
                        and time.monotonic() >= self.pending_invite['answer_at']):
                    print('[call] ring cadence elapsed; answering')
                    self._complete_answer()
                now = time.monotonic()
                for key, _ in self.selector.select(self.next_wakeup(now)):
                    key.data()
                try:
                    self.media_tick(time.monotonic())
                except Exception:
                    # A fault in the emulated pump used to propagate out of
                    # here, so the process exited through the finally below and
                    # the call simply dropped -- with the traceback on stderr
                    # and the endpoint log ending mid-sentence, which is how a
                    # page-8 fault looked indistinguishable from the peer
                    # hanging up. Contain it to the call and keep listening.
                    self.fail_call()
        finally:
            if self.pty is not None:
                self.pty.close()
            if self.capture:
                self.capture.close()
            if self.trace_stream is not None:
                self.trace_stream.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--advertise', help='IP address placed in Contact and SDP')
    ap.add_argument('--sip-port', type=int, default=5060)
    ap.add_argument('--rtp-port', type=int, default=4000)
    ap.add_argument('--registrar', help='optional SIP registrar host[:port]')
    ap.add_argument('--username', help='registrar account/user')
    ap.add_argument('--password', default='', help='registrar password')
    ap.add_argument('--rx-guard-ms', type=int, default=1000,
                    help='discard FXS startup audio before modem RX (default: 1000)')
    ap.add_argument('--law', choices=('pcmu', 'pcma'), default='pcmu',
                    help='transparent RTP G.711 law (default: pcmu)')
    ap.add_argument('--capture-prefix', type=Path,
                    help='save both RTP directions plus raw G.711 and decoded WAV files')
    ap.add_argument('--force-info-after-v8', action='store_true',
                    help='diagnostic: replace a post-V.8 low-level fallback with page 7 INFO')
    ap.add_argument('--kernel-dispatch', action='store_true',
                    help='drive TIKRNL through the SPORT0 kernel dispatcher')
    ap.add_argument('--native-mips', action='store_true',
                    help='supervise the SIP ADSP with the real Unicorn MIPS firmware')
    data_source = ap.add_mutually_exclusive_group()
    data_source.add_argument('--tx-prbs', action='store_true',
                    help='diagnostic: answer V90D TX requests with deterministic '
                         'PRBS data (requires --native-mips)')
    data_source.add_argument('--tx-v42', action='store_true',
                    help='experimental V.42 HDLC/XID/LAPM endpoint on the '
                         'synchronous data-pump interface (requires --native-mips)')
    ap.add_argument('--tx-v42bis', action='store_true',
                    help='negotiate V.42bis compression on the experimental '
                         'V.42 endpoint (requires --tx-v42)')
    ap.add_argument('--v42-pty', action='store_true',
                    help='expose the V.42 link as a pseudo-terminal and print '
                         'its path; attach with screen or minicom '
                         '(requires --tx-v42)')
    ap.add_argument('--modem-role', choices=('answer', 'calling'),
                    default='answer',
                    help='which side of the modem handshake this instance '
                         'takes (GEN_SETUP1 0x0484/0x048c). The signalling '
                         'role is always answer; this is the data-pump role, '
                         'and a loopback needs one instance of each')
    ap.add_argument('--originate-line-ready', dest='originate_line_ready',
                    default=None, action='store_true',
                    help='for the calling role, pin DM(0x0554) so the dial '
                         'page does not wait on the dial-tone/DTMF tone '
                         'detector a PRI product never arms (Sessions 95-96). '
                         'Default on for the calling role; the env var '
                         'EICON_ORIGINATE_LINE_READY controls both ends')
    ap.add_argument('--no-originate-line-ready', dest='originate_line_ready',
                    action='store_false',
                    help='leave the calling side to wait on the tone '
                         'detector, i.e. reproduce the inert caller of '
                         'Sessions 95-96 for A/B')
    ap.add_argument('--originate-v8', dest='originate_v8',
                    default=None, action='store_true',
                    help='for the calling role, request the V.8 overlay '
                         '(0x025f) once the dial page reaches training '
                         'start (TrnProgress 0x0051), since the originate '
                         'dial page never calls the kernel page-request '
                         'routine the answerer uses -- the legitimate path '
                         'is an AT dial script this SIP path bypasses. '
                         'Default on for the calling role; '
                         'EICON_ORIGINATE_V8 controls both ends')
    ap.add_argument('--no-originate-v8', dest='originate_v8',
                    action='store_false',
                    help='do not force a V.8 request from the originate '
                         'side; leave it to the firmware (which never does)')
    ap.add_argument('--dial', metavar='NUMBER',
                    help='place an outgoing call to NUMBER once the endpoint '
                         'is up, instead of waiting for an INVITE')
    ap.add_argument('--dial-target', metavar='HOST[:PORT]',
                    help='send outgoing INVITEs straight here rather than to '
                         'the registrar; a loopback points this at the other '
                         'instance and needs no registrar at all')
    ap.add_argument('--at', action='store_true',
                    help='put the divas4linux AT command set in front of the '
                         'pseudo-terminal: RING/CONNECT/NO CARRIER, S-registers '
                         'and AT+IE modulation selection, which reaches the '
                         'CAI of the next call (requires --v42-pty). Without '
                         '--tx-v42 the terminal is an AT command console for '
                         'call control (ATD/ATA/ATH) with no data link')
    ap.add_argument('--ring-seconds', type=float, default=2.0,
                    help='how long to ring before auto-answering when S0>=1 '
                         '(default 2.0s, one ring cadence). S0=0 (ATS0=0 on '
                         'the terminal) leaves the call ringing until ATA')
    ap.add_argument('--trace-v90d-state', action='store_true',
                    help='log exact outer/inner V90D record transitions; the capture '
                         'CSV always records these fields once per RTP packet')
    ap.add_argument('--trace-file', type=Path,
                    help='write [v90d] trace lines to this file (buffered) instead '
                         'of stdout; page 14 produces one line per 3200-Hz symbol')
    ap.add_argument('--rx-jitter-ms', type=int, default=40,
                    help='receive queue depth to accumulate before the first media '
                         'tick (default: 40, i.e. two RTP packets)')
    ap.add_argument('--rx-hold-ms', type=int, default=60,
                    help='how long the virtual modem clock may be held waiting for '
                         'late RTP before silence is substituted (default: 60)')
    ap.add_argument('--rx-depth-ms', type=int, default=500,
                    help='receive queue high-water mark; arrivals past it are '
                         'discarded and counted (default: 500)')
    ap.add_argument('--catchup-quanta', type=int, default=2,
                    help='160-sample quanta to run per wake-up before returning to '
                         'the socket loop (default: 2)')
    ap.add_argument('--tick-budget-ms', type=float, default=18.0,
                    help='report media ticks that exceed this wall time; the pump '
                         'itself costs about 11 ms of every 20 ms (default: 18)')
    ap.add_argument('--mips-interval', type=int, default=160,
                    help='samples between MIPS supervisor passes; 160 is one pass '
                         'per RTP packet and costs about 8.4 ms of the 20 ms media '
                         'budget. 320 halves that at the price of signalling '
                         'latency (default: 160)')
    ap.add_argument('--realtime', action='store_true',
                    help='pace media to wall clock even when the RX queue is '
                         'full, disabling the catch-up drain that otherwise '
                         'lets a loopback endpoint race ahead of its peer. '
                         'Keeps the V.8/V.34 handshake synchronized between '
                         'two loopback instances')
    ap.add_argument('--prime-v90d-bulk-cursor', action='store_true',
                    help='diagnostic: initialize V90D far-bulk cursor DM4 from DM0 '
                         'when state 0x60 activates the adapter (requires --native-mips)')
    ap.add_argument('--native-bearer-activation', action='store_true',
                    help='diagnostic: deliver the lower-PRI post-CALL_RES connected '
                         'event and disable the compatibility DIAL/WDB synthesis')
    ap.add_argument('--mips-kernel', type=Path,
                    default=Path('artifacts/eicon-dsp/build-117-926/kernel/'
                                 '0009-diva-server-pri-30m-kernel'))
    ap.add_argument('--mips-tikrnl', type=Path,
                    default=Path('artifacts/eicon-dsp/build-117-926/tikrnl/'
                                 '0258-tikrnl81.f34-task'))
    ap.add_argument('--mips-image', type=Path,
                    default=Path('docs/firmware/te_dmlt.pm'))
    ap.add_argument('--mips-combifile', type=Path,
                    default=Path('docs/firmware/dspdload.bin'))
    ap.add_argument('--db-word', default='', metavar='ADDR:VALUE[,...]',
                    help='write DSP DM words after configure_modem, e.g. '
                         '0x3f8a:0x5678 -- the reserved database word PM 0x34b5 '
                         'tests to select the INFO state chain that arms the '
                         '8-bit control channel')
    ap.add_argument('--info-action', default='', metavar='STATE:CODE[,...]',
                    help='diagnostic: dispatch INFO action-table entry CODE '
                         '(PM 0x2ee6..0x2eee) the first time TrnProgress '
                         'reaches STATE, e.g. 0x34:1 to run PM 0x2602 at the '
                         'start of the 0x34..0x37 receive window')
    ap.add_argument('--watch-exec', default='',
                    help='comma-separated PM addresses to log on execution; '
                         '0x3515 is the control-channel bit decision, where '
                         'ax1 in the [EXEC] line is the correlator magnitude '
                         'the firmware thresholds at 0x0578')
    ap.add_argument('--watch-dm', default='',
                    help='comma-separated DM addresses to write-watch (logs '
                         'the writer PC via [WATCH] dm w)')
    ap.add_argument('--init-info-detector-at-24', action='store_true',
                    help='diagnostic: invoke firmware PM 0x2602 at INFO state 0x24')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    if (args.tx_prbs or args.tx_v42) and not args.native_mips:
        ap.error('--tx-prbs/--tx-v42 require --native-mips')
    if args.tx_v42bis and not args.tx_v42:
        ap.error('--tx-v42bis requires --tx-v42')
    if args.at and not args.v42_pty:
        ap.error('--at requires --v42-pty')
    endpoint = EiconSipEndpoint(args.bind, args.sip_port, args.rtp_port,
                                args.advertise, args.verbose,
                                args.capture_prefix, args.law, args.registrar,
                                args.username, args.password, args.rx_guard_ms,
                                args.force_info_after_v8, args.kernel_dispatch,
                                args.init_info_detector_at_24,
                                tuple(int(field, 0) for field in
                                      args.watch_exec.split(',') if field.strip()),
                                tuple(int(field, 0) for field in
                                      args.watch_dm.split(',') if field.strip()),
                                {int(pair.split(':')[0], 0): int(pair.split(':')[1], 0)
                                 for pair in args.info_action.split(',')
                                 if pair.strip()},
                                {int(pair.split(':')[0], 0): int(pair.split(':')[1], 0)
                                 for pair in args.db_word.split(',')
                                 if pair.strip()},
                                args.native_mips, args.tx_prbs, args.tx_v42,
                                args.tx_v42bis,
                                args.mips_kernel, args.mips_tikrnl, args.mips_image,
                                args.mips_combifile, args.trace_v90d_state,
                                args.prime_v90d_bulk_cursor,
                                args.native_bearer_activation,
                                args.trace_file, args.rx_jitter_ms,
                                args.rx_hold_ms, args.rx_depth_ms,
                                args.catchup_quanta, args.tick_budget_ms,
                                args.mips_interval, realtime=args.realtime, v42_pty=args.v42_pty,
                                at_terminal=args.at,
                                ring_seconds=args.ring_seconds,
                                modem_role=args.modem_role,
                                originate_line_ready=args.originate_line_ready,
                                originate_v8=args.originate_v8,
                                dial_number=args.dial or '',
                                dial_target=args.dial_target or '')
    signal.signal(signal.SIGINT, lambda *_: setattr(endpoint, 'running', False))
    signal.signal(signal.SIGTERM, lambda *_: setattr(endpoint, 'running', False))
    endpoint.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
