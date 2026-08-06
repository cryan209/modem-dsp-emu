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

    tools/eicon_loopback.py --native-mips \
        --answerer-modulation v90 --caller-modulation v90a

`--modulation` still sets both ends; the per-end options override it, and
`EICON_DSP_EXTRA_DOWNLOADS=0x026b` is added to the V.90A end's environment
automatically, because without the overlay that end's firmware answers "V.90A
not supported" and the run means nothing.  This is still not proof of
interoperability -- see above -- but it is the first configuration here with
the card's own firmware on both sides of a V.90 link.
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


def build_command(args, *, role: str, sip_port: int, rtp_port: int,
                  prefix: Path, dial: "tuple[str, int] | None") -> list[str]:
    python = str(args.python)
    command = [python, "-u", str(TOOLS / "eicon_adsp_sip.py"),
               "--bind", "127.0.0.1", "--advertise", "127.0.0.1",
               "--sip-port", str(sip_port), "--rtp-port", str(rtp_port),
               "--law", args.law, "--modem-role", role,
               "--capture-prefix", str(prefix)]
    if args.native_mips:
        command += ["--native-mips",
                    "--mips-kernel", str(args.mips_kernel),
                    "--mips-tikrnl", str(args.mips_tikrnl)]
        if args.native_bearer_activation:
            command.append("--native-bearer-activation")
        if args.force_info_after_v8:
            command.append("--force-info-after-v8")
        if args.tx_prbs:
            command.append("--tx-prbs")
    if args.trace_v90d_state:
        command.append("--trace-v90d-state")
    if args.watch_exec:
        command += ["--watch-exec", args.watch_exec]
    if args.watch_dm:
        command += ["--watch-dm", args.watch_dm]
    if args.at:
        command += ["--v42-pty", "--at",
                   "--ring-seconds", str(args.ring_seconds)]
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
    ap.add_argument("--watch-exec", default="",
                    help="comma-separated PM addresses to exec-watch on both "
                         "ends (forwarded to eicon_adsp_sip.py --watch-exec)")
    ap.add_argument("--watch-dm", default="",
                    help="comma-separated DM addresses to write-watch on both "
                         "ends (forwarded to eicon_adsp_sip.py --watch-dm)")
    ap.add_argument("--at", action="store_true",
                    help="put an AT command terminal on both ends so ATD "
                         "places the call and the answerer presents RING then "
                         "CONNECT; without this the caller auto-dials via "
                         "--dial and the answerer auto-answers silently")
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

    if not Path(args.python).exists():
        ap.error(f"{args.python} does not exist; the harnesses need the venv "
                 "that has unicorn")
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
                        label: str) -> "dict[str, str]":
        """One end's environment, with the V.90A prerequisite attached.

        Asking for V.90A without staging the APCM overlay is not a weaker
        version of this test, it is a different one: the firmware answers
        "V.90A not supported" and the end negotiates as though the option had
        never been named (Session 134).  Adding the download here means the
        two-sided V.90 configuration cannot be run in the form that silently
        does not test it.
        """
        if modulation is None:
            return base
        end = dict(base)
        end["EICON_MODULATION"] = modulation
        if modulation.split(",")[0].strip().lower() == "v90a":
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
    print(f"[loopback] realtime={'on' if args.realtime else 'off'} "
          f"(wall-clock pacing {'keeps V.8 in sync' if args.realtime else 'disabled; answerer will race ahead'})")
    if args.at:
        print(f"[loopback] AT terminals on both ends (ring {args.ring_seconds}s); "
              f"attach after startup and watch the logs for the PTY paths")
    print(f"[loopback] captures in {args.capture_dir}")

    answerer_env = end_environment(environment, args.answerer_modulation,
                                   "answerer")
    answerer_cmd = build_command(
        args, role="answer", sip_port=answerer_sip, rtp_port=answerer_rtp,
        prefix=args.capture_dir / "answerer", dial=None)
    caller_env = end_environment(dict(environment, EICON_MODEM_ROLE="calling"),
                                 args.caller_modulation, "caller")
    caller_cmd = build_command(
        args, role="calling", sip_port=caller_sip, rtp_port=caller_rtp,
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
