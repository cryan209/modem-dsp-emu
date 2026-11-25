#!/usr/bin/env python3
"""Destructive-to-calls, bounded two-TTY BRI link/data test; run on the card host.

Stop services owning the selected TTYs first. Closing both TTYs hangs up.
Controller numbers are Diva logical adapters, not physical card numbers.
"""
import argparse
import hashlib
import json
import os
import select
import termios
import time


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL | termios.CRTSCTS
    a[4] = a[5] = termios.B115200
    a[6][termios.VMIN] = a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def read(fd):
    try:
        return os.read(fd, 65536)
    except BlockingIOError:
        return b""


def send(fd, data, timeout=5):
    end = time.monotonic() + timeout
    while data:
        if time.monotonic() >= end:
            raise TimeoutError("write timeout")
        if select.select([], [fd], [], .1)[1]:
            try:
                data = data[os.write(fd, data):]
            except BlockingIOError:
                pass


def command(fd, text):
    send(fd, (text + "\r").encode())
    result = bytearray()
    end = time.monotonic() + 4
    while time.monotonic() < end:
        if select.select([fd], [], [], .1)[0]:
            result.extend(read(fd))
            if b"ERROR" in result:
                raise RuntimeError(f"{text}: {result!r}")
            if b"OK" in result:
                return bytes(result).decode(errors="replace").strip()
    raise TimeoutError(f"{text}: {result!r}")


def transfer(src, dst, size, seed):
    payload = hashlib.shake_256(seed).digest(size)
    sent = 0
    received = bytearray()
    start = time.monotonic()
    end = start + max(15, size / 1000)
    while time.monotonic() < end and len(received) < size:
        ready, writable, _ = select.select([dst], [src] if sent < size else [], [], .1)
        if src in writable:
            try:
                sent += os.write(src, payload[sent:sent + 1024])
            except BlockingIOError:
                pass
        if dst in ready:
            received.extend(read(dst))
    result = {"sent": sent, "received": len(received), "seconds": round(time.monotonic()-start, 3),
              "sha256": hashlib.sha256(received).hexdigest(), "ok": bytes(received) == payload}
    if not result["ok"]:
        result["first_bytes"] = received[:80].hex()
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("caller", type=int)
    p.add_argument("answerer", type=int)
    p.add_argument("--caller-tty", default="/dev/ttyds5")
    p.add_argument("--answerer-tty", default="/dev/ttyds6")
    p.add_argument("--profile", type=int, default=1)
    p.add_argument("--bytes", type=int, default=16384)
    p.add_argument("--number", default="9876")
    args = p.parse_args()
    if args.caller_tty == args.answerer_tty:
        p.error("caller and answerer must use different TTYs")
    if args.bytes <= 0 or args.bytes > 1048576:
        p.error("--bytes must be between 1 and 1048576")
    if not args.number.isascii() or not args.number.isdecimal():
        p.error("--number must contain only ASCII digits")
    fds = []
    try:
        for path, controller in ((args.caller_tty, args.caller), (args.answerer_tty, args.answerer)):
            fd = open_port(path)
            fds.append(fd)
            for text in (f"AT&F{args.profile}", "ATE0V1", f"AT+iQ=a{controller}", "AT+iA" + args.number, "ATS0=1"):
                print(path, text, command(fd, text), flush=True)
        caller, answerer = fds
        send(caller, ("ATD" + args.number + "\r").encode())
        buffers = {fd: bytearray() for fd in fds}
        connected = set()
        end = time.monotonic() + 25
        while time.monotonic() < end and len(connected) < 2:
            for fd in select.select(fds, [], [], .2)[0]:
                data = read(fd)
                buffers[fd].extend(data)
                print("caller" if fd == caller else "answerer", repr(data), flush=True)
                if b"CONNECT" in buffers[fd]:
                    connected.add(fd)
                if any(x in buffers[fd] for x in (b"NO CARRIER", b"NO ANSWER", b"BUSY", b"ERROR", b"NO DIALTONE")):
                    raise RuntimeError("call failed: " + repr(buffers))
        if len(connected) != 2:
            raise TimeoutError("call setup: " + repr(buffers))
        time.sleep(.3)
        for fd in fds:
            termios.tcflush(fd, termios.TCIFLUSH)
        results = {"caller": args.caller, "answerer": args.answerer, "profile": args.profile}
        results["forward"] = transfer(caller, answerer, args.bytes, b"BRI forward")
        results["reverse"] = transfer(answerer, caller, args.bytes, b"BRI reverse")
        print(json.dumps(results), flush=True)
        if not all(results[k]["ok"] for k in ("forward", "reverse")):
            raise RuntimeError("data mismatch")
    finally:
        for fd in fds:
            os.close(fd)


if __name__ == "__main__":
    main()
