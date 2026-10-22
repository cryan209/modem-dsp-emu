#!/usr/bin/env python3
"""Run an eicon_dsp_bench bundle on a stopped Diva 4BRI-8 PCI v2.

Standard library only: this runs on the card host.  The card must already be
stopped (`divactrl load -c N -StopCard`), which leaves its MIPS in reset; this
program refuses to touch a card whose MIPS is running.

    sudo python3 eicon_dsp_bench_card.py --pci 0000:02:02.0 --adapter 5 \
        --serial 12196 alu.bundle --out alu.card.json

It writes the bundle to card RAM (BAR2) at the reset vector, starts the MIPS
the way the driver does (start_qBri_hardware: cold, then warm|cold on
MQ2_BREG_RISC), polls the stub's status block, copies the results out, and
puts both the MIPS and the DSPs back into reset.  Restart the card with the
driver afterwards.

Do not poll BAR2 across driver initialisation: that hard-locked this host on
2026-08-12 (docs/4bri_v1_firmware_replay.md).  This program only reads BAR2
of a card the driver has finished stopping.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import mmap
import os
import re
import struct
import sys
import time
from pathlib import Path

VENDOR, DEVICE = 0x1133, 0xE013
RISC = 0x0200                 # MQ2_BREG_RISC
COLD, WARM = 0x0001, 0x0002   # MQ_RISC_COLD_RESET_MASK, MQ_RISC_WARM_RESET_MASK
ISAC_DSP_RESET = 0x0000       # MQ2_ISAC_DSP_RESET


def u32(mm: mmap.mmap, offset: int) -> ctypes.c_uint32:
    """A single aligned 32-bit access, not a byte-wise memcpy."""
    return ctypes.c_uint32.from_buffer(mm, offset)


def check_adapter(pci: str, adapter: int, serial: int) -> None:
    device = Path("/sys/bus/pci/devices") / pci
    if int((device / "vendor").read_text(), 16) != VENDOR or \
            int((device / "device").read_text(), 16) != DEVICE:
        raise SystemExit(f"{pci} is not a 1133:e013 4BRI v2")
    info = Path(f"/proc/net/eicon/adapter{adapter}/info").read_text()
    if not re.search(rf"^Serial\s*:\s*{serial}\s*$", info, re.M):
        raise SystemExit(f"adapter {adapter} is not serial {serial}")
    devfn = int(pci.split(".")[0].split(":")[-1], 16) << 3 | int(pci.split(".")[1])
    funcs = re.findall(r"^PCI-func\s*:\s*(\d+)", info, re.M)
    if not funcs or int(funcs[0]) != devfn:
        raise SystemExit(f"adapter {adapter} is PCI-func {funcs}, not {pci} (devfn {devfn})")


def map_bar(pci: str, index: int) -> mmap.mmap:
    path = f"/sys/bus/pci/devices/{pci}/resource{index}"
    fd = os.open(path, os.O_RDWR | os.O_SYNC)
    try:
        return mmap.mmap(fd, os.path.getsize(path), mmap.MAP_SHARED,
                         mmap.PROT_READ | mmap.PROT_WRITE)
    finally:
        os.close(fd)


def read_results(ram: mmap.mmap, meta: dict) -> dict:
    status = meta["status"]
    word = lambda off: u32(ram, off).value
    out = {"suite": meta["suite"], "stage": word(status),
           "heartbeat": word(status + 4),
           "finished": word(status + 8) == meta["finished"], "dsps": []}
    for j, port in enumerate(meta["ports"]):
        base = status + 0x10 + 16 * j
        polls, done = word(base + 8), word(base + 12)
        start = meta["results"] + meta["stride"] * j
        raw = bytes(ram[start:start + 2 * meta["count"]])
        out["dsps"].append({
            "port": port, "suite": meta.get("suites", [meta["suite"]] * len(meta["ports"]))[j],
            "probe": [f"{word(base):04x}", f"{word(base + 4):04x}"],
            "polls": None if polls == 0xFFFFFFFF else polls, "done": done & 0xFFFF,
            "dm": [f"{v:04x}" for v in struct.unpack(f"<{meta['count']}H", raw)]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--pci", required=True)
    parser.add_argument("--adapter", type=int, required=True)
    parser.add_argument("--serial", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    meta = json.loads(args.bundle.with_suffix(".meta.json").read_text())
    image = args.bundle.read_bytes()
    check_adapter(args.pci, args.adapter, args.serial)
    ctl, ram = map_bar(args.pci, 3), map_bar(args.pci, 2)
    risc = u32(ctl, RISC)
    if risc.value & (COLD | WARM):
        raise SystemExit(f"MIPS is running (RISC=0x{risc.value:x}); stop the card first")

    results_end = meta["results"] + meta["stride"] * len(meta["ports"])
    ram[meta["results"]:results_end] = bytes(results_end - meta["results"])
    ram[0:len(image)] = image
    if bytes(ram[0:len(image)]) != image:
        raise SystemExit("card RAM did not read back the bundle")
    print(f"{meta['suite']}: wrote {len(image)} bytes, starting MIPS", flush=True)

    risc.value = COLD
    time.sleep(0.002)
    risc.value = COLD | WARM
    started = time.monotonic()
    last = None
    try:
        while time.monotonic() - started < args.timeout:
            time.sleep(0.25)
            stage, beat = u32(ram, meta["status"]).value, u32(ram, meta["status"] + 4).value
            if (stage, beat) != last:
                print(f"  {time.monotonic() - started:5.2f}s stage {stage} heartbeat {beat}",
                      flush=True)
                last = (stage, beat)
            if u32(ram, meta["status"] + 8).value == meta["finished"]:
                break
    finally:
        risc.value = 0
        u32(ctl, ISAC_DSP_RESET).value = 0
    result = read_results(ram, meta)
    result["source"] = f"card {args.serial} ({args.pci})"
    result["seconds"] = round(time.monotonic() - started, 2)
    args.out.write_text(json.dumps(result, indent=1))
    print(f"finished={result['finished']} stage={result['stage']} in {result['seconds']}s")
    for d in result["dsps"]:
        print(f"  {d['port']}: probe {'/'.join(d['probe'])}  polls {d['polls']}  done {d['done']:04x}")
    return 0 if result["finished"] else 1


if __name__ == "__main__":
    sys.exit(main())
