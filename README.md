# eicon-adsp-emu

An ADSP-2181 emulator and a MIPS firmware harness for the **Eicon Diva Server
PRI** card, used to make the card's own V.90 digital-modem firmware run — and be
observable — without the card.

Split out of [`v90modem`](../v90modem) because the goals diverge. v90modem is a
software V.90 digital-side modem: it implements the spec. This project runs
someone else's shipped implementation under emulation and reverse-engineers what
it does. The two share no code — the split moved files, it did not untangle
them.

## What is here

- `tools/adsp2181emu/` — the ADSP-2181 core in C (based on MAME's ADSP-21xx
  core, BSD-3-Clause), built as `libadsp2181.dylib`, plus a standalone
  disassembler under `dasm/`. Provides DM/PM write watches, execution watches
  and per-address execution coverage, which is how nearly every finding in the
  analysis doc was established.
- `tools/eicon_mips_shim.py` — runs the card's real MIPS firmware under Unicorn
  and drives the ADSP through it. `create_native_mips_modem()` is the harness
  that reproduces the live card on V.90 page 14.
- `tools/eicon_adsp_sip.py` — answers a real SIP call and puts the emulated card
  on the line, so an analogue modem can dial it. G.711 passthrough only.
- `tools/eicon_loopback.py` — runs two endpoints on loopback and calls one from
  the other, so a handshake can be traced from both ends without hardware.
- `tools/eicon_idi.py` — the IDI payloads (CAI, LLI/LLC/DLC) and the entity/call
  state machine, ported from divas4linux's `putcai()` and `atPlusMS()` rather
  than hand-built. `tools/eicon_at.py` — the AT command set `/dev/ttyds*`
  presents, on top of it. Both are pure Python with no emulator dependency, and
  are covered by `tests/test_eicon_idi.py` and `tests/test_eicon_at.py`.
- `tools/ppp.py` — a dial-in PPP server (framing, LCP, PAP/CHAP, IPCP) for the
  far side of the V.42 link, with `tools/ppp_serve.py` to run it on a PTY or a
  socket with no emulator underneath. `tools/usernet.py` is a userspace NAT
  that puts its clients on the network with no root, and `tools/tun.py` a
  kernel tun for what that cannot carry. Pure Python, covered by
  `tests/test_ppp.py` and `tests/test_usernet.py`.
- `tools/v90_dpcm_*.py`, `tools/eicon_*_replay.py` — offline replay of recorded
  line audio through the data pump, plus the state/vector tracers.
- `tools/dial_*.py` — the DIAL/TIKRNL dispatch investigation harnesses.
- `docs/eicon_adsp_firmware_analysis.md` — the running log, 78 sessions. Read
  the relevant session before changing anything; it records what has already
  been disproved, which is most of the value in this repo.
- `docs/firmware/` — the card's firmware images. Required inputs, tracked.

## Build and run

The top-level makefile does not exist here; the emulator has its own:

```bash
make -C tools/adsp2181emu
```

`libadsp2181.dylib` is gitignored, so build it before trusting any replay.

The Python harnesses need `unicorn`, which is why they run under a separate venv
rather than the system interpreter:

```bash
/tmp/eicon-venv/bin/python tools/v90_dpcm_vector_trace.py \
  artifacts/eicon-native-tower/run34.rx.ulaw --to 17.0 --refill-audit
```

A live call, answering as extension 6001:

```bash
/tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py \
  --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs \
  --trace-v90d-state --law pcmu --capture-prefix artifacts/eicon-native-tower/runNN \
  --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --registrar asterisk.example --username 6001 --password 6001
```

### Profiles

That command is the same every time bar the capture prefix, so it has a name.
`profiles.toml` records the combinations that already travel together, and
`./run` expands one:

```bash
./run native-tower --run 35
```

`./run --list` shows what is defined. Anything after the profile name is passed
through and *overrides* a same-named flag rather than repeating it, so
`./run native-tower --run 35 --law pcma` sends `--law pcma` only. `-e KEY=VAL`
sets one of the `EICON_*` variables for the run; profiles can carry them too,
which is how `v34-live` pins `EICON_MODULATION`.

The resolved command — environment included — is printed to stderr before it
runs, and `./run -n <profile>` prints it without running anything. That output
is the line to paste into a session entry in the analysis doc: profiles are a
shorthand for typing, not a substitute for recording what was run.

Registrar host and credentials come from `[vars]`. Override them, or point
`python` somewhere other than `/tmp/eicon-venv`, in `profiles.local.toml`,
which is gitignored and overlaid a table at a time.

### The terminal before the call

`--v42-pty` allocates the terminal at startup and prints its path, and with
`--at` the command set is answered from that moment — `./run at`, then attach
with `screen` and type. The endpoint services the terminal whenever no call is
up, so `ATS0=0`, `AT+MS=` and the S-registers can be set *before* the INVITE
lands, which is when they have to be set: `+IE` reaches the CAI of the next
call, not the one in progress.

`--preboot` boots a card at startup rather than inside the answer path, and
keeps one booted between calls. Nothing clocks it while it waits — the ADSP
only advances on the sample clock — so the emulated timeline is unchanged and
only the wall-clock cost moves, off the INVITE-to-first-tick path. Each call
still consumes its card and the next is booted fresh, so no firmware state
crosses a call boundary and per-call boots stay comparable.

For the experimental V.42 endpoint, replace `--tx-prbs` with `--tx-v42`.
While the DSP has not published a negotiated data rate, this path normally
uses the legacy PRBS training fill. This is disabled by default so a real
modem does not receive random-looking host-generated bits; it uses mark fill
until the rate is known. Set `EICON_V42_TRAINING_PRBS=1` to enable PRBS for
training tests. It supplies HDLC flags during idle,
decodes the upstream synchronous mailbox,
answers XID and SABME, acknowledges received I frames, and transmits its own:
`send()` segments a byte stream into N401-sized I frames, tracks V(S)/V(A)
against the window, honours incoming N(R), stops on RNR and goes back N on REJ.
It still does not implement V.42bis. XID negotiates the V.42 N401 and window
parameters; the local defaults are k=15 and N401=128. The optional-functions
mask carries the six bit positions Table 11a/V.42 requires of every XID
transmitter (`0x0000898A`) and none of the four optional procedures of
clause 10, which are unimplemented. Frames are addressed per Table 6/V.42 —
the C/R bit depends on the direction and on which end originated the call, so
commands and responses do not share an address octet.

Add `--v42-pty` to put a terminal on the link. It allocates a pseudo-terminal
and prints the path, so a session can be attached before the call lands:

```text
[v42-pty] terminal ready on /dev/ttys012 -- attach with: screen /dev/ttys012
```

Anything typed becomes I frames; acknowledged payload is written back. The PTY
carries no line speed, parity or flow control -- those belong to a real modem's
UART, and this link starts at the synchronous V.42 boundary. `stty` will appear
to work and change nothing. LAPM's window is the only buffer, so when it fills,
reads stop and the terminal blocks, which is the intended back-pressure.

Retransmission is counted in data-pump service calls rather than seconds,
because the bit pipe has no wall clock and the harness can run far from real
time; a stalled window is probed with RR(P) before anything is resent.

The V.42 detection phase (7.2.1) is implemented for both roles: the answerer
sends mark until four DC1s of alternating parity arrive, then sends the
"V.42 supported" ADP ten times; the originator sends ODP until it sees two
adjacent ADPs. Both then enter protocol establishment. Without this exchange
an originator may fall back to no error control -- a Courier reports `Protocol NONE` and both directions become
garbage (Session 86). `EICON_V42_DETECT=0` restores the old behaviour.

### PPP over the V.42 link

`tools/ppp.py` is a dial-in PPP server: RFC 1662 framing, LCP, PAP and CHAP,
and IPCP address assignment. It is a peer rather than a server only — RFC 1661
negotiation is symmetric, so the client half costs almost nothing and is what
makes the whole thing testable without hardware. No dependencies and no I/O:
`feed()`, `tick()`, `take()`, and the caller owns the clock.

Callers are assigned addresses out of **RFC 6598 shared space,
`100.64.0.0/10`**, with this end on `100.64.0.1`. That range rather than
RFC 1918 because a dialled-in host is very likely already on `10/8` or
`192.168/16`: an address that collides with its own LAN costs it that LAN, and
the failure looks like a modem fault rather than an addressing one. `AddressPool`
hands out the next free address per call and takes it back when the call ends,
so the second caller of a run is never given the first one's address; the
server's own address is reserved so it can never be issued to a client. The
cursor moves on rather than reissuing immediately, so a client that reconnects
gets a fresh address instead of one its own stack may still have cached.

### Reaching the network

By default a client gets a real network through **`tools/usernet.py`, a
userspace NAT** — no root, no tun, nothing system-wide touched. Client flows
are *terminated* here and re-originated as ordinary host sockets: TCP to
anywhere, UDP (so DNS), and ICMP echo through the unprivileged datagram
socket. This is what `slirp` was written for in the early nineties — real IP
for dial-up users over a plain shell account — and for a modem emulator it is
arguably the more faithful design than a kernel tun.

Its TCP is deliberately modest: no SACK, no timestamps, no window scaling, no
congestion control. It can afford to be, because underneath is a V.42 link
that is already reliable and in-order, so segments never arrive out of order
and are lost only if the client drops them. Flow control *is* real, driven
from the socket buffers at both ends.

What it cannot do, because no kernel path carries it: traceroute's TTL
behaviour, GRE, IPsec, raw sockets on the client. Connections are outbound
only — nothing on the host network can initiate one *to* a client.

`--no-network` (`--ppp-no-network`) turns it off, and **IP terminates in this
process** instead: datagrams land in `rx_ip` and an ICMP responder answers
pings to the server address. That is the cheapest proof that framing,
negotiation and the data path all work at once, and it is how you tell a link
problem from a network one.

> **Security.** A client reaches whatever this host can reach, including its
> LAN and its loopback services. The link is authenticated (CHAP by default)
> and this is a lab tool — don't point it at untrusted callers and expect a
> boundary.

### The kernel tun, for what the NAT cannot do

`--tun` (`--ppp-tun`) hands datagrams to the kernel instead, via
`tools/tun.py`. Choose it when you need every protocol, or need the host to
reach the client. Two things have to be true for it to work, and only the
first is the tun's job:

1. **Packets reach the kernel and come back.** The device, its addresses, and a
   route covering the pool. `TunDevice` does all of it and undoes it on close,
   so a run that ends does not leave a dead interface and a route to nowhere.
2. **The kernel forwards and translates them.** IP forwarding and NAT, which
   are system-wide. `--nat` does it, prints every command first, and reverts on
   exit. It is deliberately not implied by `--tun`.

```bash
sudo python3 tools/ppp_serve.py --tun --nat --auth chap
```

Root is required — creating the device, `ifconfig` and the route all need it —
which is why the userspace NAT is the default and the rest of the harness runs
unprivileged. With either network attached the ICMP responder stands down: the
host answers its own pings, and a responder in the process would reply first
and shadow it.

The whole pool is routed to the interface rather than relying on the
point-to-point peer address, because one interface cannot have a peer per
client; the peer address is only there to satisfy `ifconfig`. The interface
MTU is set from the peer's negotiated MRU once IPCP is up, which is what makes
the kernel fragment or signal PMTU rather than handing down packets the client
would have to drop — and it clamps, so one small-MRU client cannot ratchet the
interface down for the callers after it.

macOS and Linux reach the same object very differently: macOS has no
`/dev/net/tun`, a utun is a `PF_SYSTEM` socket and every packet carries a
4-byte address-family header that Linux's `IFF_NO_PI` does not. Both are
implemented; **only the macOS path has been exercised**, and even there the
privileged half is untested — see the caveat below.

The quickest thing to point a client at needs no firmware at all:

```bash
python3 tools/ppp_serve.py --auth chap --user ppp --password ppp
```

It prints a PTY path (`--tcp PORT` serves a socket instead). Aim a client at
it — with the system `pppd` as the *client*, `sudo pppd /dev/ttysNNN 115200
noauth nodetach user ppp` — and once IPCP is up, `ping 100.64.0.1` is answered.
A failure here is a PPP failure; a failure over `--ppp` below but not here is a
data-path failure, and keeping those apart is the point of the standalone
server.

On the SIP endpoint, `--ppp` puts the same server on the V.42 link (requires
`--tx-v42`, and conflicts with `--v42-pty`, which claims the same link).
`--ppp-auth`, `--ppp-user`, `--ppp-password`, `--ppp-local`, `--ppp-pool` and
`--ppp-dns` configure it; `--ppp-peer` pins one address for every caller
instead of allocating, and `--ppp-client` takes the calling half. The loopback
runs both ends:

```bash
tools/eicon_loopback.py --native-mips --ppp --ppp-auth chap
```

The answerer is the server and the caller is the client, so the negotiation
crosses the emulated data pump rather than happening between two peers wired
together in one process.

**This does not connect yet**, for a reason that has nothing to do with PPP.
A 90-second loopback call places and answers the INVITE, brings the B-channel
up and trains both DSPs, and then stalls where Session 147 says it does —
caller deepest `TrnProgress 0x0060`, answerer `0x0090`, matching that session's
table exactly. No V.42 link means no byte stream, so the PPP peer is never
even constructed. The NAT is created and torn down cleanly around it and
reports `opened=0 in=0 out=0`, which confirms the wiring is live on a real
call and had nothing to carry. What *is* covered is everything between PPP and
the pump —
`tests/test_ppp.py` runs the same `LapmPppLink` glue over two real
`LapmEndpoint`s back to back, including a ping round trip and the window
back-pressure, which is the live path bar the bits on the line.

The userspace NAT is verified against real sockets rather than mocks, because
its whole claim is that client flows become ordinary host sockets: a loopback
TCP server, a loopback UDP echo, and a ping to 127.0.0.1, plus a live HTTP GET
driven end to end over a PPP link (`ppp_serve.py` → CHAP → IPCP → NAT → a real
web server) and a DNS query answered by the system resolver. The endpoint's own
assembly — `LapmPppLink` + `UserNetwork` over two real `LapmEndpoint`s — fetches
over TCP in `tests/test_ppp.py`, which is that stack minus only the bits on the
line. The client side of
those tests is hand-built segments rather than a second TCP stack, which is the
only way to assert exact sequence numbers and construct the awkward cases — a
zero window, a stray RST, a FIN riding on truncated data.

**The tun's privileged half has never been run.** Creating a utun needs root,
which was not available when it was written, so what is verified is: the
`CTLIOCGINFO` ioctl and its struct packing (the kernel resolves the utun
control and refuses only at `connect`, with EPERM), the AF_INET header
constant, and the whole `TunBridge` path against a fake device — both
directions, the MTU tracking, the refusal path and the ICMP responder standing
down. What is *not* verified is `connect()` on the control socket, the
`ifconfig`/`route` invocations, and every NAT command. Run it under `sudo`
once before trusting it.

Note that `modem_nl_assign_payload()` sets `DLC_MODEMPROT_DISABLE_V42_V42BIS`,
so the card's own V.42 is switched off and this Python is the V.42 entity. Using
the firmware's implementation instead has never been tried; Session 86 sketches
what it would take.

Rebuild the disassembler (only needed off arm64):

```bash
c++ -O2 -std=c++17 -o tools/adsp2181emu/dasm/dasm \
    tools/adsp2181emu/dasm/dasm_main.cpp tools/adsp2181emu/dasm/2100dasm.c
```

It decodes ALU/MAC and control flow correctly but mislabels the direct DM
read/write opcodes, and on some overlay pages it mis-decodes wholesale — the
watchpoints are the ground truth, not the disassembly.

## Gotchas

- **Two replay harnesses disagree past the INFO page.** `eicon_info_replay.py`
  uses `LiveKernelModem`; live captures and `v90_dpcm_replay.py` use
  `create_native_mips_modem()`. Only the native one reproduces the live card on
  page 14. Session 50 records what mixing them up costs.
- **Two page-14 diagnostics are default-on** in `eicon_mips_shim.py` and both
  change what the card puts on the line. `EICON_V90D_TX_BLOCK_HOLD=0` restores
  the resident kernel's per-frame clear of the mapping-frame block
  `DM(0x3fa7..0x3fac)`, which drops five of every six downstream samples;
  `EICON_V90D_BULK_ADAPTER=1` keeps the `0x1900..0x19c8` echo bulk-delay adapter
  live, in which case the outer state machine stops at `0x0068` and the card
  transmits nothing. That adapter is the card's echo canceller, so this is a real
  functional gap rather than a tidy diagnostic, and it cannot simply be switched
  back on: Session 88 has the three failure modes and the reason Session 65's
  `DM(0x3fb3)` finding no longer reproduces.
- **Never infer generator activity from block contents.** A constant block is a
  legitimate signal — Phase 4 opens with Ri on a single PCM codeword (V.90
  §9.4.1.1) — so "constant" does not mean "stale". Count executions of the
  generator dispatch at PM `0x2a52` instead. Session 68 records the audit that
  got this wrong.
- **The media thread has 20 ms and spends 3.9 of them.** It was 11 ms until
  Session 81: a rangeless `UC_HOOK_CODE` made every MIPS instruction a Python
  callback, which was 8.5 ms of the tick, and the trace it appended to grew to
  813 MB over a 20 s call. `_step_mips` is now 1.9 ms and the ADSP 2.0 ms.
  Diagnostics are 0.5 ms, so logging is still not what makes a call flaky; what
  does is losing wall time, so watch the `[media]` line for substituted RX
  samples, discards and clock holds. `--mips-interval 320` is still there if you
  need more headroom, and the Session 70 pacing defaults are now conservative.
- **Offline replay cannot see a missing capability, and reaching page 14 in
  replay proves nothing.** `v90_dpcm_replay.py` is open loop: the recorded RX
  already holds a V.90-accepting answer whatever the card offered. Session 82
  used that to argue `V8_SETUP` (write DB `+0x04`) had broken V.90 and was wrong
  — hardware connects V.90 with it at `0x0000`. `EICON_WDB_OVERRIDE=0x04:0x6000`
  remains as an A/B for the still-unexplained documented-vs-native capability
  gap, not as a fix. If the question is what the card *advertises*, only a call
  answers it.
- **A media-path exception no longer kills the endpoint.** It used to propagate
  out of `run()` and exit the process, so a firmware fault and the peer hanging
  up produced identical logs — `[capture] wrote` with no `[call] ended` above it
  is the tell, and `call10-force-v34-cai.endpoint.log` is the example. `run()`
  now reports the overlay, bootpage and TrnProgress at the fault and keeps
  listening. Session 83.
- **`EICON_MIPS_WARMUP` shifts the timeline by a sample.** Three idle supervisor
  passes run at attachment so Unicorn translates the media-phase mainloop before
  the sample clock starts; without them the first in-call tick costs 93 ms
  offline and 390 ms live. It is the one part of Session 81 that is not
  behaviour-preserving. Set `EICON_MIPS_WARMUP=0` when diffing a replay against
  a recorded capture.
- **A capability the card "does not support" may just be a download you did not
  stage.** The protocol image decides what a channel can do by searching the DSP
  download table this harness builds, so a shipping file set that omits an
  overlay reads as a missing feature. V.90A is the case: the PRI file set has no
  V.90 APCM overlay, and `EICON_DSP_EXTRA_DOWNLOADS=0x026b` supplies it, after
  which `EICON_MODULATION=v90a` gets the supported branch instead of the
  firmware's "V.90A not supported" trace. Session 134.
- **Never transcode the G.711 stream.** The RTP payload *is* the DS0 PCM stream
  the far-end converter sees. No resampling, VAD/CNG, comfort noise, echo
  cancellation or gain anywhere in the audio path.
- `artifacts/` is untracked and large; a single hardware session runs to
  hundreds of megabytes.

## Still in v90modem

Two files this workflow uses were deliberately left there, because v90modem
depends on them:

- `tools/cx_at.py` — Courier/USR AT diagnostics and dialling, referenced by
  `docs/v90_hardware_interop.md`. Courier calls against this emulator are placed
  with it: `./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 dial 6001 --wait 120 --pre 'AT&M0'`
- `docs/courier_firmware_analysis.md` — peer-modem analysis serving both
  projects, and cited from v90modem's status notes.

## Where things stand

**Start with [`docs/handoff.md`](docs/handoff.md).** It is the current picture:
the three live blockers, the full echo-canceller trace, an explicit list of what
has already been disproved, reproduction commands and the ranked next steps.
`docs/eicon_adsp_firmware_analysis.md` is the chronological record of how each
finding was established, and is the place to look once the handoff points you at a
session.

In short, as of Session 93: the card reaches full V.90 data mode and has walked
the whole state machine to `0x00d0` at 38666/24000 with DCD and CTS asserted. Three
blockers are open — V.34 does not connect at all, V.90 needs
`--native-bearer-activation` for reasons unknown, and DIL is a lottery whose
leading suspect is the card's echo canceller, which this harness disables because
enabling it corrupts the V90D record table. A LAPM transmitter and PTY terminal
exist and are unit-tested. Against hardware the receive path now demodulates,
frames and passes FCS, but establishment does not complete: the peer
retransmits XID and no SABME has ever arrived. See `docs/handoff.md` for the
fixes waiting on the next live call.
