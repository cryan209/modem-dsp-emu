# V.34 loopback progress — 5 September 2026

The mixed Analog build-109 caller / PRI build-117 answerer now reaches
`TrnProgress=0x00d0` on both V.34 pages (`0x0261`). This is a training
milestone, not a verified stable data connection: both ends retrain after
roughly 3–4 seconds in that state, and no payload transfer has been proved.

## Reproduce

```sh
make -C tools/adsp2181emu
/tmp/eicon-venv/bin/python tools/eicon_loopback.py \
  --seconds 65 \
  --answerer-firmware-set pri117 --answerer-modulation v34,0 \
  --caller-firmware-set analog109 --caller-modulation v34,0 \
  --caller-kernel-dispatch \
  --capture-dir artifacts/v34-loopback
```

The loopback enables `EICON_EXPAND_SPORT=1` for the PRI V.34 answerer,
as it already does for V.90. An explicit `--answerer-env
EICON_EXPAND_SPORT=0` restores the raw-PCMU control. No state-machine pins,
reactive engines or data sidebands are needed for this training result.

## Changes and evidence

`Card.configure_modem()` previously called `norm_l_from_cai()` without a
disabled-modulation mask. Thus the direct backend and its Analog kernel
wrapper ignored `EICON_MODULATION` and advertised the unrestricted `0xa13f`
menu. They now use the same selection parser as the native MIPS shim;
`v34,0` produces `Norm_L=0x0100`. This change applies the modulation menu;
it does not implement the requested min/max rate bounds in the direct backend.

Captures are local and untracked:

| Capture under `artifacts/` | Configuration | Result |
|---|---|---|
| `v34-20260905-baseline` | Before the fix, both ends requested `v34` | Negotiated V.90 pages 14/13 instead |
| `v34-20260905-selection-only` | Fixed menu, `v34,0`, raw PRI receive | Neither end reached `0x00d0` in 45 s |
| `v34-20260905-selected` | Fixed menu, `v34,0`, expanded PRI receive | Both reached `0x00d0` three times in 65 s |
| `v34-20260905-ppp` | Same pairing with `--ppp` | CLI rejected the unsupported data path before starting a call |
| `v34-20260905-default` | Final loopback defaults, no explicit SPORT override | Both again first reached `0x00d0` at 15.460/17.220 s in a 40 s repeat |

The expanded run first reaches `0x00d0` at answerer sample 123680
(15.460 s) and caller sample 137760 (17.220 s). These are local DSP clocks;
the answerer has a 2 s setup gap. The answerer leaves at 18.880 s and caller
at 21.060 s. Two further training cycles also reach `0x00d0`.

During those data-state windows the DATASTATE words report 24000, 26400
and 28800 bit/s across directions and attempts. These are firmware rate
reports, not measured throughput. Both ends report CTS/DSR transitions;
the caller's first `0x00d0` transition does not include DCD.

## Remaining work

The direct Card and Analog kernel wrapper do not service the synchronous
TX/RX mailboxes for V.42. `eicon_adsp_sip.py` therefore rejects `--tx-v42`
unless a native MIPS backend or reactive V.90 engine is selected. Adding
`--ppp` to the command above cannot validate V.34 payload delivery.

The next implementation step is to share the native shim's synchronous
mailbox service with these backends, preserving per-sample service, negotiated
datagram widths, bit order and retrain handling. Then test LAPM establishment,
PPP authentication/IPCP and peer ping replies. The cause of the repeated
retraining is not yet established; absence of a host data source is not proof
that it causes the signal failure. Hardware interoperability remains untested
by this loopback.

Validation: 122 unit tests passed across `test_eicon_idi.py`,
`test_eicon_at.py` and `test_direct_modulation.py`; emulator build and
`git diff --check` passed. The direct configuration test checks both roles,
strict V.34/V.22bis selection, V.34 automode and the unchanged default menu.
