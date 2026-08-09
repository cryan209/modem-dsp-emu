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
import math
import random
import re
import selectors
import signal
import socket
import struct
import threading
import time
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from dial_tikrnl_drive import ADSP, Card
from logcap import emit

SAMPLES_PER_PACKET = 160
# The rms of a signal at 0 dBm0 in G.711 linear units: a full-scale sine is
# +3.17 dBm0 by definition, and its rms is 32124/sqrt(2).
DBM0_RMS = (32124 / math.sqrt(2)) / (10 ** (3.17 / 20))
# One-way delay to hold in the receive path, in ms. The media loop is built to
# give latency back -- the drain below exists for exactly that -- and measured
# on the loopback rig the resulting round trip is 0.00 ms in both directions,
# in realtime mode as well as under --no-realtime. That is right for an
# emulated digital end against a real analogue modem, where added delay is pure
# loss, and wrong for two emulated ends: V.34 Phase 2 is ranging, RTDEa/RTDEc
# are defined as a measured interval minus 40 ms (V.34 11.2.1.1.4, 11.2.1.2.4),
# and the Phase 3 recovery timers are all specified as "plus a round trip
# delay". A modem cannot range a line with no length. Holding the queue at this
# depth makes the consumer trail the producer permanently, which is a delay
# line rather than a jitter margin. 0 keeps the historical behaviour.
RX_LAG_MS = int(os.environ.get("EICON_RX_LAG_MS", "0"), 0)
TICK_SECONDS = SAMPLES_PER_PACKET / 8000
LAW_INFO = {'pcmu': (0, 0xFF, 'PCMU'), 'pcma': (8, 0xD5, 'PCMA')}
PAGE_NAMES = {0: 'DIAL', 1: 'V.22', 2: 'V.32', 3: 'FSK', 4: 'FAX',
              6: 'V.8', 7: 'INFO (V.34/V.90 phase 2)', 8: 'V.34',
              10: 'protocol', 11: 'AT offline', 12: 'AT online',
              13: 'V.90 APCM', 14: 'V.90 DPCM', 15: 'fax protocol',
              16: 'low-level/FAX partial'}


def status_block_is_scratch(dm) -> bool:
    """Is DM 0x3fb0..0x3fca currently somebody else's buffer?

    The status block is only the outer state machine's while that machine is
    running.  When it stops, the INFO overlay's bit-reversing block copy at
    PM 0x3b24..0x3b27 takes the same DM over as scratch and rewrites every
    word of it -- thousands of times per call -- so `bootpage`, `TrnProgress`
    and both Rstatus words go on reading as though the modem were doing
    something, and are not.  Session 136.

    `TrnProgress` is the discriminator.  Every legitimate write of DM(0x3fc2)
    is a small state number: 300,000 consecutive writes were logged and the
    high byte was zero in all of them, across every page.  A nonzero high byte
    therefore means the block is not being published by its owner.
    """
    return bool(dm[0x3FC2] & 0xFF00)


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


def contact_uri(contact: str, fallback: str) -> str:
    """The remote target from a Contact header, or `fallback` if unusable.

    Contact is `<sip:user@host:port>;expires=...` or a bare URI, and the
    angle-bracket form is the one that carries parameters belonging to the
    header rather than the URI -- so taking everything up to the first `;`
    without looking for brackets first gets the common case wrong.
    """
    inner = re.search(r'<([^>]+)>', contact)
    if inner:
        return inner.group(1).strip()
    bare = contact.split(';')[0].strip()
    return bare if bare.startswith('sip:') else fallback


def build_inbound_bye(*, target: str, via_host: str, via_port: int,
                      branch: str, from_header: str, local_tag: str,
                      to_header: str, call_id: str, cseq: int) -> str:
    """The BYE that ends a call this endpoint answered.

    Kept apart from the socket so the message can be asserted directly: the
    role swap is the part that is easy to get wrong and impossible to see in
    a log that only shows what we sent being ignored.
    """
    if 'tag=' not in from_header:
        from_header = f'{from_header};tag={local_tag}'
    return '\r\n'.join([
        f'BYE {target} SIP/2.0',
        f'Via: SIP/2.0/UDP {via_host}:{via_port};branch=z9hG4bK{branch};rport',
        'Max-Forwards: 70',
        f'From: {from_header}',
        f'To: {to_header}',
        f'Call-ID: {call_id}',
        f'CSeq: {cseq} BYE',
        'Content-Length: 0', '', ''])


@dataclass
class Call:
    sip_peer: tuple[str, int]
    rtp_peer: tuple[str, int]
    call_id: str
    local_tag: str
    card: Card
    # Enough of the inbound dialog to hang up on the caller. A UAS BYE swaps
    # the roles the INVITE established: our To becomes the From and carries
    # our tag, the caller's From becomes the To with its own tag, and the
    # request goes to the remote target from Contact rather than to the AOR.
    # Without these the endpoint can only ever wait to be hung up on, which
    # leaves the far end holding a call the emulator has already forgotten.
    remote_from: str = ''
    local_to: str = ''
    target: str = ''
    cseq: int = 0
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
    status_block_scratch: bool = False
    # Media pacing. The modem's clock is virtual, so RX jitter is absorbed as
    # latency (hold the clock until the packet lands) rather than as silence
    # substituted into the sequence the far modem is measuring.
    rx_started: bool = False
    # Samples of bearer time served before this end's modem was on the line.
    # See Endpoint.setup_gap_samples: the bearer is up and carrying idle PCM
    # while the far end is still dialling, ringing or waiting to be answered.
    gap_samples: int = 0
    rx_hold_until: float | None = None
    # When to look again after a clock hold. Separate from next_tick so that
    # waiting for a late packet does not move the media schedule.
    rx_retry_at: float = 0.0
    rx_holds: int = 0
    # Produced quanta waiting for their turn on the wire, and the wire clock
    # that hands them out. The emulator fills this as fast as received audio
    # lets it; `next_send` is a strict 20 ms schedule that never moves except
    # on an underrun. See EiconSipEndpoint.service_transmit.
    tx_queue: collections.deque[bytes] = field(
        default_factory=collections.deque)
    next_send: float = 0.0
    tx_underruns: int = 0
    tx_queue_low: int = 1 << 30
    tx_sends: int = 0
    tx_thread: object = None
    tx_stop: object = None
    # The level of what the modem is handed, accumulated per sample and
    # reported per second. See EiconSipEndpoint.receive_health.
    rx_energy: float = 0.0
    rx_energy_samples: int = 0
    rx_peak_high: int = 0
    rx_peak_low: int = 0
    reported_rx_second: int = -1
    rx_substituted: int = 0
    rx_dropped: int = 0
    hold_time: float = 0.0
    # Warned once that this run is host-bound and its cycle counts are not
    # comparable with an unloaded one. See the check in the media report.
    host_bound_warned: bool = False
    catchup_deferrals: int = 0
    over_budget_ticks: int = 0
    worst_tick: float = 0.0
    # Where a 20 ms quantum's wall time goes: the data pump and everything
    # else in the sample loop, the G.711 encode and sendto, and the V.42/PPP
    # service. Their sum is `tick_seconds`, and 20 ms minus its mean is the
    # headroom this rig has to give back after a stall.
    tick_seconds: float = 0.0
    tick_count: int = 0
    pump_seconds: float = 0.0
    send_seconds: float = 0.0
    link_seconds: float = 0.0
    diag_seconds: float = 0.0
    tick_histogram: list[int] = field(default_factory=lambda: [0] * 21)
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
        self.lock = threading.Lock()
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
                        # DM(0x3F87) is the guide's RTDelay, round trip delay
                        # in 10 ms units -- not a DIL counter, which is what
                        # this column was called from Session 87 until 207.
                        # docs/addsp_database.md trap 2. DM(0x3F8B)/DM(0x3F8E)
                        # fall in a reserved run and keep their local names.
                        'dil_flag,rtdelay,dil_measure,'
                        # The read database quality block; see
                        # docs/addsp_database.md for the addressing, and read
                        # it before adding another location here. The eye
                        # samples are already above as eye0/eye1/eye2 at
                        # DM(0x3F9C..0x3F9E) -- they were briefly duplicated
                        # here at 0x3F1C, which is what you get by feeding
                        # guide 6.6's read-relative "location 3C" into the
                        # write-side formula. It reads zero, and looks exactly
                        # like an eye that is not being generated.
                        #
                        # SNRatio is 10log(average signal power / average
                        # squared error) off the received phase-point diagram,
                        # in half dB from 8 dB at 0x00 -- the same quantity
                        # slmodemd prints as its own SNR. INR is V.32 only.
                        #
                        # The rest of the quality block, added after a whole-
                        # window diff of a V.32 and a V.34/V.90 call showed all
                        # five moving and none of them recorded. They are the
                        # measurements a receiver makes about the line it is
                        # being handed, which is the open question on V.32:
                        # slmodemd scores our transmit at 8 dB while our own
                        # SNRatio reads 29-40 dB on the same G.711 path.
                        # FarEchoPhaseRoll is measured on V.34 only.
                        'snratio,inr,signalquality,'
                        'freqoffset,timoffset,phasejit,peakphaserr,'
                        'farechophaseroll,symbolrate,rxlevel,'
                        # The echo and line-probe block, added in Session 207.
                        # Every one of these reads a constant 0x0000 across all
                        # 28 archived captures, whole call, both modulations,
                        # while FarEchoPhaseRoll, Signalquality and RTDelay
                        # vary in the same records -- which is the positive
                        # control that says the read half is live and these
                        # four are never written. SNRPROB is the projected
                        # slicer SNR, the same quantity the INFO1d projected
                        # rate is built from.
                        'eclevel,nearectlevel,farectlevel,snrprob,'
                        # The V.90 upstream rate ladder, added because the
                        # rates it settles on are quantised and nothing was
                        # recording the word that quantises them. DM(0x0FCF)
                        # is the quality the ceiling DM(0x20BA) is derived
                        # from, and across the archive that ceiling follows
                        # 100/sqrt(quality) closely enough to predict every
                        # observed rate. What is missing is any quality
                        # between 0x5c and 0xd1, or between 0xe9 and 0x196 --
                        # exactly the bands that would select 19200, 21600,
                        # 24000 and 14400, which is why no call has ever used
                        # them. One value per call, sampled when the rate word
                        # changed, cannot say whether that is the metric or
                        # the sampling; a per-tick series can. The two masks
                        # are here to stay honest about the alternative: they
                        # have permitted every rate from 4800 up on every call
                        # measured, so nothing is locked out by a mask.
                        'upstream_quality,upstream_ceiling,'
                        'upstream_peer_mask,upstream_local_mask\n')
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
        # The transmit direction is written by the wire-clock thread and the
        # receive direction by the main loop, into the same pcap and the same
        # ip_id counter. Interleaved records would be a capture that is not
        # what went on the wire, which is the one thing this file has to be.
        with self.lock:
            self._write(rtp, payload, source, destination, outbound)

    def _write(self, rtp: bytes, payload: bytes, source: tuple[str, int],
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
        # Same files as write(), from the main loop only, but the pcap's
        # neighbours are shared and the diagnostic writes are large.
        with self.lock:
            self._write_diag(sample, card)

    def _write_diag(self, sample: int, card: Card) -> None:
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
                  dm[0x3F8B], dm[0x3F87], dm[0x3F8E],
                  dm[0x3F7D], dm[0x3F84], dm[0x3F86],
                  dm[0x3F7E], dm[0x3F7F], dm[0x3F82], dm[0x3F83],
                  dm[0x3F7C], dm[0x3F65], dm[0x3F78],
                  dm[0x3F79], dm[0x3F7A], dm[0x3F7B], dm[0x3F85],
                  dm[0x0FCF], dm[0x20BA], dm[0x1E3F], dm[0x210B])
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
                 watch_exec: tuple[tuple[int, int], ...] = (),
                 watch_dm: tuple[tuple[int, int], ...] = (),
                 assert_dm_clean: tuple[int, int] | None = None,
                 pc_histogram: Path | None = None,
                 pc_histogram_from: int | None = None,
                 info_actions: dict[int, int] | None = None,
                 db_words: dict[int, int] | None = None,
                 native_mips: bool = False,
                 tx_prbs: bool = False,
                 tx_v42: bool = False,
                 tx_v42bis: bool = False,
                 tx_v44: bool = False,
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
                 tick_budget_ms: float = 18.0, tx_buffer_ms: int = 160,
                 mips_interval: int = 160,
                 realtime: bool = False,
                 v42_pty: bool = False, at_terminal: bool = False,
                 ppp_config=None, ppp_pool=None, ppp_network=None,
                 ppp_ping: str | None = None, ppp_ping_count: int = 4,
                 ring_seconds: float = 2.0,
                 modem_role: str = 'answer',
                 originate_line_ready: bool | None = None,
                 originate_v8: bool | None = None,
                 dial_number: str = '', dial_target: str = '',
                 preboot: bool = False,
                 pc_histogram_state: int | None = None,
                 setup_gap_ms: float = 0.0,
                 watch_dm_writes: tuple[tuple[int, int], ...] = ()):
        self.bind = bind
        self.advertised = advertised
        self.law = law
        self.payload_type, self.silence, self.codec_name = LAW_INFO[law]
        self.other_rtp_payload_types: set[int] = set()
        self.registrar = registrar
        self.username = username
        self.password = password
        self.register_cseq = 0
        self.register_call_id = f'eicon-{random.randrange(2**64):016x}'
        # Keep one binding for the lifetime of the endpoint. Hardware batches
        # run long enough to approach the old one-hour expiry, and restarting
        # the process between calls churns the Asterisk contact and its qualify
        # state. Refresh a short lease instead; deregistration remains a
        # shutdown-only operation.
        self.register_expires = 300
        self.register_refresh_at: float | None = None
        self.register_request_expires = self.register_expires
        self.rx_guard_samples = max(0, rx_guard_ms * 8)
        # This end's modem is not on the line for the first N ms of the bearer.
        # On a real call the calling modem is running -- dialling, and then
        # waiting through call setup -- before the answering one is connected to
        # anything, so the two modems' clocks do not start together. Serving
        # them together is what put the answerer's first ANSam phase reversal
        # 20 ms ahead of the caller's V.8 deadline (Session 182), a margin
        # smaller than one RTP packet. During the gap the bearer carries idle
        # PCM and this end's card is not clocked at all.
        self.setup_gap_samples = max(0, int(setup_gap_ms * 8))
        self.force_info_after_v8 = force_info_after_v8
        self.kernel_dispatch = kernel_dispatch
        self.init_info_detector_at_24 = init_info_detector_at_24
        self.watch_exec = watch_exec
        self.watch_dm = watch_dm
        # Write-only watches, for a pointer a hung loop rereads. DM(0x2F29) and
        # DM(0x2F2B) are read 43 million times across a 0x00b3 stall (Session
        # 120) and written a handful of times; a read-and-write watch spends
        # its whole limit on the loop and never reaches the write that set it.
        self.watch_dm_writes = watch_dm_writes
        # Range asserted to take no DM writes for the life of the call.  A
        # bound on where a runaway pointer marches is not a fix and cannot be
        # verified by checking one table inside it (Session 114z); this checks
        # every word.
        self.assert_dm_clean = assert_dm_clean
        self.assert_dm_armed = False
        self.pc_histogram = pc_histogram
        # Zero the per-PC counters the moment this overlay becomes resident, so
        # the dump covers one page's residency instead of the whole call.
        self.pc_histogram_from = pc_histogram_from
        self.pc_histogram_started = False
        # Gate the histogram on a TrnProgress value rather than an overlay.
        # An overlay is resident for the whole call on page 14, so a dump keyed
        # to it covers everything and answers nothing about one state (Session
        # 118). Counters are cleared on entry to the state and read out on exit,
        # so what is dumped is the sum over that state's residency alone, and a
        # state entered several times contributes each visit.
        self.pc_histogram_state = pc_histogram_state
        self.pc_state_active = False
        self.pc_state_totals: dict[int, int] = {}
        self.pc_state_visits = 0
        self.pc_state_samples = 0
        self.pc_state_entry = 0
        self.pc_state_last_sample = 0
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
        self.tx_v44 = tx_v44
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
        # PPP is per call, not per endpoint: each caller gets a fresh peer, so
        # one client's failed authentication or half-closed link cannot be
        # inherited by the next.  Only the configuration lives up here.
        self.ppp_config = ppp_config
        self.ppp = None
        self.ppp_address = None
        # The pool outlives the call, which is the whole point: it is what
        # stops the second caller of a run being handed the first one's
        # address while that one is still on the line.
        self.ppp_pool = ppp_pool
        # The tun outlives calls for the same reason the pool does: creating
        # one per call would add and remove a system interface and its route
        # on every INVITE.
        self.ppp_network = ppp_network
        # The ping instrument, and its per-call state. Sequence numbers restart
        # with the call so a second call's replies cannot be matched against
        # the first one's requests.
        self.ppp_ping = ppp_ping
        self.ppp_ping_count = ppp_ping_count
        self.ppp_ping_seq = 0
        self.ppp_ping_due = 0.0
        self.ppp_ping_sent: dict[int, float] = {}
        self.ppp_ping_replies = 0
        # Reported and acted on once per call, not once per datagram.
        self.link_failure_reported = False
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
        # The maintained delay line. Unlike the prefill, which is only a
        # threshold for starting, this is a floor the queue is never consumed
        # below, so it survives steady state instead of collapsing to zero.
        self.rx_lag_samples = max(0, RX_LAG_MS * 8)
        if self.rx_lag_samples:
            print(f'[media] holding {RX_LAG_MS} ms of one-way receive delay '
                  f'({self.rx_lag_samples} samples)')
        # Above this the queue is backlog, not jitter margin, and is drained
        # ahead of the wall clock. Kept clear of the delay line, or the drain
        # would immediately give back what the delay line is holding.
        self.rx_drain_samples = (self.rx_prefill_samples + self.rx_lag_samples
                                 + SAMPLES_PER_PACKET)
        self.rx_hold_seconds = max(0.0, rx_hold_ms / 1000)
        self.rx_depth_samples = max(SAMPLES_PER_PACKET, rx_depth_ms * 8)
        # Decoding a G.711 code is a table lookup, and the receive level is
        # measured on every sample, so build the table once rather than call
        # the decoder 8,000 times a second.
        decode = (RtpCapture.decode_ulaw if law == 'pcmu'
                  else RtpCapture.decode_alaw)
        self.linear_table = [decode(code) for code in range(256)]
        self.catchup_quanta = max(1, catchup_quanta)
        # Produced quanta held between the emulator and the wire. This is not
        # added delay: the same audio was being held one stage earlier in the
        # receive jitter buffer, and the emulator now takes it from there as
        # fast as it arrives. What it buys is that a 70 ms emulator stall stops
        # being a 70 ms hole in what the far modem is demodulating.
        self.tx_target_quanta = max(0, tx_buffer_ms * 8 // SAMPLES_PER_PACKET)
        if self.tx_target_quanta:
            print(f'[media] transmit buffer: {self.tx_target_quanta} quanta '
                  f'({self.tx_target_quanta * 20} ms), wire clock decoupled '
                  f'from the data pump')
        else:
            print('[media] transmit buffer disabled; each quantum goes out as '
                  'it is produced')
        self.realtime = realtime
        self.tick_budget = tick_budget_ms / 1000
        self.mips_interval = mips_interval
        self.native_card = None
        # Card booted at dial time, waiting for the 200 OK. See dial().
        self.dialed_card = None
        # Card booted before any call arrives, so the several seconds of
        # firmware entry and bearer attachment are paid while idle instead of
        # inside the answer path. One card per call still: this is consumed by
        # the call that takes it and a fresh one is booted afterwards, so no
        # firmware state crosses a call boundary. See preboot().
        self.preboot_enabled = preboot
        self.preboot_card = None
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

    def send_register(self, challenge: dict[str, str] | None = None,
                      expires: int | None = None) -> None:
        if expires is None:
            expires = self.register_request_expires
        else:
            self.register_request_expires = expires
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
                 f'Expires: {expires}']
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
        # Retry if no final response arrives. A successful response below
        # replaces this with the normal refresh point.
        if expires:
            self.register_refresh_at = time.monotonic() + 30.0

    def deregister(self, timeout: float = 2.0) -> None:
        """Drop the registration on the way out.

        Without this the binding survives the process for the whole `Expires`
        hour, and the registrar goes on qualifying a contact that nothing is
        listening on. Asterisk marks such a contact `Unavail`, and
        `PJSIP_DIAL_CONTACTS()` expands only to available ones -- so a later
        run can register successfully, be running, answer OPTIONS, and still
        receive no INVITE, because the endpoint is still carrying the failed
        qualify from the previous run's corpse. That is a slow, confusing
        failure and it costs one packet to avoid.

        Driven synchronously: the selector loop has already stopped by the
        time this runs, so the 401 has to be answered here. Best effort with a
        short deadline -- a registrar that does not answer is not a reason to
        hang up the shutdown.
        """
        if not (self.registrar and self.username):
            return
        try:
            self.send_register(expires=0)
            deadline = time.monotonic() + timeout
            challenged = False
            while time.monotonic() < deadline:
                try:
                    data, _ = self.sip.recvfrom(65535)
                except (BlockingIOError, OSError):
                    time.sleep(0.02)
                    continue
                first, headers, _ = parse_sip(data)
                if not first.startswith('SIP/2.0'):
                    continue
                if not headers.get('cseq', '').upper().endswith('REGISTER'):
                    continue
                status = first.split()[1] if len(first.split()) > 1 else ''
                if status in ('401', '407') and not challenged:
                    challenge = self.digest_challenge(
                        headers.get('www-authenticate')
                        or headers.get('proxy-authenticate', ''))
                    if not (challenge.get('realm') and challenge.get('nonce')):
                        break
                    challenged = True
                    self.send_register(challenge, expires=0)
                    continue
                if status == '200':
                    print(f'[sip] deregistered {self.username}@{self.registrar}')
                    return
                break
            print(f'[sip] deregister not confirmed for '
                  f'{self.username}@{self.registrar}; the binding will lapse '
                  'on its own')
        except OSError as exc:
            print(f'[sip] deregister failed: {exc}')

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
                        self.send_register(
                            challenge, expires=self.register_request_expires)
                elif parts[1] == '200':
                    expires = self.register_request_expires
                    if expires:
                        self.register_refresh_at = (
                            time.monotonic() + max(30.0, expires * 0.75))
                        print(f'[sip] registered {self.username}@{self.registrar}; '
                              f'refresh in {max(30, int(expires * 0.75))}s')
                    else:
                        self.register_refresh_at = None
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
            offered = []
            for match in re.finditer(
                    r'(?im)^a=rtpmap:(\d+)\s+([^\s/]+)(?:/\d+)?', body):
                offered.append(f'{match.group(1)}={match.group(2)}')
            media_line = re.search(r'(?im)^m=audio[^\r\n]*', body)
            print('[sip] INVITE media offer: '
                  + (media_line.group(0) if media_line else 'no m=audio')
                  + ('; ' + ', '.join(offered) if offered else ''))
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
                self._dump_pc_histogram(self.call.card)
                downstream = getattr(
                    self.call.card, 'negotiated_downstream_bps', None)
                upstream = getattr(
                    self.call.card, 'negotiated_upstream_bps', None)
                if downstream is not None or upstream is not None:
                    print(f'[v90] final negotiated rates: downstream '
                          f'{downstream or "?"} bit/s, upstream '
                          f'{upstream or "?"} bit/s')
                lapm = getattr(self.call.card, 'lapm', None)
                if lapm is not None:
                    state = ('connected' if lapm.connected else
                             f'down ({lapm.failed})' if lapm.failed else 'down')
                    print(f'[v42] totals: state={state}, '
                          f'links={lapm.generation}, '
                          f're-establishments={lapm.stats.reestablish}, '
                          f'line disturbances={lapm.stats.suspensions}, '
                          f'discarded while establishing='
                          f'{lapm.stats.discarded_in_establishment}, '
                          f'HDLC good/bad/abort={lapm.decoder.good}/'
                          f'{lapm.decoder.bad_fcs}/{lapm.decoder.aborts}, '
                          f'XID rx/tx={lapm.stats.xid_rx}/{lapm.stats.xid_tx}, '
                          f'SABME rx/tx={lapm.stats.sabme_rx}/'
                          f'{lapm.stats.sabme_tx}, UA tx={lapm.stats.ua_tx}, '
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
                      f'{self.call.catchup_deferrals} catch-up deferrals, '
                      f'{self.tick_cost(self.call)}, '
                      f'{self.transmit_health(self.call)}')
                # The transmit wire clock owns this Call and may still be
                # sending from its queue. Stop it before dropping the last
                # endpoint reference; otherwise a normal remote BYE leaves a
                # stale thread alive across the next INVITE.
                self.stop_transmit_clock(self.call)
                self.call = None
                self.outgoing = None
                self.close_ppp()
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
            if len(packet) >= 2 and packet[0] >> 6 == 2:
                payload_type = packet[1] & 0x7F
                if payload_type not in self.other_rtp_payload_types:
                    self.other_rtp_payload_types.add(payload_type)
                    print(f'[rtp] ignoring first payload type {payload_type} '
                          f'from {peer[0]}:{peer[1]} (audio is '
                          f'PT {self.payload_type})')
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
        if not call.rx_started and self.rx_lag_samples:
            # Pad once, before the first real sample. Holding the queue deeper
            # does not delay anything the modem can measure: the consumer still
            # takes the peer's sample n on its own tick n, so a deeper queue
            # only moves when that happens in wall time. What ranging needs is
            # a shift in the sample correspondence, which is padding.
            call.rx.extend([self.silence] * self.rx_lag_samples)
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
            # Retry soon, but *not* by moving the media schedule. Writing
            # `next_tick = now + 0.002` here discarded however far behind the
            # schedule already was, so every hold quietly forgave the deficit
            # instead of making the loop work it off -- 27 holds on one live
            # call, against an 84 ms residual deficit the catch-up had
            # otherwise recovered 84% of. The quanta are owed either way: the
            # samples behind them are real received audio that has arrived,
            # and running them late is what the drain above exists to undo.
            call.rx_retry_at = now + 0.002
            call.hold_time += 0.002
            return False
        # Waited out the whole hold: the peer has genuinely stopped sending, so
        # run the quantum on silence rather than stalling the call.
        call.rx_hold_until = None
        return True

    def receive_health(self, call: Call) -> None:
        """Report the level of what the modem is being handed, and its verdict.

        The upstream rate a call settles on is set by DM(0x0FCF), and across
        the archive that word only ever lands in five bands. The receiver's own
        SNRatio says why, and says it far more sharply than a level alone
        would: over one call's 21,454 data-state records, DM(0x3F78) moving
        from 0x22 to 0x29 -- seven units -- took SNRatio from 36.5 dB to
        13.5 dB. Twenty-three decibels for seven is not what adding noise to a
        signal does, and it is the shape of something in the receive chain
        rather than something on the line.

        Both halves are printed together because neither is worth much alone:
        the measured level says what arrived, and SNRatio says what the
        receiver made of it.
        """
        second = call.samples // 8000
        if second == call.reported_rx_second or not call.rx_energy_samples:
            return
        if second % 10 and call.reported_rx_second >= 0:
            return
        call.reported_rx_second = second
        mean_square = call.rx_energy / call.rx_energy_samples
        rms = math.sqrt(mean_square) or 1e-9
        peak = max(call.rx_peak_high, -call.rx_peak_low) or 1
        call.rx_energy = call.rx_energy_samples = 0
        call.rx_peak_high = call.rx_peak_low = 0
        dm = getattr(call.card, 'dm', None)
        verdict = ''
        if dm is not None:
            # SNRatio is half-dB steps from 8 dB at 0x00; RxLevel and
            # Signalquality are the receiver's own words for the same call.
            verdict = (f', SNRatio {8 + dm[0x3F7D] / 2:.1f} dB '
                       f'(0x{dm[0x3F7D]:02x}), RxLevel 0x{dm[0x3F78]:02x}, '
                       f'Signalquality 0x{dm[0x3F86]:04x}, '
                       f'upstream quality 0x{dm[0x0FCF]:04x} '
                       f'ceiling {dm[0x20BA] * 2400} bit/s')
        print(f'[rx] {second} s: level {20 * math.log10(rms / DBM0_RMS):.1f} '
              f'dBm0, peak {20 * math.log10(peak / 32124):.1f} dBFS{verdict}')

    def transmit_health(self, call: Call) -> str:
        """How close the cushion came to running out.

        The low-water mark is the figure that matters: a buffer that never
        drops below its target was never tested, and one that reaches zero is
        letting emulator stalls back onto the wire. `rtp_pcap_timing.py` on the
        capture is the confirmation, but this says it during the call.
        """
        if not self.tx_target_quanta:
            return 'tx buffer off'
        low = (call.tx_queue_low if call.tx_queue_low <= self.tx_target_quanta
               else self.tx_target_quanta)
        return (f'tx buffer {len(call.tx_queue)}/{self.tx_target_quanta} '
                f'(low {low}, {call.tx_underruns} underruns)')

    @staticmethod
    def tick_cost(call: Call) -> str:
        """What a 20 ms quantum costs, and what that leaves to recover with.

        This is the pacing story in one clause. The transmit stream of a live
        call measured -1240 ppm against a receive stream at -5, and the wire
        said why: after a stall the loop runs quanta back to back, and those
        recovery quanta came out at a median 17.3 ms apiece. 20 ms of media for
        17 ms of real time is 3 ms of headroom, so a 73 ms stall takes half a
        second to repay and the next one lands first. Nothing was reporting it:
        a worst-tick figure and a count over an 18 ms budget cannot tell 3 ms
        of headroom from 15, and the median sat just under the alarm.
        """
        if not call.tick_count:
            return 'tick cost n/a'
        mean = call.tick_seconds / call.tick_count
        # The bucket the 95th percentile falls in, from the 2 ms histogram.
        target = call.tick_count * 0.95
        seen = 0
        p95 = len(call.tick_histogram) - 1
        for index, count in enumerate(call.tick_histogram):
            seen += count
            if seen >= target:
                p95 = index
                break
        share = (f'{call.pump_seconds / call.tick_seconds * 100:.0f}% pump/'
                 f'{call.diag_seconds / call.tick_seconds * 100:.0f}% capture/'
                 f'{call.send_seconds / call.tick_seconds * 100:.0f}% rtp/'
                 f'{call.link_seconds / call.tick_seconds * 100:.0f}% v42+ppp'
                 if call.tick_seconds else '')
        return (f'tick cost mean {mean * 1000:.1f} ms p95 <{(p95 + 1) * 2} ms '
                f'({(TICK_SECONDS - mean) * 1000:.1f} ms headroom, {share})')

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
              f'{self.tick_cost(call)}, {self.transmit_health(call)}, '
              f'wall {wall:.1f}s (ratio {second / wall:.2f}x)')
        # The rig is wall-clock paced, so host speed feeds back into the
        # emulated sample timeline: an unloaded run spends most of its time
        # holding the clock, and one that cannot keep up never holds at all and
        # sees a different timeline. Session 188s measured the difference --
        # --watch-dm-writes on a hot address moved the V.32 stall by 1.8 M
        # cycles purely through log volume, and 188o's frame count was taken
        # under exactly that. Say so, once, rather than let it be silent.
        if second >= 5 and call.rx_holds < second * 10 and not call.host_bound_warned:
            call.host_bound_warned = True
            print(f'[media] WARNING: host-bound -- only {call.rx_holds} clock '
                  f'holds in {second} s, so the emulated timeline is being set '
                  f'by how fast this machine runs, not by the 8 kHz clock. '
                  f'Cycle counts and frame counts from this run are not '
                  f'comparable with an unloaded one. Usually log volume: gate '
                  f'watches with EICON_WATCH_OVERLAY and avoid watching hot '
                  f'addresses.')

    def pump_pty(self) -> None:
        """Service the terminal, whether or not a call is up.

        The data link is whatever the current call has; with no call there is
        none, which ``PtyLink.pump`` handles -- in command mode it reads
        regardless of the window, so an idle terminal still gets its AT
        commands answered.
        """
        call = self.call
        lapm = getattr(call.card, 'lapm', None) if call else None
        if self.pty is not None:
            self.pty.pump(lapm)
        self.pump_ppp(lapm)
        self.check_link_failure(lapm)

    def check_link_failure(self, lapm) -> None:
        """Clear the call once the data link is unrecoverably down.

        A modem that loses V.42 beyond recovery drops the call, and the DTE
        redials; this endpoint used to hold the call open instead, so a PPP
        session whose link died at 145 s sat there to 320 s with a caller that
        had no way to know. LAPM has already tried to re-establish by the time
        `failed` is set, so there is nothing further to wait for.

        EICON_V42_HANGUP=0 keeps the call up for anyone watching the firmware
        rather than the session -- the link stays down either way.
        """
        if lapm is None or self.call is None or not self.services_link:
            return
        reason = getattr(lapm, 'failed', None)
        if reason is None or self.link_failure_reported:
            return
        self.link_failure_reported = True
        print(f'[v42] the data link is down and did not come back: {reason}')
        if os.environ.get('EICON_V42_HANGUP', '1') == '0':
            print('[v42] EICON_V42_HANGUP=0: leaving the call up')
            return
        self.hangup_call(f'V.42 link failure ({reason})')

    @property
    def services_link(self) -> bool:
        """Whether anything on this endpoint consumes the V.42 byte stream.

        A terminal and a PPP peer are alternatives -- they claim the same link
        -- so asking about one of them is never the right question.
        """
        return self.pty is not None or self.ppp_config is not None

    def pump_ppp(self, lapm) -> None:
        """Service the PPP peer, creating one when a call first has a link.

        Deferred until LAPM is up rather than built with the call: the peer
        sends its Configure-Request the moment it starts, and starting it
        against a link that cannot carry it yet would burn restarts on frames
        nobody receives.
        """
        if self.ppp_config is None or lapm is None or not lapm.data_ready:
            return
        if self.ppp is None:
            import dataclasses

            from ppp import LapmPppLink, PppPeer
            config = self.ppp_config
            if self.ppp_pool is not None:
                self.ppp_address = self.ppp_pool.allocate()
                config = dataclasses.replace(config,
                                             peer_address=self.ppp_address)
                print(f'[ppp] assigning {self.ppp_address} to this caller '
                      f'({len(self.ppp_pool)} of the pool in use)')
            peer = PppPeer(config)
            if self.ppp_network is not None:
                peer.attach_network(self.ppp_network)
            self.ppp = LapmPppLink(peer)
        self.ppp.pump(lapm, time.monotonic())
        if self.ppp_ping:
            self.service_ppp_ping(time.monotonic())

    def service_ppp_ping(self, now: float) -> None:
        """Send `--ppp-ping` echo requests once IPCP is up, and match replies.

        One a second, because the point is a round trip over a 2400 bit/s link
        and not a throughput test: an 8-byte payload is already ~0.2 s of line
        time each way once framing and LAPM are counted.

        The client end terminates IP itself -- no network is attached on that
        side -- so replies arrive in `peer.rx_ip` rather than going anywhere.
        """
        from ppp import icmp_echo_request, parse_icmp_echo_reply
        peer = self.ppp.peer
        if not peer.up:
            return
        for packet in peer.rx_ip:
            match = parse_icmp_echo_reply(packet)
            if match is None:
                continue
            identifier, sequence = match
            sent = self.ppp_ping_sent.pop(sequence, None)
            if sent is None:
                continue
            self.ppp_ping_replies += 1
            print(f'[ping] reply seq={sequence} in '
                  f'{(now - sent) * 1000:.0f} ms')
        del peer.rx_ip[:]
        if self.ppp_ping_seq >= self.ppp_ping_count or now < self.ppp_ping_due:
            return
        # `assigned` is what the far end settled on for itself, which on the
        # client is the server's own address -- the same value on_ipcp_up()
        # prints as `peer=`.
        destination = (self.ppp_ping if self.ppp_ping != 'peer' else
                       (peer.ipcp.assigned or peer.peer_address))
        self.ppp_ping_seq += 1
        self.ppp_ping_due = now + 1.0
        self.ppp_ping_sent[self.ppp_ping_seq] = now
        peer.send_ip(icmp_echo_request(peer.ipcp.local_address, destination,
                                       sequence=self.ppp_ping_seq))
        print(f'[ping] {destination} seq={self.ppp_ping_seq} sent')

    def close_ppp(self) -> None:
        """Tear the PPP peer down with the call that carried it."""
        if self.ppp is None:
            return
        self.ppp.close(time.monotonic())
        self.ppp = None
        if self.ppp_address is not None:
            # Drop this caller's flows before its address goes back in the
            # pool. The next caller can be issued the same address long before
            # the NAT's idle timeout would have reaped them, and it must not
            # inherit someone else's connections.
            dropper = getattr(self.ppp_network, 'drop_client', None)
            if dropper is not None:
                dropped = dropper(self.ppp_address)
                if dropped:
                    print(f'[ppp] closed {dropped} flow(s) for '
                          f'{self.ppp_address}')
            if self.ppp_pool is not None:
                self.ppp_pool.release(self.ppp_address)
            self.ppp_address = None

    def next_wakeup(self, now: float) -> float:
        """Selector timeout. A backlogged receive queue means do not sleep: the
        catch-up cap deliberately returns here between batches of quanta, and
        sleeping until the next scheduled tick would pace the drain back to real
        time and leave the backlog standing as latency for the rest of the call.
        """
        call = self.call
        if not call:
            # An attached terminal is polled rather than selected on: the PTY
            # master stays readable while LAPM back-pressure blocks a read, so
            # putting it in the selector would spin. 20 ms is imperceptible at
            # a keyboard and costs nothing while idle.
            return 0.02 if self.pty is not None else 0.25
        if not self.realtime and len(call.rx) > self.rx_drain_samples:
            return 0.0
        # A clock hold sets rx_retry_at rather than moving next_tick, so the
        # wake-up is whichever comes first: the next scheduled quantum, or the
        # short retry that is waiting for a packet to arrive.
        if self.tx_target_quanta:
            # Producing is gated on the queue, not the clock, so being below
            # target is work available now. Otherwise the only deadline is the
            # wire clock, which must not be slept through: it is the one thing
            # here the far end can measure.
            if self.wants_quantum(call, now) and not call.rx_hold_until:
                return 0.0
            due = call.next_send or (now + TICK_SECONDS)
        else:
            due = call.next_tick
        if call.rx_retry_at > now:
            due = min(due, call.rx_retry_at)
        return max(0.0, min(0.25, due - now))

    def wants_quantum(self, call: Call, now: float) -> bool:
        """Whether the emulator should run another 160 samples right now.

        With the transmit buffer on this is the queue depth and nothing else,
        which makes the emulator what it physically is -- a DSP consuming a
        sample stream -- instead of something driven by the wall clock. It
        cannot run away: it can only produce from received audio, so the
        peer's own clock is the long-run pacing, and the queue caps the
        short-run. The cushion costs no added delay either, because it is
        filled out of the receive jitter buffer that was already holding the
        same audio a stage earlier.

        With the buffer off (`--tx-buffer-ms 0`) it is the wall-clock schedule
        exactly as it was.
        """
        if not self.tx_target_quanta:
            return now >= call.next_tick
        return len(call.tx_queue) < self.tx_target_quanta

    def _queue_rtp(self, call: Call, linear: list[int]) -> None:
        """Encode one produced quantum and put it in line for the wire.

        Shared by the modem tick and the setup gap, so a held-off end still
        produces an unbroken RTP stream: the far end's clock is fed from this
        socket and nothing else, and a gap in the sequence would hold its modem
        clock rather than sound like an idle line.

        Encoding happens here rather than in the sender so that the send path
        is a header, a sendto and nothing else. What the sender must not do is
        work: it is the only thing on this thread with a real deadline.
        """
        call.tx_queue.append(self.codec.encode_g711(linear))
        if not self.tx_target_quanta:
            # Buffering disabled: straight to the wire, as it was before the
            # queue existed. The schedule is not consulted at all, so nothing
            # can slip and nothing can be held.
            self.transmit_one(call)

    def start_transmit_clock(self, call: Call) -> None:
        """Run the wire clock on its own thread.

        A queue alone does not decouple anything, and simulating it said so
        before a call had to: the emulator holds the thread for the whole of a
        70 ms stall, so a single-threaded sender cannot send during one however
        deep the cushion is. It converts a gap into a gap followed by a burst
        and leaves the far modem with the same hole.

        A thread does work here, and for a reason worth stating: the data pump
        is `ctypes.CDLL`, which releases the GIL for the whole of every
        `adsp2181_run`, and there are 320 of those in a quantum. Measured
        against the worst case -- a main thread executing pure Python, which
        yields only on the 5 ms switch interval -- a 20 ms sender is late by a
        median 4 ms and never more than 5. That is the bound, and it is set by
        the interpreter rather than by anything this rig does.

        Only this thread touches the socket's transmit side, the sequence
        numbers and the queue's read end, so the sharing is one deque between
        one producer and one consumer.
        """
        if not self.tx_target_quanta or call.tx_thread is not None:
            return
        call.tx_stop = threading.Event()
        call.tx_thread = threading.Thread(
            target=self._transmit_loop, args=(call,),
            name='rtp-wire-clock', daemon=True)
        call.tx_thread.start()

    def stop_transmit_clock(self, call: Call) -> None:
        if call.tx_thread is None:
            return
        call.tx_stop.set()
        call.tx_thread.join(timeout=1.0)
        call.tx_thread = None

    def _transmit_loop(self, call: Call) -> None:
        while not call.tx_stop.is_set():
            if not call.next_send:
                # Still filling the cushion. service_transmit starts the clock
                # when it is full; until then there is nothing to be late for.
                self.service_transmit(call, time.monotonic())
                if call.tx_stop.wait(0.002):
                    return
                continue
            delay = call.next_send - time.monotonic()
            if delay > 0 and call.tx_stop.wait(delay):
                return
            try:
                self.service_transmit(call, time.monotonic())
            except Exception as exc:              # pragma: no cover
                # A dead wire clock is silent, and silence here looks exactly
                # like a modem fault at the far end. Say so and stop.
                print(f'[media] the wire clock failed: {exc!r}')
                return

    def service_transmit(self, call: Call, now: float) -> None:
        """Hand queued quanta to the wire on a strict 20 ms schedule.

        This is the whole point of the queue. The emulator produces a quantum
        in a median 17 ms and occasionally stalls for 70-100, and until now
        each quantum went out at the moment it was produced, so the wire
        inherited every one of those stalls -- a transmit stream at -1240 ppm
        against a receive stream at -5. `next_send` accumulates absolutely, so
        as long as the queue is not empty the far end sees exactly 8000 Hz
        whatever the emulator is doing.

        An empty queue is the one case that cannot be papered over. Sending
        invented silence to keep the schedule is precisely the sin `rx_ready`
        refuses in the other direction -- a hole in the middle of what a modem
        is measuring is not recoverable, and it is worse than late audio. So an
        underrun slips the schedule to now and is counted; that is no worse
        than the old behaviour, which slipped on every quantum.
        """
        if not self.tx_target_quanta:
            return
        if not call.tx_queue:
            if call.next_send and now >= call.next_send + TICK_SECONDS:
                # A quantum's worth past due with nothing to send: the
                # emulator is behind, not the clock.
                call.tx_underruns += 1
                call.next_send = now
            return
        call.tx_queue_low = min(call.tx_queue_low, len(call.tx_queue))
        if not call.next_send:
            if len(call.tx_queue) < self.tx_target_quanta:
                return                      # still filling the cushion
            call.next_send = now
            print(f'[media] transmit buffer primed: '
                  f'{len(call.tx_queue) * SAMPLES_PER_PACKET / 8} ms of '
                  f'produced audio, wire clock started')
        while call.tx_queue and now >= call.next_send:
            self.transmit_one(call)
            call.next_send += TICK_SECONDS

    def transmit_one(self, call: Call) -> None:
        """Put the oldest queued quantum on the wire."""
        payload = call.tx_queue.popleft()
        call.tx_sends += 1
        marker = 0x80 if call.packets == 0 else 0
        header = struct.pack('!BBHII', 0x80, marker | self.payload_type,
                             call.tx_seq, call.tx_timestamp, call.ssrc)
        packet = header + payload
        self.rtp.sendto(packet, call.rtp_peer)
        if self.capture:
            source_ip = local_address_for(call.sip_peer, self.bind,
                                          self.advertised)
            self.capture.write(packet, payload, (source_ip, self.rtp_port),
                               call.rtp_peer, True)
        call.tx_seq = (call.tx_seq + 1) & 0xFFFF
        call.tx_timestamp = (call.tx_timestamp
                             + SAMPLES_PER_PACKET) & 0xFFFFFFFF
        call.packets += 1

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
            # The wire clock is the thread's; this loop only produces.
            self.start_transmit_clock(call)
            if not self.wants_quantum(call, now):
                return
            if served >= self.catchup_quanta:
                call.catchup_deferrals += 1
                return
            if call.gap_samples < self.setup_gap_samples:
                # Not on the line yet. Send the idle PCM the bearer carries so
                # the far end's clock is fed, drop what arrives (this modem was
                # not listening to it), and do not clock the card: its own
                # timers must start when it answers, not when the bearer came
                # up. Sequence and timestamp advance because those are wire
                # state, not modem state.
                if call.gap_samples == 0:
                    print(f'[media] setup gap: holding this end off the line '
                          f'for {self.setup_gap_samples / 8:.0f} ms of bearer '
                          f'time (idle PCM only)')
                call.rx.clear()
                self._queue_rtp(call, [0] * SAMPLES_PER_PACKET)
                call.gap_samples += SAMPLES_PER_PACKET
                call.next_tick += SAMPLES_PER_PACKET / 8000
                if call.gap_samples >= self.setup_gap_samples:
                    print('[media] setup gap over; this end is on the line')
                served += 1
                continue
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
                # What the modem is actually being handed, measured where it is
                # handed over. Two table lookups and two adds a sample; the
                # alternative is mining it out of a 26 MB capture afterwards,
                # which is how it went unexamined until the rate ladder made
                # someone ask.
                sample = self.linear_table[code]
                call.rx_energy += sample * sample
                call.rx_energy_samples += 1
                if sample > call.rx_peak_high:
                    call.rx_peak_high = sample
                elif sample < call.rx_peak_low:
                    call.rx_peak_low = sample
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
            call.pump_seconds += time.monotonic() - tick_start
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
            scratch = status_block_is_scratch(call.card.dm)
            if scratch != call.status_block_scratch:
                call.status_block_scratch = scratch
                if scratch:
                    print(f'[adsp] sample {call.samples} '
                          f'({call.samples / 8000:.3f}s): the status block '
                          f'DM(0x3fb0..0x3fca) has been taken over as scratch '
                          f'by the bit-reversal copy at PM 0x3b25 '
                          f'(TrnProgress reads 0x{trn_progress:04x}); page, '
                          f'state and Rstatus reporting suspended -- Session 136')
                else:
                    print(f'[adsp] sample {call.samples} '
                          f'({call.samples / 8000:.3f}s): status block is the '
                          f'state machine\'s again; reporting resumed')
            # Nothing is recorded while the block is scratch, so when the state
            # machine takes it back the next report is a transition from the
            # last value it actually published.
            if not scratch and (
                    trn_progress != call.trn_progress
                    or rstatus_ch != call.rstatus_ch
                    or rstatus != call.rstatus):
                info_rx = ''
                # Rstatus_ch bits D and B are the ADDSP guide's SPEEDTX and
                # SPEED: "the transmitter/receiver speed is available in the
                # DATASTATETX/DATASTATE read database location", read database
                # 0x81 and 0x82, which is DM(0x3F61)/DM(0x3F62) at the 0x3EE0
                # read-database base. They are validity flags, so print the two
                # words exactly when the card says they mean something.
                if rstatus_ch & 0x2800:
                    info_rx += (f'; DATASTATEspeedTx=0x{call.card.dm[0x3F61]:04x}'
                                f' DATASTATESpeed=0x{call.card.dm[0x3F62]:04x}')
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
                    # DM(0x3f87) is RTDelay, in 10 ms units, and was printed
                    # here as a DIL count for 120 sessions (207). It reads
                    # 6..0x1d over the archived captures -- 60-290 ms, varying
                    # run to run on one rig -- which is the round trip this
                    # SIP/ATA/two-wire path actually has, and the V.8
                    # classifier is already known to be delay-sensitive (§3).
                    # The echo and probe words are here because the DIL region
                    # is where they would be used and where they are zero.
                    print(f'[dil] sample {call.samples} '
                          f'({call.samples / 8000:.3f}s): '
                          f'flag DM(0x3f8b)=0x{dm[0x3F8B]:04x} '
                          f'measure DM(0x3f8e)=0x{dm[0x3F8E]:04x} '
                          f'RTDelay=0x{dm[0x3F87]:04x} '
                          f'({dm[0x3F87] * 10} ms) '
                          f'EcLevel=0x{dm[0x3F79]:04x} '
                          f'NearEcLevel=0x{dm[0x3F7A]:04x} '
                          f'FarEcLevel=0x{dm[0x3F7B]:04x} '
                          f'FarEchoPhaseRoll=0x{dm[0x3F7C]:04x} '
                          f'SNRPROB=0x{dm[0x3F85]:04x} '
                          f'Signalquality=0x{dm[0x3F86]:04x} '
                          f'Maxtimer=0x{dm[0x3F0C]:04x} '
                          f'Mintimer=0x{dm[0x3F0D]:04x} '
                          f'MAXTXSPEED=0x{dm[0x3F5C]:04x}/'
                          f'{dm[0x3F5D]:04x} '
                          f'MAXRXSPEED=0x{dm[0x3F5E]:04x}/'
                          f'{dm[0x3F5F]:04x}')
            di_control = call.card.dm[0x3FAD]
            baud_info = call.card.dm[0x3FBB]
            info_mode = call.card.dm[0x3F94]
            info_variant = call.card.dm[0x16B6]
            di_changed = di_control != call.di_control
            # PRBS mode services bit F at the DSP datagram rate. The complete
            # value remains in the binary/CSV capture; avoid synchronous log
            # I/O twice per request on the real-time media thread.
            # Masked, not compared for equality: the tick can coalesce two
            # datagrams, and then bit F has changed *and* something else has,
            # which an == 0x8000 test reads as a real event. That is where 1,779
            # of the [tx_request] lines in one live call came from. The claim
            # being made is "nothing but bit F moved", so say that.
            tx_request_only = ((self.tx_prbs or self.tx_v42)
                               and call.di_control >= 0 and
                               (di_control ^ call.di_control) & ~0x8000 == 0)
            # DM(0x16B6) is the INFO variant while the modems are still
            # negotiating. Once the data pump reaches synchronous data state
            # the word is scratch and changes constantly, which made this line
            # a per-tick trace exactly as tx_request_only was written to
            # prevent for DI_control: 17,926 of one live PPP call's 18,431
            # [adsp] lines were printed from the real-time media thread after
            # BaudInfo and INFO_mode had stopped moving, in a run that reported
            # itself host-bound for its whole length.
            data_state = bool(getattr(call.card,
                                      'negotiated_downstream_bps', None))
            if ((di_changed and not tx_request_only) or
                    baud_info != call.baud_info or
                    info_mode != call.info_mode_selector or
                    (info_variant != call.info_variant and not data_state)):
                # Through the cap rather than print(): this site is on the
                # media thread, is driven by DM words the firmware owns, and
                # has now run away twice for two different reasons. The cap is
                # the standing guard against the third, and names itself in the
                # exit summary instead of being found by reading a 19 MB log.
                emit(f'[adsp] sample {call.samples} '
                     f'({call.samples / 8000:.3f}s): '
                     f'DI_control=0x{di_control:04x}'
                     f'[{flag_names(di_control, DI_CONTROL_BITS)}] '
                     f'BaudInfo=0x{baud_info:04x} INFO_mode=0x{info_mode:04x} '
                     f'INFO_variant=0x{info_variant:04x}')
            call.baud_info = baud_info
            call.info_mode_selector = info_mode
            call.info_variant = info_variant
            call.di_control = di_control
            bootpage = call.card.dm[0x3FB0]
            if bootpage != call.bootpage and not scratch:
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
            if (self.assert_dm_clean and self.assert_dm_clean[2] is not None
                    and not self.assert_dm_armed
                    and getattr(call.card, 'resident', 0)
                    == self.assert_dm_clean[2]):
                self._arm_dm_assertion(getattr(call.card, 'card',
                                               call.card).cpu)
            if (self.pc_histogram_from is not None
                    and not self.pc_histogram_started
                    and getattr(call.card, 'resident', 0) == self.pc_histogram_from):
                from eicon_mips_shim import ADSP as _ADSP
                _ADSP.adsp2181_coverage_clear(
                    getattr(call.card, 'card', call.card).cpu)
                self.pc_histogram_started = True
                print(f'[pc-histogram] cleared at sample {call.samples} '
                      f'({call.samples / 8000:.3f}s), overlay '
                      f'0x{self.pc_histogram_from:04x} resident')
            if self.pc_histogram_state is not None:
                self._pc_state_track(call)
            if self.capture:
                # Timed separately because it is the one part of a quantum
                # that is pure diagnostics: about 1,450 individual DM reads
                # and 2.9 kB of writes per tick, which is 130 MB of .bin and
                # .csv over a five-minute run. If the headroom is not there,
                # this is the first thing to ask about.
                diag_start = time.monotonic()
                self.capture.write_diag(call.samples, call.card)
                call.diag_seconds += time.monotonic() - diag_start
            send_start = time.monotonic()
            self._queue_rtp(call, linear)
            call.send_seconds += time.monotonic() - send_start
            link_start = time.monotonic()
            # Once per 20 ms quantum, not per sample: a terminal does not
            # need 8 kHz service, and the LAPM window is what actually paces
            # it. This must not be gated on the PTY alone -- PPP claims the
            # same V.42 link and excludes --v42-pty, so a --ppp call had its
            # link serviced by nobody: the peer sent LCP, LAPM acked it, and
            # the bytes sat in rx_data for the whole call.
            if self.pty is not None:
                self.at_watch(call)
            if self.services_link:
                self.pump_pty()
            call.link_seconds += time.monotonic() - link_start
            call.next_tick += TICK_SECONDS
            served += 1
            elapsed = time.monotonic() - tick_start
            call.worst_tick = max(call.worst_tick, elapsed)
            # What a quantum costs is the whole pacing story and nothing was
            # measuring it: `worst` and a count over budget cannot tell a rig
            # with 15 ms of headroom from one with 3 ms, and it was 3. A
            # quantum is 20 ms of media, so the mean is how much of real time
            # this rig spends to produce real time, and 20 ms minus it is all
            # there is to repay a stall with. The buckets are 2 ms wide and
            # cover 0..40 ms, which is where every observed tick lands.
            call.tick_seconds += elapsed
            call.tick_count += 1
            call.tick_histogram[min(len(call.tick_histogram) - 1,
                                    int(elapsed * 500))] += 1
            if elapsed > self.tick_budget:
                call.over_budget_ticks += 1
            self.report_media(call)
            self.receive_health(call)
            now = time.monotonic()

    def build_card(self):
        """Boot an emulated card for one call, whichever way it was set up.

        The signalling role is always 'answer' -- the card is driven through
        its incoming-call path in both directions. Which side of the *modem*
        handshake this instance takes is `--modem-role`, published in
        GEN_SETUP1, and is the only thing that has to differ between the two
        ends of a loopback.
        """
        if self.preboot_card is not None:
            card = self.preboot_card
            self.preboot_card = None
            print('[preboot] taking the card booted at startup')
            return card
        if self.native_mips:
            if self.native_card is None:
                from eicon_mips_shim import create_native_mips_modem
                self.native_card = create_native_mips_modem(
                    self.mips_kernel, self.mips_tikrnl, self.law,
                    self.mips_image, self.mips_combifile,
                    force_info_after_v8=self.force_info_after_v8,
                    tx_prbs=self.tx_prbs, tx_v42=self.tx_v42,
                    tx_v42bis=self.tx_v42bis,
                    tx_v44=self.tx_v44,
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
        for address, limit in self.watch_exec:
            ADSP.adsp2181_watch_exec_limited(cpu, address, limit)
        from eicon_mips_shim import ADSP as _ADSP
        for address, limit in self.watch_dm:
            _ADSP.adsp2181_watch_dm_limited(cpu, address, limit)
        for address, limit in self.watch_dm_writes:
            _ADSP.adsp2181_watch_dm_writes(cpu, address, limit)
        if self.assert_dm_clean and self.assert_dm_clean[2] is None:
            self._arm_dm_assertion(cpu)
        return card

    def preboot(self) -> None:
        """Boot the card ahead of the call that will use it.

        Nothing clocks it here: the ADSP only advances on the sample clock, so
        a card sitting prebooted is in exactly the state the answer path would
        have built anyway, and the emulated timeline is unchanged. What moves
        is where the wall-clock cost lands -- ahead of the call rather than
        between the INVITE and the first media tick.
        """
        if not self.preboot_enabled or self.preboot_card is not None:
            return
        start = time.monotonic()
        print('[preboot] booting a card before the call arrives')
        try:
            self.preboot_card = self.build_card()
        except Exception:
            # A card that will not boot is a fault to report, not a reason to
            # stop listening: the answer path will try again and fail there,
            # where the existing handling already covers it.
            traceback.print_exc()
            print('[preboot] boot failed; the next call will boot its own')
            self.preboot_card = None
            return
        print(f'[preboot] card ready in {time.monotonic() - start:.1f}s')

    def _arm_dm_assertion(self, cpu) -> None:
        """Write-watch every word of the asserted range, one write each.

        One write per address is enough to fail the assertion and name the
        writer; more would only repeat it.  Arming is deferred to a page's
        residency when a page is named, because low DM is legitimately cleared
        once per call by PM 0x3738 -- an unconditional assertion spends its
        whole budget on that memset and never sees the write that matters,
        which is exactly the way the Session 114k-l verification passed.
        """
        from eicon_mips_shim import ADSP as _ADSP
        lo, hi = self.assert_dm_clean[0], self.assert_dm_clean[1]
        budget = self.assert_dm_clean[3]
        for address in range(lo, hi + 1):
            _ADSP.adsp2181_watch_dm_writes(cpu, address, budget)
        self.assert_dm_armed = True
        print(f'[assert-dm-clean] armed on DM 0x{lo:04x}..0x{hi:04x} '
              f'({hi - lo + 1} words, {budget} write(s) logged per address); '
              f'any [WATCH] dm w line is a failure')

    def _pc_state_track(self, call) -> None:
        """Clear on entry to the gated TrnProgress, read out on exit.

        The core counts every instruction fetch and cannot be paused, so a
        state's share is taken as a difference: clear when the state is
        entered and read the counters when it is left.

        The gate is polled once per media quantum, so each edge is accurate to
        20 ms and not better. The clear happens on the first quantum in which
        the state is seen, which discards that quantum -- part of which ran
        under the previous state -- and the read-out happens on the first
        quantum in which it is no longer seen, which includes one quantum of
        the next state. Both errors are one quantum per visit against states
        that last seconds, and the bias is deliberately toward discarding
        rather than admitting: a PC that genuinely runs in this state recurs
        in the remaining quanta, whereas one admitted from a neighbouring
        state is indistinguishable from a real result.

        Reading every counter costs 0x4000 calls, which is why it happens on
        exit and not on every quantum.
        """
        card = getattr(call.card, 'card', call.card)
        self.pc_state_last_sample = call.samples
        trn = card.dm[0x3FC2]
        if trn == self.pc_histogram_state:
            if not self.pc_state_active:
                from eicon_mips_shim import ADSP as _ADSP
                _ADSP.adsp2181_coverage_clear(card.cpu)
                self.pc_state_active = True
                self.pc_state_visits += 1
                self.pc_state_entry = call.samples
                print(f'[pc-histogram] entered TrnProgress '
                      f'0x{self.pc_histogram_state:04x} at sample '
                      f'{call.samples} ({call.samples / 8000:.3f}s), '
                      f'visit {self.pc_state_visits}; counters cleared')
        elif self.pc_state_active:
            self._pc_state_collect(call.card, call.samples)

    def _pc_state_collect(self, card, samples: int) -> None:
        """Fold the visit that is ending into the running totals."""
        from eicon_mips_shim import ADSP as _ADSP
        cpu = getattr(card, 'card', card).cpu
        for pc in range(0x4000):
            count = _ADSP.adsp2181_coverage_count(cpu, pc)
            if count:
                self.pc_state_totals[pc] = self.pc_state_totals.get(pc, 0) + count
        held = samples - self.pc_state_entry
        self.pc_state_samples += held
        self.pc_state_active = False
        print(f'[pc-histogram] left TrnProgress 0x{self.pc_histogram_state:04x} '
              f'at sample {samples} ({samples / 8000:.3f}s) after {held} samples '
              f'({held / 8000:.3f}s); {len(self.pc_state_totals)} PCs so far')

    def _dump_pc_histogram(self, card) -> None:
        """Write per-PC execution counts for the call.

        The core already keeps this: `coverage[0x4000]` is incremented on every
        instruction fetch, per CPU, and is exported as
        `adsp2181_coverage_count()`.  Nothing needed enabling -- what was
        missing was a dump, and its absence is why Sessions 114m-114s measured
        execution rates by watching one address per call and comparing across
        runs.  A histogram costs no log volume and answers "what ran while the
        samples stopped" in a single call.

        Opcodes are read back at dump time, so for PM at or above 0x2000 they
        are the *resident* page's instructions.  The resident overlay is
        printed with the header for that reason.
        """
        if not self.pc_histogram or card is None:
            return
        from eicon_mips_shim import ADSP as _ADSP
        cpu = getattr(card, 'card', card).cpu
        if self.pc_histogram_state is not None:
            # A call that ends inside the gated state still has that visit in
            # the counters; fold it in rather than discarding it.
            if self.pc_state_active:
                self._pc_state_collect(card, self.pc_state_last_sample)
            counts = self.pc_state_totals
        else:
            counts = {pc: _ADSP.adsp2181_coverage_count(cpu, pc)
                      for pc in range(0x4000)}
        rows = [(pc, _ADSP.adsp2181_read_pm(cpu, pc) & 0xFFFFFF, count)
                for pc, count in sorted(counts.items()) if count]
        try:
            from adsp2181_dis import disas
        except Exception:                                   # pragma: no cover
            disas = lambda op: ''                            # noqa: E731
        resident = getattr(card, 'resident', 0)
        self.pc_histogram.parent.mkdir(parents=True, exist_ok=True)
        with self.pc_histogram.open('w') as out:
            out.write(f'# resident=0x{resident:04x} '
                      f'cleared_at_overlay='
                      f'{"none" if self.pc_histogram_from is None else hex(self.pc_histogram_from)}\n')
            if self.pc_histogram_state is not None:
                out.write(f'# gated_on_trnprogress='
                          f'0x{self.pc_histogram_state:04x} '
                          f'visits={self.pc_state_visits} '
                          f'samples={self.pc_state_samples} '
                          f'({self.pc_state_samples / 8000:.3f}s)\n')
            out.write('pc\topcode\texecutions\tdisassembly\n')
            for pc, op, count in rows:
                out.write(f'{pc:04x}\t{op:06x}\t{count}\t{disas(op)}\n')
        total = sum(count for _, _, count in rows)
        scope = (f'TrnProgress 0x{self.pc_histogram_state:04x} only, '
                 f'{self.pc_state_visits} visit(s), '
                 f'{self.pc_state_samples / 8000:.3f}s'
                 if self.pc_histogram_state is not None
                 else f'resident=0x{resident:04x}')
        print(f'[pc-histogram] {len(rows)} PCs executed, {total} instructions, '
              f'{scope}; wrote {self.pc_histogram}')
        for pc, op, count in sorted(rows, key=lambda r: -r[2])[:20]:
            share = 100.0 * count / total if total else 0.0
            print(f'  {pc:04x}  {count:12d}  {share:5.1f}%  {disas(op)}')

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
        # Captured at answer, because this is the last point the INVITE's
        # headers are in hand and a BYE at shutdown needs them.
        self.call.remote_from = headers.get('from', '').strip()
        self.call.local_to = headers.get('to', '').strip()
        self.call.target = contact_uri(headers.get('contact', ''),
                                       f'sip:{peer[0]}:{peer[1]}')
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
        downstream = speed
        upstream = 0
        if carrier == 'V90' and hasattr(card, 'negotiated_downstream_bps'):
            downstream = card.negotiated_downstream_bps or speed
            upstream = card.negotiated_upstream_bps or 0
        elif carrier == 'V34':
            upstream = speed
        call.at_connected = True
        print(f'[at] CONNECT {carrier} downstream {downstream} bit/s, '
              f'upstream {upstream or "?"} bit/s '
              f'(rate word 0x{rate:04x})')
        if self.pty is not None:
            self.pty.write_terminal(
                self.at.connected(downstream, upstream, carrier, protocol,
                                  'NONE'))

    def hangup_call(self, reason: str) -> None:
        """End the current call *from this side*, telling the far end.

        Only for the cases where we are the one hanging up. The BYE-received
        path tears the call down inline and must not answer a BYE with a BYE.

        This matters more than it looks. Until now the endpoint could only
        wait to be hung up on: killing it during a call left Asterisk holding
        a leg to 6001 that nothing would ever end, and subsequent calls to
        that extension came back BUSY -- from two different modems on two
        different FXS ports, which is what finally made it obvious the fault
        was ours rather than the ATA's.
        """
        call = self.call
        if call is None:
            return
        if call.remote_from and call.local_to:
            peer = call.sip_peer
            local_ip = local_address_for(peer, self.bind, self.advertised)
            call.cseq += 1
            message = build_inbound_bye(
                target=call.target, via_host=local_ip,
                via_port=self.sip_port,
                branch=f'{random.randrange(2**48):012x}',
                from_header=call.local_to, local_tag=call.local_tag,
                to_header=call.remote_from, call_id=call.call_id,
                cseq=call.cseq)
            try:
                self.sip.sendto(message.encode(), peer)
                print(f'[call] BYE sent to {peer[0]}:{peer[1]}')
            except OSError as exc:
                # A shutdown path must not fail because the socket already
                # went; the call is over either way.
                print(f'[call] could not send BYE: {exc}')
        self.end_call(reason)

    def end_call(self, reason: str) -> None:
        """Drop the current call, telling the terminal if one is attached."""
        if self.call is None:
            return
        print(f'[call] ended by {reason}')
        # Before anything else releases the call: the wire clock is a thread
        # holding this object, and it must not be sending for a call that has
        # been torn down under it.
        self.stop_transmit_clock(self.call)
        # Every teardown comes through here, including the shutdown path a
        # loopback run always takes -- reporting this from the BYE branch alone
        # would have printed it on no loopback capture at all.
        census = getattr(self.call.card, 'lec_publish_census', None)
        if census is not None:
            line = census()
            if line:
                print(f'[adsp] {line}')
        self.call = None
        self.outgoing = None
        self.link_failure_reported = False
        self.close_ppp()
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
        self.close_ppp()

    def run(self) -> None:
        print(f'[sip] listening on {self.bind}:{self.sip_port}; RTP '
              f'{self.bind}:{self.rtp_port}; {self.codec_name} only; '
              f'modem role {self.modem_role}')
        # Before the dial grace period is measured, not after: booting a card
        # takes seconds, and starting the countdown first would spend the
        # whole of it here and dial the moment the boot returned.
        self.preboot()
        if self.pty is not None:
            print('[at] terminal is live; AT commands are answered now, '
                  'not only once a call is up')
        dial_at = None
        if self.dial_number:
            # A moment's grace so the far instance is listening and any
            # REGISTER has been answered before the INVITE goes out.
            dial_at = time.monotonic() + 1.0
        try:
            while self.running:
                now = time.monotonic()
                if (self.registrar and self.username
                        and self.register_refresh_at is not None
                        and now >= self.register_refresh_at):
                    # Refresh the existing Contact and Call-ID; do not tear it
                    # down between calls. send_register() installs a retry
                    # deadline until Asterisk confirms the new lease.
                    self.send_register(expires=self.register_expires)
                if self.call is None:
                    # Idle: nothing else services the terminal.
                    self.pump_pty()
                # Re-test rather than sharing the branch above: pump_pty() is
                # what dispatches ATA, so a call can be established between the
                # two, and booting a card takes seconds -- long enough to stall
                # the media loop just as the peer starts training. Replace a
                # consumed card only while genuinely idle, so the stall lands
                # between calls rather than at the start of one.
                if self.call is None:
                    self.preboot()
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
            # A loopback run always ends by SIGTERM from the rig, so a
            # histogram that only dumps on `[call] ended` never got written
            # there at all -- the instrument existed and produced nothing.
            if self.pc_histogram and self.call is not None:
                self._dump_pc_histogram(self.call.card)
            # Before deregistering, and before the sockets go: a call still up
            # at shutdown has to be ended towards the far end, or the switch
            # goes on believing this extension is busy long after the process
            # is gone.
            self.hangup_call('shutdown')
            # Before the sockets go: this needs self.sip still open, and the
            # registrar would otherwise keep qualifying a dead contact.
            self.deregister()
            if self.pty is not None:
                self.pty.close()
            if self.capture:
                self.capture.close()
            if self.trace_stream is not None:
                self.trace_stream.close()


def _parse_dm_assertion(text: str) -> tuple[int, int, int | None, int]:
    """Parse LO:HI[:BUDGET][@OVERLAY] into (lo, hi, overlay_or_None, budget).

    BUDGET is writes logged per address, default 1.  More than one is what
    turns the assertion into a survey: with a budget of 1 the first writer of
    each word -- often a one-shot memset -- hides every writer after it, which
    is how Session 115e ended up sampling four addresses instead of reading the
    region's ownership straight off.
    """
    body, _, page = text.partition('@')
    fields = body.split(':')
    lo, hi = int(fields[0], 0), int(fields[1], 0)
    budget = int(fields[2], 0) if len(fields) > 2 else 1
    return lo, hi, int(page, 0) if page else None, budget


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
    compression = ap.add_mutually_exclusive_group()
    compression.add_argument('--tx-v42bis', action='store_true',
                    help='negotiate V.42bis compression on the experimental '
                         'V.42 endpoint (requires --tx-v42)')
    compression.add_argument('--tx-v44', action='store_true',
                    help='negotiate V.44 compression on the experimental '
                         'V.42 endpoint (requires --tx-v42)')
    ap.add_argument('--v42-pty', action='store_true',
                    help='expose the V.42 link as a pseudo-terminal and print '
                         'its path; attach with screen or minicom '
                         '(requires --tx-v42)')
    ap.add_argument('--ppp', action='store_true',
                    help='run a dial-in PPP server on the V.42 link, so a '
                         'client that dials in gets LCP, authentication and '
                         'an IP address (requires --tx-v42). IP terminates in '
                         'this process: the assigned server address answers '
                         'ping, and nothing is routed to the host network')
    ap.add_argument('--ppp-client', action='store_true',
                    help='take the calling half of PPP instead of the '
                         'answering half, which is what the originating '
                         'instance of a loopback needs (implies --ppp)')
    ap.add_argument('--ppp-ping', metavar='ADDRESS', default=None,
                    help='once IPCP is up, ping ADDRESS from the client end '
                         'and report the replies (requires --ppp-client). '
                         'The cheapest proof that the link carries user data '
                         'and not only its own negotiation; "peer" pings '
                         'whichever address the server assigned itself')
    ap.add_argument('--ppp-ping-count', type=int, default=4,
                    help='how many echo requests --ppp-ping sends, one a '
                         'second (default 4)')
    ap.add_argument('--ppp-auth', choices=('none', 'pap', 'chap'),
                    default='chap',
                    help='what the server demands of the caller (default '
                         'chap; pap sends the password in the clear and '
                         'exists for clients that cannot do better)')
    ap.add_argument('--ppp-user', default='ppp',
                    help='the single account the server accepts, and the '
                         'username the client presents (default ppp)')
    ap.add_argument('--ppp-password', default='ppp',
                    help='the secret for --ppp-user (default ppp)')
    ap.add_argument('--ppp-local', default='100.64.0.1', metavar='IP',
                    help="this end's address (default 100.64.0.1, in the "
                         'RFC 6598 carrier-NAT range)')
    ap.add_argument('--ppp-pool', default='100.64.0.0/10', metavar='CIDR',
                    help='the prefix callers are assigned from (default '
                         '100.64.0.0/10, RFC 6598 shared address space). Each '
                         'call takes the next free address and gives it back '
                         'when it ends. Shared space is used rather than '
                         'RFC 1918 because a caller is far more likely to be '
                         'on 10/8 or 192.168/16 already, and an address that '
                         "collides with its own LAN costs it that LAN")
    ap.add_argument('--ppp-peer', default='', metavar='IP',
                    help='assign this exact address to every caller instead '
                         'of allocating from --ppp-pool')
    ap.add_argument('--ppp-trace', action='store_true',
                    help='log every PPP packet in and out, with its options '
                         'named. A dial-in client cannot usually be '
                         'instrumented, so this is how a failed negotiation '
                         'is diagnosed without placing another call')
    ap.add_argument('--ppp-tun', action='store_true',
                    help='route the caller through a kernel tun device rather '
                         'than the userspace NAT. Needs root, which the rest '
                         'of this harness does not, but it carries every '
                         'protocol and lets the host reach the caller')
    ap.add_argument('--ppp-no-network', action='store_true',
                    help='give the caller no network: IP terminates in this '
                         'process and only ping to --ppp-local is answered. '
                         'Use it to tell a link problem from a network one')
    ap.add_argument('--ppp-tun-name', default='', metavar='NAME',
                    help='ask for a specific tun interface (utunN, or a Linux '
                         'name); default is the first free one')
    ap.add_argument('--ppp-dns', default='', metavar='IP[,IP]',
                    help='the DNS servers offered over IPCP (default the '
                         'server address, which answers nothing -- clients '
                         'ask for these and some refuse to proceed without)')
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
    ap.add_argument('--setup-gap-ms', type=float, default=0.0,
                    help='hold this end off the line for the first N ms of '
                         'the bearer: idle PCM is sent, arriving audio is '
                         'dropped and the card is not clocked. Models the '
                         'calling modem running through dialling and call '
                         'setup before the answering one is connected. The '
                         'loopback sets this on the answerer; on a live call '
                         'the network provides the gap and this stays 0 '
                         '(Session 182)')
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
    ap.add_argument('--tx-buffer-ms', type=int, default=160,
                    help='produced audio held between the data pump and the '
                         'wire clock, so that an emulator stall is not a hole '
                         'in what the far modem demodulates. Filled from the '
                         'receive jitter buffer, so with --rx-jitter-ms at or '
                         'above this it costs no added delay. Simulated '
                         'against the observed stalls, 160 is where underruns '
                         'reach zero. 0 sends each quantum as it is produced, '
                         'which is what the wire saw before (default: 160)')
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
                    help='comma-separated PM addresses to log on execution, '
                         'each optionally ADDR:LIMIT to log only the first '
                         'LIMIT executions (e.g. 0x2e21:10) -- required for '
                         'addresses inside hot loops, which reach hundreds of '
                         'millions of executions per call; 0x3515 is the '
                         'control-channel bit decision, where ax1 in the '
                         '[EXEC] line is the correlator magnitude the firmware '
                         'thresholds at 0x0578')
    ap.add_argument('--watch-dm', default='',
                    help='comma-separated DM addresses to watch (logs reads '
                         'and the writer PC via [WATCH] dm r / dm w), each '
                         'optionally ADDR:LIMIT to log only the first LIMIT '
                         'events -- required for addresses a hung loop sweeps, '
                         'which can reach millions of touches per call')
    ap.add_argument('--watch-dm-writes', default='',
                    help='like --watch-dm but logs writes only ([WATCH] dm w '
                         'with the writer PC and registers). Use this for an '
                         'address a running loop rereads: a pointer written '
                         'five times and read millions of times gives nothing '
                         'under --watch-dm, because the reads spend the limit '
                         'first. Same ADDR[:LIMIT] syntax')
    ap.add_argument('--assert-dm-clean', default='',
                    help='LO:HI range of DM that must take no writes for the '
                         'life of the call; each word is write-watched once, '
                         'so every [WATCH] dm w line in the log is a failure '
                         'and names the writer. Append @OVERLAY to arm only '
                         'once that page is resident, which low DM needs: it '
                         'is legitimately cleared once per call by PM 0x3738. '
                         'Use 0x0061:0x0241@0x0261 for the bulk worker sweep '
                         '(Session 114z). Insert :BUDGET as LO:HI:BUDGET to '
                         'log more than one write per address, which turns the '
                         'assertion into an ownership survey of the range '
                         '(Session 115f)')
    ap.add_argument('--pc-histogram', type=Path, default=None,
                    help='write per-PC execution counts for the call to this '
                         'TSV (pc, opcode, executions, disassembly) and print '
                         'the top 20; costs no log volume, unlike --watch-exec')
    ap.add_argument('--pc-histogram-from', default='',
                    help='zero the counters when this overlay becomes resident '
                         '(e.g. 0x0261), so the histogram covers one page\'s '
                         'residency instead of the whole call')
    ap.add_argument('--pc-histogram-state', default='',
                    metavar='TRNPROGRESS',
                    help='gate the histogram on a TrnProgress value instead of '
                         'an overlay: clear the counters on entry to that '
                         'state and read them out on exit, so the dump is that '
                         "state's residency alone and nothing else. A state "
                         'entered several times contributes every visit. Use '
                         'this rather than --pc-histogram-from when the '
                         'overlay stays resident for the whole call, which on '
                         'page 14 it does')
    ap.add_argument('--preboot', action='store_true',
                    help='boot a card at startup and keep one booted between '
                         'calls, instead of booting inside the answer path. '
                         'The card is not clocked while it waits -- the ADSP '
                         'only advances on the sample clock -- so the '
                         'emulated timeline is unchanged and only the '
                         'wall-clock cost moves. Each call still consumes its '
                         'card and the next one is booted fresh, so no '
                         'firmware state crosses a call boundary')
    ap.add_argument('--init-info-detector-at-24', action='store_true',
                    help='diagnostic: invoke firmware PM 0x2602 at INFO state 0x24')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    if (args.tx_prbs or args.tx_v42) and not args.native_mips:
        ap.error('--tx-prbs/--tx-v42 require --native-mips')
    if args.tx_v42bis and not args.tx_v42:
        ap.error('--tx-v42bis requires --tx-v42')
    if args.tx_v44 and not args.tx_v42:
        ap.error('--tx-v44 requires --tx-v42')
    if args.at and not args.v42_pty:
        ap.error('--at requires --v42-pty')
    ppp_config = None
    ppp_pool = None
    ppp_network = None
    if args.ppp_tun and not (args.ppp or args.ppp_client):
        ap.error('--ppp-tun requires --ppp')
    if args.ppp_ping and not args.ppp_client:
        # The server end has a network attached and would re-originate the
        # datagram as a host socket, which tests the NAT rather than the link.
        ap.error('--ppp-ping requires --ppp-client: the ping has to start at '
                 'the dial-in end for the round trip to cross the modem link')
    if args.ppp_ping_count < 1:
        ap.error('--ppp-ping-count must be at least 1')
    if args.ppp or args.ppp_client:
        if not args.tx_v42:
            ap.error('--ppp requires --tx-v42: PPP needs the error-corrected '
                     'link, not the raw data pump')
        if args.v42_pty:
            ap.error('--ppp and --v42-pty both claim the V.42 link; use one')
        from ppp import AddressPool, PppConfig
        dns = [part.strip() for part in args.ppp_dns.split(',') if part.strip()]
        dns = (dns + dns)[:2] if dns else [args.ppp_local] * 2
        peer_address = args.ppp_peer
        if not args.ppp_client and not peer_address:
            try:
                # Reserve this end's address so the pool cannot issue it to a
                # caller, which would be a silent address conflict on the link.
                ppp_pool = AddressPool(args.ppp_pool, reserve=(args.ppp_local,))
            except ValueError as exc:
                ap.error(f'--ppp-pool: {exc}')
            if args.ppp_local not in ppp_pool:
                print(f'[ppp] note: {args.ppp_local} is outside '
                      f'{args.ppp_pool}; callers will be on a different '
                      'prefix from this end')
            peer_address = args.ppp_local    # replaced per call from the pool
        ppp_config = PppConfig(
            role='client' if args.ppp_client else 'server',
            # The client asks to be assigned an address; only the server has
            # one to offer.
            local_address='0.0.0.0' if args.ppp_client else args.ppp_local,
            peer_address=peer_address,
            dns=tuple(dns),
            auth=None if (args.ppp_auth == 'none' or args.ppp_client)
                 else args.ppp_auth,
            secrets={args.ppp_user: args.ppp_password},
            username=args.ppp_user, password=args.ppp_password,
            icmp_echo=not args.ppp_client,
            trace=args.ppp_trace)
        if args.ppp_tun:
            from tun import TunBridge, TunDevice, TunError
            device = TunDevice(name=args.ppp_tun_name)
            try:
                device.open()
                # The pool prefix is routed to the interface, so any address
                # the pool later assigns is already reachable; the
                # point-to-point peer is only there to satisfy ifconfig.
                device.configure(args.ppp_local,
                                 ppp_pool.preview() if ppp_pool
                                 else args.ppp_peer,
                                 routes=(args.ppp_pool,))
            except TunError as exc:
                device.close()
                ap.error(f'--ppp-tun: {exc}')
            except Exception:
                device.close()
                raise
            print(f'[ppp] {device.name} up: {args.ppp_local}, '
                  f'{args.ppp_pool} routed to it')
            ppp_network = TunBridge(device)
        elif not args.ppp_no_network and not args.ppp_client:
            from usernet import UserNetwork
            # The gateway address must be known here: whatever --ppp-dns
            # advertises defaults to it, and a query to it is answered by the
            # NAT's proxy rather than sent to an address nothing routes to.
            ppp_network = UserNetwork(local_address=args.ppp_local)
            print('[ppp] userspace NAT: TCP, UDP and ICMP echo are '
                  're-originated as host sockets. No root, nothing routed')
    if args.pc_histogram_from and not args.pc_histogram:
        ap.error('--pc-histogram-from requires --pc-histogram')
    if args.pc_histogram_state and not args.pc_histogram:
        ap.error('--pc-histogram-state requires --pc-histogram')
    if args.pc_histogram_state and args.pc_histogram_from:
        # The overlay clear would land inside a state visit and silently
        # discard part of it; the two gates cannot both own the counters.
        ap.error('--pc-histogram-state and --pc-histogram-from are exclusive')
    endpoint = EiconSipEndpoint(args.bind, args.sip_port, args.rtp_port,
                                args.advertise, args.verbose,
                                args.capture_prefix, args.law, args.registrar,
                                args.username, args.password, args.rx_guard_ms,
                                args.force_info_after_v8, args.kernel_dispatch,
                                args.init_info_detector_at_24,
                                tuple((int(field.split(':')[0], 0),
                                       int(field.split(':')[1], 0)
                                       if ':' in field else 0)
                                      for field in args.watch_exec.split(',')
                                      if field.strip()),
                                tuple((int(field.split(':')[0], 0),
                                       int(field.split(':')[1], 0)
                                       if ':' in field else 0)
                                      for field in args.watch_dm.split(',')
                                      if field.strip()),
                                (_parse_dm_assertion(args.assert_dm_clean)
                                 if args.assert_dm_clean else None),
                                args.pc_histogram,
                                (int(args.pc_histogram_from, 0)
                                 if args.pc_histogram_from else None),
                                {int(pair.split(':')[0], 0): int(pair.split(':')[1], 0)
                                 for pair in args.info_action.split(',')
                                 if pair.strip()},
                                {int(pair.split(':')[0], 0): int(pair.split(':')[1], 0)
                                 for pair in args.db_word.split(',')
                                 if pair.strip()},
                                args.native_mips, args.tx_prbs, args.tx_v42,
                                args.tx_v42bis, args.tx_v44,
                                args.mips_kernel, args.mips_tikrnl, args.mips_image,
                                args.mips_combifile, args.trace_v90d_state,
                                args.prime_v90d_bulk_cursor,
                                args.native_bearer_activation,
                                args.trace_file, args.rx_jitter_ms,
                                args.rx_hold_ms, args.rx_depth_ms,
                                args.catchup_quanta, args.tick_budget_ms,
                                args.tx_buffer_ms,
                                args.mips_interval, realtime=args.realtime, v42_pty=args.v42_pty,
                                at_terminal=args.at,
                                ppp_config=ppp_config, ppp_pool=ppp_pool,
                                ppp_network=ppp_network,
                                ppp_ping=args.ppp_ping,
                                ppp_ping_count=args.ppp_ping_count,
                                ring_seconds=args.ring_seconds,
                                setup_gap_ms=args.setup_gap_ms,
                                modem_role=args.modem_role,
                                originate_line_ready=args.originate_line_ready,
                                originate_v8=args.originate_v8,
                                dial_number=args.dial or '',
                                dial_target=args.dial_target or '',
                                preboot=args.preboot,
                                pc_histogram_state=(
                                    int(args.pc_histogram_state, 0)
                                    if args.pc_histogram_state else None),
                                watch_dm_writes=tuple(
                                    (int(field.split(':')[0], 0),
                                     int(field.split(':')[1], 0)
                                     if ':' in field else 0)
                                    for field in args.watch_dm_writes.split(',')
                                    if field.strip()))
    signal.signal(signal.SIGINT, lambda *_: setattr(endpoint, 'running', False))
    signal.signal(signal.SIGTERM, lambda *_: setattr(endpoint, 'running', False))
    try:
        endpoint.run()
    finally:
        if ppp_network is not None:
            print(f'[ppp] {ppp_network.summary()}')
            # For a tun this takes the interface and its route with it, so a
            # run that ends does not leave a dead utun and a route to nowhere;
            # for the userspace NAT it closes every flow's socket.
            closer = getattr(ppp_network, 'device', ppp_network)
            closer.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
