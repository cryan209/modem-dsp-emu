#!/usr/bin/env python3
"""Call one emulated card from another and trace both ends.

Every closed-loop test in this project has needed the physical Courier on a
real line, and `docs/handoff.md` §5 is explicit that offline replay cannot
answer questions about what the card advertises or how a peer reacts.  This
runs two `eicon_adsp_sip.py` instances on loopback, points one at the other,
and captures both sides, so both ends of a failed handshake are readable.

    tools/eicon_loopback.py --modulation v34,0,,33600,,33600 --seconds 40

What it is not: proof of interoperability.  Two emulated ends share the same
bugs and can agree with each other while both being wrong, so a loopback that
connects does not retire the Courier.  What it is good for is the opposite
case -- a handshake that fails with both ends instrumented, which is a much
better position than one instrumented end against a black box.

Roles.  Both instances are driven through their *incoming*-call signalling
path; the emulated card has no outgoing Q.931 state machine and does not need
one, because which side of the modem handshake an instance takes is
GEN_SETUP1 (`--modem-role`), not who sent the SETUP.  The caller runs
`calling`, the answerer `answer`.  Session 74 found that forcing `calling`
broke V.8, but that was an open-loop replay against a recording of a peer
that had itself called in; a loopback is the first configuration where the
two roles can actually be opposite.

V.90 was written off here -- it needs an analogue-side client against a
digital-side server, and both instances were the digital side.  Session 134
removed that: the PRI firmware admits V.90A once download `0x026b` is staged,
so the two ends can be given different modulations.

    tools/eicon_loopback.py \
        --answerer-firmware-set pri117 --answerer-modulation v90 \
        --caller-firmware-set analog109 --caller-modulation v90a \
        --caller-kernel-dispatch

`--caller-kernel-dispatch` is not optional here, and leaving it off costs a
whole session's measurements.  The direct backend clocks the analog codec at
8000; `docs/ansam_envelope_loss.md` measured what that does -- the envelope
detector chain runs 5/6 slow, its 14.4 Hz passband lands at 12 Hz, and ANSam's
15 Hz falls outside it, so `DM(0x0778)` never reaches the 240 it needs.  The
caller then sends CI, hears ANSam and never answers it: the CI builder at
V8.ANA `PM 0x3817` runs, the CM builder at `PM 0x3828` does not, and V.8 parks
at `TrnProgress 0x0001` on bootpage 6 for the rest of the call.  Under kernel
dispatch the same rig walks 6 -> 7 INFO -> 14 V.90 DPCM / 13 V.90 APCM.

That mixed direct-ADSP configuration is the faithful topology: the answering
end is the PRI/DPCM digital modem and the calling end is the Analog-card/APCM
modem, with its own `TIKRNL81.ANA`, `V8.ANA`, `INFO.ANA`, `V34.ANA`, and
`V90.ANA` family.  The older all-PRI diagnostic remains available with
`--native-mips`; for that case `EICON_DSP_EXTRA_DOWNLOADS=0x026b` is added to
the V.90A end automatically because the PRI file set otherwise answers "V.90A
not supported".  It proves the PRI task can admit APCM, but it is not the same
execution path as an analogue card.  Neither configuration is proof of
interoperability -- see above.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
VENV_PYTHON = Path("/tmp/eicon-venv/bin/python")


def free_port(start: int) -> int:
    """First free UDP port at or above `start`.

    Both instances bind before either dials, and a stale endpoint from an
    earlier run holding 5060 has cost this project five calls before
    (handoff.md, Sessions 85-86), so the ports are checked rather than
    assumed.
    """
    for port in range(start, start + 200, 2):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    raise RuntimeError(f"no free UDP port at or above {start}")


def build_command(args, *, role: str, firmware_set: str, native_mips: bool,
                  sip_port: int, rtp_port: int, prefix: Path,
                  dial: "tuple[str, int] | None",
                  kernel_dispatch: bool = False,
                  db_word: str = "", preboot: bool = False) -> list[str]:
    python = str(args.python)
    command = [python, "-u", str(TOOLS / "eicon_adsp_sip.py"),
               "--bind", "127.0.0.1", "--advertise", "127.0.0.1",
               "--sip-port", str(sip_port), "--rtp-port", str(rtp_port),
               "--law", args.law, "--modem-role", role,
               "--firmware-set", firmware_set,
               "--capture-prefix", str(prefix)]
    if db_word:
        command += ["--db-word", db_word]
    if preboot:
        command.append("--preboot")
    if kernel_dispatch:
        command.append("--kernel-dispatch")
        command += ["--analog-codec-rate", str(args.analog_codec_rate)]
    if native_mips:
        command += ["--native-mips",
                    "--mips-kernel", str(args.mips_kernel),
                    "--mips-tikrnl", str(args.mips_tikrnl)]
        if args.mips_interval != 160:
            command += ["--mips-interval", str(args.mips_interval)]
        if firmware_set == "analog109":
            command += ["--mips-image",
                        str(TOOLS.parent / "docs/firmware/build-109/te_dmlt.am")]
        if args.native_bearer_activation:
            command.append("--native-bearer-activation")
        if args.force_info_after_v8:
            command.append("--force-info-after-v8")
        # --tx-prbs is on by default and is the same data source as --tx-v42:
        # PPP is what fills the transmitter when it is running, so the PRBS
        # filler has to stand down rather than compete for it.
        if args.tx_prbs and not args.ppp:
            command.append("--tx-prbs")
    if args.trace_v90d_state:
        command.append("--trace-v90d-state")
    if args.trace_v90a_state:
        command.append("--trace-v90a-state")
    if args.trace_retrain:
        command.append("--trace-retrain")
    # The media path's own latency is not cosmetic on a V.90 call: INFO
    # measures the round trip into DM(0x3FCB), and the APCM page's state
    # 0x0070 waits DM(0x3FCB)+0x3F on it (PM 0x3530). Every millisecond of
    # jitter buffer and transmit buffer here lands in that state's duration.
    command += ["--rx-jitter-ms", str(args.rx_jitter_ms),
                "--rx-hold-ms", str(args.rx_hold_ms),
                "--tx-buffer-ms", str(args.tx_buffer_ms)]
    if args.watch_exec:
        command += ["--watch-exec", args.watch_exec]
    if args.watch_dm:
        command += ["--watch-dm", args.watch_dm]
    if args.watch_dm_writes:
        command += ["--watch-dm-writes", args.watch_dm_writes]
    if args.assert_dm_clean:
        command += ["--assert-dm-clean", args.assert_dm_clean]
    if args.pc_histogram:
        # Per end, so the two can be diffed: the rig's whole value here is
        # that the same firmware runs on both sides, so an address one end
        # executes and the other does not is the difference between them.
        command += ["--pc-histogram",
                    str(prefix.with_suffix(".pc-histogram.txt"))]
        if args.pc_histogram_state:
            command += ["--pc-histogram-state", args.pc_histogram_state]
        elif args.pc_histogram_from:
            command += ["--pc-histogram-from", args.pc_histogram_from]
    if args.at:
        command += ["--v42-pty", "--at",
                   "--ring-seconds", str(args.ring_seconds)]
    if args.ppp:
        # The answering side is the server, because that is the side a real
        # caller reaches. The calling side takes the client half, so the whole
        # negotiation happens over the emulated data pump rather than between
        # two peers wired together in one process.
        command += ["--tx-v42", "--ppp",
                    "--ppp-auth", args.ppp_auth,
                    "--ppp-user", args.ppp_user,
                    "--ppp-password", args.ppp_password]
        if role == "calling":
            command.append("--ppp-client")
            if args.ppp_ping:
                command += ["--ppp-ping", args.ppp_ping,
                            "--ppp-ping-count", str(args.ppp_ping_count)]
    if args.rx_guard_ms is not None:
        command += ["--rx-guard-ms", str(args.rx_guard_ms)]
    if role == "answer" and args.setup_gap_ms:
        # Only the answering end. The calling modem is the one that is already
        # running while the call is being set up, so giving the gap to the
        # answerer is what puts the two modems' clocks where a real call puts
        # them. Serving them together left the answerer's first ANSam phase
        # reversal 20 ms ahead of the caller's V.8 deadline, which any one-way
        # delay over ~12 ms -- or one late media tick -- then spent (182).
        command += ["--setup-gap-ms", str(args.setup_gap_ms)]
    if args.realtime:
        command.append("--realtime")
    if args.catchup_quanta != 2:
        command += ["--catchup-quanta", str(args.catchup_quanta)]
    if dial is not None:
        number, target_port = dial
        command += ["--dial", number,
                    "--dial-target", f"127.0.0.1:{target_port}"]
    return command


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=40.0,
                    help="how long to let the call run before shutting both "
                         "instances down (default 40)")
    ap.add_argument("--modulation", default="",
                    help="EICON_MODULATION for both instances, e.g. "
                         "'v34,0,,33600,,33600'. Leaving this unset means the "
                         "call falls back on its own")
    ap.add_argument("--answerer-modulation", default=None,
                    help="EICON_MODULATION for the answering end alone, "
                         "overriding --modulation. With --caller-modulation "
                         "this is how the two ends take opposite sides of a "
                         "V.90 link: 'v90' here and 'v90a' there")
    ap.add_argument("--caller-modulation", default=None,
                    help="EICON_MODULATION for the calling end alone, "
                         "overriding --modulation")
    ap.add_argument("--number", default="6001",
                    help="called party number to dial (cosmetic on loopback)")
    ap.add_argument("--law", choices=("pcmu", "pcma"), default="pcmu")
    ap.add_argument("--firmware-set", choices=("pri117", "analog109"),
                    default="pri117",
                    help="direct-ADSP firmware family for both ends (default "
                         "pri117); per-end options override it")
    ap.add_argument("--answerer-firmware-set", choices=("pri117", "analog109"),
                    default=None,
                    help="firmware family for the answering/digital end only")
    ap.add_argument("--caller-firmware-set", choices=("pri117", "analog109"),
                    default=None,
                    help="firmware family for the calling/analogue end only; "
                         "use analog109 with caller modulation v90a for the "
                         "real APCM topology")
    ap.add_argument("--answerer-native-mips", dest="answerer_native_mips",
                    default=None, action="store_true",
                    help="run the answering end on the native MIPS tower "
                         "regardless of --native-mips. That is the backend "
                         "every archived V.90 result was taken on, so it is "
                         "the one to pair against; the calling end can stay "
                         "on the direct backend, which is the only one that "
                         "holds the 8 kHz clock for an Analog card")
    ap.add_argument("--no-answerer-native-mips", dest="answerer_native_mips",
                    action="store_false", help=argparse.SUPPRESS)
    ap.add_argument("--caller-native-mips", dest="caller_native_mips",
                    default=None, action="store_true",
                    help="run the calling end on the native MIPS tower "
                         "regardless of --native-mips")
    ap.add_argument("--no-caller-native-mips", dest="caller_native_mips",
                    action="store_false", help=argparse.SUPPRESS)
    ap.add_argument("--caller-kernel-dispatch", action="store_true",
                    help="let the calling end's own card kernel dispatch "
                         "TIKRNL, instead of the harness calling its frame "
                         "entries. On analog109 that is the SPORT1 path which "
                         "fills RXSAMPLE; it is exclusive with "
                         "--caller-native-mips")
    ap.add_argument("--answerer-kernel-dispatch", action="store_true",
                    help="the same for the answering end")
    ap.add_argument("--answerer-preboot", action="store_true",
                    help="boot the answering card at startup instead of inside "
                         "the answer path, so its page sequence finishes before "
                         "any media tick. A live call gets this for free -- the "
                         "INVITE-to-RTP gap is long enough -- and without it the "
                         "loopback starts clocking the card while V.22FC is "
                         "still resident and V.8 lands on a running page")
    ap.add_argument("--answerer-db-word", default="", metavar="ADDR:VALUE[,...]",
                    help="data-pump database words to write on the answering "
                         "end after boot, e.g. 0x3f09:0xa13f to give it the "
                         "NORM_L a live call gets from the card's own answer "
                         "WDB instead of the shim's 0xB13F default")
    ap.add_argument("--caller-db-word", default="", metavar="ADDR:VALUE[,...]",
                    help="the same for the calling end")
    ap.add_argument("--analog-codec-rate", type=int, default=9600,
                    help="SPORT1 codec rate for an analog109 kernel-dispatch "
                         "end. V.8 asks for 9600 (Samplerate code 4) and its "
                         "tone constants are 9600 Hz constants, so 8000 emits "
                         "every tone at 5/6 -- the V.25 calling tone lands at "
                         "1083.5 Hz instead of 1300 -- and its ANSam envelope "
                         "detector never counts up. Pass 8000 only to "
                         "reproduce that")
    ap.add_argument("--sip-port", type=int, default=5070,
                    help="base SIP port; the answerer takes this and the "
                         "caller the next free one above it")
    ap.add_argument("--rtp-port", type=int, default=4010)
    ap.add_argument("--capture-dir", type=Path,
                    default=Path("artifacts/loopback"),
                    help="both sides are captured here as caller.* and "
                         "answerer.*")
    ap.add_argument("--native-mips", action="store_true",
                    help="run the real card firmware on both ends; roughly "
                         "five seconds of boot each and the configuration "
                         "that matches a live call")
    ap.add_argument("--mips-kernel", type=Path,
                    default=Path("artifacts/eicon-dsp/build-117-926/kernel/"
                                 "0009-diva-server-pri-30m-kernel"))
    ap.add_argument("--mips-tikrnl", type=Path,
                    default=Path("artifacts/eicon-dsp/build-117-926/tikrnl/"
                                 "0258-tikrnl81.f34-task"))
    ap.add_argument("--native-bearer-activation", action="store_true",
                    default=True)
    ap.add_argument("--no-native-bearer-activation", action="store_false",
                    dest="native_bearer_activation")
    ap.add_argument("--force-info-after-v8", action="store_true")
    ap.add_argument("--tx-prbs", action="store_true", default=True,
                    help="answer TX requests with PRBS on both ends so the "
                         "data path has something to carry (default on)")
    ap.add_argument("--no-tx-prbs", action="store_false", dest="tx_prbs")
    ap.add_argument("--trace-v90d-state", action="store_true")
    ap.add_argument("--trace-retrain", action="store_true",
                    help="trace local retrain markers and the state history "
                         "on both endpoint processes")
    ap.add_argument("--rx-jitter-ms", type=int, default=40)
    ap.add_argument("--rx-hold-ms", type=int, default=60)
    ap.add_argument("--tx-buffer-ms", type=int, default=160,
                    help="these three set the rig's own round-trip delay, "
                         "which INFO measures into DM(0x3FCB) and the APCM "
                         "page then waits out in state 0x0070")
    ap.add_argument("--mips-interval", type=int, default=160,
                    help="native-MIPS supervisor interval in samples "
                         "(default: 160; larger values reduce host cost)")
    ap.add_argument("--trace-v90a-state", action="store_true",
                    help="trace the APCM page's outer machine on the analogue "
                         "end; pair it with --trace-v90d-state to see which "
                         "end is waiting on the other")
    ap.add_argument("--watch-exec", default="",
                    help="comma-separated PM addresses to exec-watch on both "
                         "ends (forwarded to eicon_adsp_sip.py --watch-exec)")
    ap.add_argument("--watch-dm", default="",
                    help="comma-separated DM addresses to watch on both ends "
                         "(forwarded to eicon_adsp_sip.py --watch-dm). This "
                         "reports reads as well as writes, so on a word the "
                         "firmware polls it spends its budget before anything "
                         "interesting happens -- use --watch-dm-writes there")
    ap.add_argument("--watch-dm-writes", default="",
                    help="comma-separated DM addresses to write-watch on both "
                         "ends, reporting the storing PC "
                         "(forwarded to eicon_adsp_sip.py --watch-dm-writes)")
    ap.add_argument("--assert-dm-clean", default="",
                    help="LO:HI[:BUDGET][@OVERLAY] write-watch over a DM range "
                         "on both ends (forwarded to eicon_adsp_sip.py). With "
                         "a budget it is an ownership survey rather than an "
                         "assertion, which is how the page-8 transmit history "
                         "at 0x3680:0x36c9 was read (Session 152)")
    ap.add_argument("--caller-env", metavar="KEY=VALUE", action="append",
                    default=[],
                    help="set an environment variable for the calling end "
                         "only. The rig's value is that both ends are the same "
                         "firmware, so a variable that reaches only one of them "
                         "keeps the other as the control")
    ap.add_argument("--answerer-env", metavar="KEY=VALUE", action="append",
                    default=[],
                    help="set an environment variable for the answering end "
                         "only")
    ap.add_argument("--pc-histogram", action="store_true",
                    help="dump each end's PC histogram to "
                         "<capture-dir>/<end>.pc-histogram.txt. Both ends run "
                         "the same firmware, so diffing the two is what names "
                         "the code one of them does not reach")
    ap.add_argument("--pc-histogram-state", default="",
                    help="clear the histogram on entry to this TrnProgress and "
                         "dump it on exit, so the window is one state rather "
                         "than the whole call")
    ap.add_argument("--pc-histogram-from", default="",
                    help="clear the histogram when this overlay becomes "
                         "resident (exclusive with --pc-histogram-state)")
    ap.add_argument("--at", action="store_true",
                    help="put an AT command terminal on both ends so ATD "
                         "places the call and the answerer presents RING then "
                         "CONNECT; without this the caller auto-dials via "
                         "--dial and the answerer auto-answers silently")
    ap.add_argument("--ppp", action="store_true",
                    help="run PPP over the V.42 link: the answerer is the "
                         "server, the caller is the client. Implies --tx-v42 "
                         "on both ends and cannot be combined with --at, "
                         "which claims the same link")
    ap.add_argument("--ppp-auth", choices=("none", "pap", "chap"),
                    default="chap",
                    help="what the answering side demands of the caller "
                         "(default chap)")
    ap.add_argument("--ppp-user", default="ppp")
    ap.add_argument("--ppp-password", default="ppp")
    ap.add_argument("--ppp-ping", metavar="ADDRESS", default=None,
                    help="once IPCP is up, ping ADDRESS from the calling end "
                         "and report the replies (requires --ppp). 'peer' "
                         "pings the answering end's own address, which is the "
                         "round trip that crosses the modem link and nothing "
                         "else")
    ap.add_argument("--ppp-ping-count", type=int, default=4,
                    help="how many echo requests --ppp-ping sends, one a "
                         "second (default 4)")
    ap.add_argument("--rx-guard-ms", type=int, default=None,
                    help="forwarded to both endpoints: how much received audio "
                         "is replaced with silence before the modem hears it "
                         "(the FXS off-hook transient guard, default 1000 in "
                         "eicon_adsp_sip.py). It has to be shorter than the "
                         "setup gap or the modem is deaf into its own "
                         "handshake -- see Session 182")
    ap.add_argument("--setup-gap-ms", type=float, default=2000.0,
                    help="hold the answering end off the line for the first N "
                         "ms of the bearer, sending idle PCM and not clocking "
                         "its card (default 2000). A real caller runs through "
                         "dialling and call setup before the answering modem "
                         "is connected to anything; starting both together "
                         "left the answerer's first ANSam phase reversal 20 ms "
                         "ahead of the caller's V.8 deadline, so any one-way "
                         "delay over ~12 ms cost the call V.8 (Session 182). "
                         "0 restores the pre-182 simultaneous start")
    ap.add_argument("--ring-seconds", type=float, default=2.0,
                    help="how long the answerer rings before auto-answering "
                         "when S0>=1 (default 2.0s). Requires --at")
    ap.add_argument("--realtime", dest="realtime", default=True, action="store_true",
                    help="pace both endpoints to wall clock so the V.8/V.34 "
                         "handshake stays synchronized instead of one "
                         "racing ahead (default on for --at loopback)")
    ap.add_argument("--no-realtime", dest="realtime", action="store_false",
                    help="let the endpoints free-run (the old loopback "
                         "behaviour; the answerer races ahead and V.8 fails)")
    ap.add_argument("--catchup-quanta", type=int, default=2,
                    help="max ticks per wake-up (default 2). 1 with --realtime "
                         "gives strict 1x pacing")
    ap.add_argument("--originate-line-ready", dest="originate_line_ready",
                    default=True, action="store_true",
                    help="for the calling instance, pin DM(0x0554) so the "
                         "dial page does not wait on the dial-tone/DTMF tone "
                         "detector a PRI never arms (Sessions 95-96). "
                         "Default on; this is the only way the caller does "
                         "anything at all")
    ap.add_argument("--no-originate-line-ready", dest="originate_line_ready",
                    action="store_false",
                    help="leave the calling instance to wait on the tone "
                         "detector, i.e. reproduce the inert caller of "
                         "Sessions 95-96 for A/B")
    ap.add_argument("--originate-v8", dest="originate_v8",
                    default=True, action="store_true",
                    help="for the calling instance, request the V.8 overlay "
                         "once the dial page reaches training start, since "
                         "the originate firmware never does (default on)")
    ap.add_argument("--no-originate-v8", dest="originate_v8",
                    action="store_false",
                    help="do not force a V.8 request from the caller")
    ap.add_argument("--python", type=Path, default=VENV_PYTHON,
                    help="interpreter with unicorn installed")
    args = ap.parse_args()

    if args.ppp_ping and not args.ppp:
        ap.error("--ppp-ping requires --ppp: there is no IP link without it")
    if args.ppp and args.at:
        ap.error("--ppp and --at both claim the V.42 link; use one")
    if not Path(args.python).exists():
        ap.error(f"{args.python} does not exist; the harnesses need the venv "
                 "that has unicorn")
    answerer_firmware_set = (args.answerer_firmware_set
                             or args.firmware_set)
    caller_firmware_set = args.caller_firmware_set or args.firmware_set
    if args.native_mips:
        for path in (args.mips_kernel, args.mips_tikrnl):
            if not path.exists():
                ap.error(f"{path} does not exist")

    args.capture_dir.mkdir(parents=True, exist_ok=True)
    answerer_sip = free_port(args.sip_port)
    caller_sip = free_port(answerer_sip + 2)
    answerer_rtp = free_port(args.rtp_port)
    caller_rtp = free_port(answerer_rtp + 2)

    environment = dict(os.environ)
    if args.modulation:
        environment["EICON_MODULATION"] = args.modulation
    environment["EICON_MODEM_ROLE"] = "answer"
    # The originate-side line-ready pin is what makes the caller do anything;
    # it is forwarded through the env var so both instances pick it up and the
    # answerer (which does not need it) simply ignores it.
    environment["EICON_ORIGINATE_LINE_READY"] = (
        "1" if args.originate_line_ready else "0")
    environment["EICON_ORIGINATE_V8"] = (
        "1" if args.originate_v8 else "0")

    def end_environment(base: "dict[str, str]", modulation: "str | None",
                        firmware_set: str, label: str,
                        extra: "list[str]" = ()) -> "dict[str, str]":
        """One end's environment, including the PRI V.90A prerequisite.

        The Analog firmware set already contains its native APCM page. Asking
        PRI firmware for V.90A without staging the compatible APCM overlay is
        a different test: it answers "V.90A not supported" and negotiates as
        though the option had never been named (Session 134).
        """
        end = dict(base)
        for setting in extra:
            key, _, value = setting.partition("=")
            end[key.strip()] = value
            print(f"[loopback] {label}: {key.strip()}={value}")
        if modulation is None:
            return end
        end["EICON_MODULATION"] = modulation
        # A PRI V.90D endpoint is attached to the 2185N SPORT timeslot, whose
        # receive callback supplies right-justified signed PCM rather than the
        # raw PCMU octet.  The low-level direct-card helper keeps its legacy
        # A/B default for callers that use it directly, but the loopback's
        # normal V.90 topology should exercise the hardware-correct boundary.
        if (label == "answerer" and firmware_set == "pri117"
                and modulation.split(",")[0].strip().lower() == "v90"
                and "EICON_EXPAND_SPORT" not in end):
            end["EICON_EXPAND_SPORT"] = "1"
            print(f"[loopback] {label}: enabling hardware-correct PRI "
                  "SPORT PCM expansion (EICON_EXPAND_SPORT=1)")
        if (firmware_set == "pri117"
                and modulation.split(",")[0].strip().lower() == "v90a"):
            extras = [field for field
                      in end.get("EICON_DSP_EXTRA_DOWNLOADS", "").split(",")
                      if field.strip()]
            if not any(int(field, 0) == 0x026B for field in extras):
                extras.append("0x026b")
                end["EICON_DSP_EXTRA_DOWNLOADS"] = ",".join(extras)
                print(f"[loopback] {label}: staging the V.90 APCM overlay "
                      f"(EICON_DSP_EXTRA_DOWNLOADS={end['EICON_DSP_EXTRA_DOWNLOADS']})")
        return end

    print(f"[loopback] answerer SIP {answerer_sip} RTP {answerer_rtp}; "
          f"caller SIP {caller_sip} RTP {caller_rtp}")
    print(f"[loopback] firmware: answerer={answerer_firmware_set}, "
          f"caller={caller_firmware_set}")
    if args.modulation:
        print(f"[loopback] both ends: EICON_MODULATION={args.modulation}")
    for label, modulation in (("answerer", args.answerer_modulation),
                              ("caller", args.caller_modulation)):
        if modulation is not None:
            print(f"[loopback] {label}: EICON_MODULATION={modulation}")
    print(f"[loopback] originate-line-ready="
          f"{'on' if args.originate_line_ready else 'off'} "
          f"(caller skips dial-tone/DTMF wait; Sessions 95-96)")
    print(f"[loopback] originate-v8="
          f"{'on' if args.originate_v8 else 'off'} "
          f"(caller requests V.8 at training start)")
    effective_guard = 1000 if args.rx_guard_ms is None else args.rx_guard_ms
    if args.setup_gap_ms and effective_guard >= args.setup_gap_ms:
        print(f"[loopback] WARNING: rx guard {effective_guard} ms is not "
              f"shorter than the {args.setup_gap_ms:.0f} ms setup gap, so the "
              f"caller is still deaf when the answerer starts ANSam. That is "
              f"the Session 182 failure: V.8 gets one packet of tone before it "
              f"evaluates and any one-way delay decides the call.")
    print(f"[loopback] setup-gap={args.setup_gap_ms:.0f}ms "
          f"({'answerer joins the bearer late, as on a real call' if args.setup_gap_ms else 'both ends start together -- pre-182 behaviour'})")
    print(f"[loopback] realtime={'on' if args.realtime else 'off'} "
          f"(wall-clock pacing {'keeps V.8 in sync' if args.realtime else 'disabled; answerer will race ahead'})")
    if args.at:
        print(f"[loopback] AT terminals on both ends (ring {args.ring_seconds}s); "
              f"attach after startup and watch the logs for the PTY paths")
    print(f"[loopback] captures in {args.capture_dir}")

    answerer_native_mips = (args.native_mips if args.answerer_native_mips is None
                            else args.answerer_native_mips)
    caller_native_mips = (args.native_mips if args.caller_native_mips is None
                          else args.caller_native_mips)
    if answerer_native_mips and args.answerer_kernel_dispatch:
        raise SystemExit("--answerer-kernel-dispatch and the native MIPS tower "
                         "are two different backends; pick one")
    if caller_native_mips and args.caller_kernel_dispatch:
        raise SystemExit("--caller-kernel-dispatch and the native MIPS tower "
                         "are two different backends; pick one")

    def backend_name(native: bool, dispatch: bool) -> str:
        if native:
            return "native-mips"
        return "kernel-dispatch" if dispatch else "direct"

    print(f"[loopback] backend: answerer="
          f"{backend_name(answerer_native_mips, args.answerer_kernel_dispatch)}"
          f", caller="
          f"{backend_name(caller_native_mips, args.caller_kernel_dispatch)}")
    answerer_env = end_environment(environment, args.answerer_modulation,
                                   answerer_firmware_set, "answerer",
                                   args.answerer_env)
    answerer_cmd = build_command(
        args, role="answer", firmware_set=answerer_firmware_set,
        native_mips=answerer_native_mips,
        kernel_dispatch=args.answerer_kernel_dispatch,
        db_word=args.answerer_db_word,
        preboot=args.answerer_preboot,
        sip_port=answerer_sip, rtp_port=answerer_rtp,
        prefix=args.capture_dir / "answerer", dial=None)
    caller_env = end_environment(dict(environment, EICON_MODEM_ROLE="calling"),
                                 args.caller_modulation, caller_firmware_set,
                                 "caller", args.caller_env)
    caller_cmd = build_command(
        args, role="calling", firmware_set=caller_firmware_set,
        native_mips=caller_native_mips,
        kernel_dispatch=args.caller_kernel_dispatch,
        db_word=args.caller_db_word,
        sip_port=caller_sip, rtp_port=caller_rtp,
        prefix=args.capture_dir / "caller",
        dial=(args.number, answerer_sip))

    logs = {}
    processes = {}
    try:
        for name, command, env in (("answerer", answerer_cmd, answerer_env),
                                   ("caller", caller_cmd, caller_env)):
            log_path = args.capture_dir / f"{name}.endpoint.log"
            logs[name] = log_path.open("w", buffering=1)
            processes[name] = subprocess.Popen(
                command, stdout=logs[name], stderr=subprocess.STDOUT, env=env)
            print(f"[loopback] {name} pid {processes[name].pid} -> {log_path}")
            if name == "answerer":
                # The answerer must be listening before the caller's dial
                # timer fires, and with --native-mips it spends several
                # seconds booting firmware before it reaches its select loop.
                time.sleep(8.0 if args.native_mips else 1.5)

        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            for name, process in processes.items():
                if process.poll() is not None:
                    print(f"[loopback] {name} exited early "
                          f"({process.returncode}); stopping")
                    deadline = 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[loopback] interrupted")
    finally:
        for name, process in processes.items():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for name, process in processes.items():
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                print(f"[loopback] {name} did not stop; killing")
                process.kill()
        for handle in logs.values():
            handle.close()

    print()
    for name in ("caller", "answerer"):
        path = args.capture_dir / f"{name}.endpoint.log"
        print(f"=== {name} ===")
        summarize(path)
    print(f"\nBoth logs in full: {args.capture_dir}/{{caller,answerer}}"
          ".endpoint.log")
    return 0


def summarize(path: Path) -> None:
    """Print the lines that say whether the two ends got anywhere.

    TrnProgress is the training state machine and is the thing to read first;
    a run where both sides stop at the same state failed in the handshake,
    and one where they stop at different states failed asymmetrically, which
    is the more informative case and the reason both ends are captured.
    """
    if not path.exists():
        print("  (no log)")
        return
    interesting = []
    last_progress = None
    for line in path.read_text(errors="replace").splitlines():
        if ("[sip]" in line or "[call]" in line or "[v42]" in line
                or "modulation role" in line or "media fault" in line
                or "[analog-line]" in line
                or "[at]" in line or "ringing" in line or "ring cadence" in line
                or "v42-pty" in line):
            interesting.append(line)
        if "TrnProgress" in line:
            last_progress = line
    for line in interesting[:12]:
        print("  " + line)
    if last_progress:
        print("  last: " + last_progress.strip())


if __name__ == "__main__":
    sys.exit(main())
