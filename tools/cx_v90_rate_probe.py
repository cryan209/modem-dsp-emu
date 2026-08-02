#!/usr/bin/env python3
"""Place one rate-capped Conexant V.90 hardware call.

The six numeric +MS fields are named from the analogue modem's point of view:
TX is V.34 upstream and RX is PCM downstream.  The emulator should be running
on the dialled extension and will report the complementary ADDSP read-database
rates from its digital-modem point of view.
"""

from __future__ import annotations

import argparse
import os
import select
import termios
import time


def read_for(fd: int, seconds: float) -> bytes:
    deadline = time.monotonic() + seconds
    data = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = select.select(
            [fd], [], [], min(0.1, deadline - time.monotonic()))
        if ready:
            try:
                data.extend(os.read(fd, 4096))
            except BlockingIOError:
                pass
    return bytes(data)


def command(fd: int, text: str, wait: float = 1.2) -> bytes:
    os.write(fd, text.encode("ascii") + b"\r")
    answer = read_for(fd, wait)
    print(f"{text}\n{answer.decode('ascii', 'backslashreplace').strip()}",
          flush=True)
    return answer


def read_until(fd: int, expected: bytes, seconds: float) -> bytes:
    """Read until *expected* arrives or the deadline expires."""
    deadline = time.monotonic() + seconds
    data = bytearray()
    while time.monotonic() < deadline and expected not in data:
        data.extend(read_for(fd, min(0.25, deadline - time.monotonic())))
    return bytes(data)


def configure_raw_115200(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[4] = attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="/dev/cu.usbmodem123456781")
    ap.add_argument("--number", default="6001")
    ap.add_argument("--automode", type=int, choices=(0, 1), default=1)
    ap.add_argument("--s48", type=int, choices=(0, 128), default=0,
                    help="V.42 negotiation control (128 disables detection)")
    ap.add_argument("--s36", type=int, default=4,
                    help="fallback action used with S48=128")
    ap.add_argument("--s91", type=int,
                    help="PSTN transmit attenuation in dBm (modem S91)")
    ap.add_argument("--upstream-min", type=int, default=300)
    ap.add_argument("--upstream-max", type=int, required=True)
    ap.add_argument("--downstream-min", type=int, default=300)
    ap.add_argument("--downstream-max", type=int, required=True)
    ap.add_argument("--connect-timeout", type=float, default=75)
    ap.add_argument("--hold", type=float, default=12,
                    help="seconds to hold data mode before online diagnostics")
    ap.add_argument("--data-delay", type=float, default=0,
                    help="seconds after CONNECT before sending the payload")
    ap.add_argument("--payload", default="cx-rate-probe\r\n")
    ap.add_argument("--endpoint-pty",
                    help="V.42 PTY printed by eicon_adsp_sip.py; when set, "
                         "prove both payload directions")
    ap.add_argument("--reverse-payload", default="eicon-rate-probe\r\n")
    args = ap.parse_args()

    fd = os.open(args.device,
                 os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    endpoint_fd = None
    try:
        configure_raw_115200(fd)
        termios.tcflush(fd, termios.TCIOFLUSH)
        if args.endpoint_pty:
            endpoint_fd = os.open(
                args.endpoint_pty,
                os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            # The server creates this slave in raw mode. Reassert it in case a
            # prior terminal changed the line discipline between calls.
            endpoint_attrs = termios.tcgetattr(endpoint_fd)
            endpoint_attrs[0] = endpoint_attrs[1] = endpoint_attrs[3] = 0
            endpoint_attrs[2] = (termios.CS8 | termios.CREAD
                                 | termios.CLOCAL)
            endpoint_attrs[6][termios.VMIN] = 0
            endpoint_attrs[6][termios.VTIME] = 1
            termios.tcsetattr(endpoint_fd, termios.TCSANOW, endpoint_attrs)
            termios.tcflush(endpoint_fd, termios.TCIOFLUSH)

        time.sleep(1)
        os.write(fd, b"+++")
        read_for(fd, 1)
        # Keep these separate. Conexant firmware variants differ in their
        # supported result-code and flow-control commands, and one ERROR in a
        # compound string otherwise hides which essential setting was lost.
        setup_commands = ["ATH", "ATZ", "ATX4", "ATW2",
                          f"ATS48={args.s48}", f"ATS36={args.s36}",
                          "ATS46=136", "AT&K0"]
        if args.s91 is not None:
            setup_commands.extend(("ATS91?", f"ATS91={args.s91}",
                                   "ATS91?"))
        for setup in setup_commands:
            command(fd, setup)
        command(fd, "AT+MS=?", 2.0)
        ms = (f"AT+MS=V90,{args.automode},{args.upstream_min},"
              f"{args.upstream_max},{args.downstream_min},"
              f"{args.downstream_max}")
        ms_answer = command(fd, ms)
        if b"OK" not in ms_answer:
            print("rate_request_accepted=False", flush=True)
            return 1
        print("rate_request_accepted=True", flush=True)
        command(fd, "AT+MS?")

        os.write(fd, f"ATDT{args.number}\r".encode("ascii"))
        response = bytearray()
        deadline = time.monotonic() + args.connect_timeout
        while time.monotonic() < deadline and b"CONNECT" not in response:
            response.extend(read_for(fd, 0.25))
        print(response.decode("ascii", "backslashreplace").strip(), flush=True)
        if b"CONNECT" not in response:
            return 2

        if args.data_delay:
            time.sleep(args.data_delay)
        upstream_payload = args.payload.encode("ascii")
        os.write(fd, upstream_payload)
        payload_rc = 0
        if endpoint_fd is None:
            received = read_for(fd, args.hold)
            print(f"data_mode_received={received!r}", flush=True)
        else:
            upstream_received = read_until(
                endpoint_fd, upstream_payload, args.hold)
            upstream_ok = upstream_payload in upstream_received
            print(f"upstream_payload_ok={upstream_ok} "
                  f"endpoint_received={upstream_received!r}", flush=True)
            if not upstream_ok:
                payload_rc = 3
            else:
                reverse_payload = args.reverse_payload.encode("ascii")
                os.write(endpoint_fd, reverse_payload)
                downstream_received = read_until(
                    fd, reverse_payload, args.hold)
                downstream_ok = reverse_payload in downstream_received
                print(f"downstream_payload_ok={downstream_ok} "
                      f"modem_received={downstream_received!r}", flush=True)
                if not downstream_ok:
                    payload_rc = 4

        # Keep the carrier up while asking the modem for its own view of both
        # negotiated directions. The guard times avoid treating +++ as data.
        time.sleep(1)
        os.write(fd, b"+++")
        time.sleep(1)
        read_for(fd, 0.5)
        command(fd, "ATI6", 1.5)
        command(fd, "ATI11", 2.0)
        command(fd, "ATH", 1.5)
        return payload_rc
    finally:
        if endpoint_fd is not None:
            os.close(endpoint_fd)
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
