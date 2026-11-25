"""Twenty-four further summaries of the two repositories' recorded work."""
def extend(posts):
    def log(key,group,subject,body,source,parent=None):
        posts.append(dict(key='devlog2-'+key,group=group,author='Modem lab notebook',subject=subject,
                          body=body.strip()+'\n\nSource: '+source+'\n',parent='devlog2-'+parent if parent else None,kind='devlog'))
    g='dev.modem-dsp-emu'
    log('oracle',g,'22 Oct: turning the spare ADSPs into an instruction oracle', '''
The 4BRI card's DSPs are reached through the board MIPS, not the PRI-only
host access path. A small bare-metal MIPS loader follows the stock
protocol's reset and IDMA sequence, installs a test program and reads
results back. This makes single-instruction comparisons possible on the
actual ADSP-2181s rather than treating another emulator as ground truth.
The first run exposed two heavily used instruction behaviours in ours.
''','modem-dsp-emu/docs/dsp_conformance_20261022.md')
    log('satflags',g,'Hardware comparison: saturated ALU results and status flags', '''
One conformance finding concerned AZ/AN with AR saturation enabled.
The emulator derived those flags from the overflowed intermediate sum;
the hardware result establishes the saturated value as the relevant
basis. Testing AR alone would have missed an instruction that returned
the right visible value but left the wrong branch conditions.
The bench records status as well as arithmetic output.
''','modem-dsp-emu/docs/dsp_conformance_20261022.md, AR_SAT flags','oracle')
    log('adc',g,'14 Oct: add-with-carry overflow had a modem-level consequence', '''
The old helpers folded carry into Y before checking signed overflow.
At a sign boundary that could report overflow for a valid signed sum,
and saturation could then turn 0x7fff into 0x8000. Overflow now uses the
original operands while carry-out uses the complete unsigned sum.
With this correction the firmware pair carried PPP at 21600, 26400 and
negotiated 31200 bit/s; the unrestricted control checked a 64 KiB TCP file.
These were local emulated results with normal quality controls retained.
''','modem-dsp-emu/docs/v34_adc_20261014.md')
    log('33600',g,'14 Oct: the 33600 acceptance run checked the file at the receiver', '''
The mixed Analog 109 / PRI 117 pair held DATA for over 558 DSP seconds,
transferred 1310720 bytes on one TCP connection and matched source and
receiver SHA-256. All 100 pings returned; the recorded run had no retrain,
PPP FCS error, link reset or substituted/dropped receive sample.
Both mailboxes published 14-bit datagrams at 2400 Hz.
This is a local emulated loopback, not a physical-line benchmark.
''','modem-dsp-emu/docs/v34_33600_20261014.md')
    log('clock',g,'The send loop was adding its own work to every packet period', '''
Sleeping a whole packet interval after sending made send work accumulate
into the clock period. Waiting for the absolute next-send deadline avoids
that drift. The deterministic example is a 6 ms send cost inside a 20 ms
period: wait 14 ms, not another 20. An overdue empty queue still backs off.
This change and the smaller V.34 cushion reduced the measured delay enough
for the firmware's own rate control to retain 3429 symbols/s.
The firmware delay threshold itself was not patched out.
''','modem-dsp-emu/docs/v34_33600_20261014.md, wire clock','33600')
    log('ansam',g,'23 Oct: original ANSam generator agrees across all eight DSPs', '''
The original V8.ANA routine generated 7680 samples on each physical DSP.
All 61440 samples and the captured final state matched the emulator.
The measured tone was 2100 Hz with the expected phase-reversal behaviour.
A derived segment also travelled exactly through the physical SPORT/IPAC
loop. Generation parity and a finite loop segment remain distinct from
a lossless, continuously serviced live modem path.
''','modem-dsp-emu/docs/modem_tone_output_20261023.md')
    log('fsk',g,'More than one sine wave: V.21 generator payload checks', '''
The same overlay's seven modulation tones and full V.21 FSK generator
were compared on all eight physical DSPs. All 38400 recorded samples
matched the emulator, and the 300 bit/s FSK output demodulated with zero
bit errors in the documented test. These isolated runs had no BRI line,
SPORT clock or analogue connection. They test the generator routines,
not complete call establishment across a real telephone circuit.
''','modem-dsp-emu/docs/modulation_tone_generators_20261023.md','ansam')
    log('mmio',g,'23 Oct measurement corrected the PCI bandwidth intuition', '''
A 20 ms media block cost 97 microseconds of MMIO on the stopped spare:
about 0.5 percent duty. The measured word-at-a-time rate also exceeded
one channel's 8000 words/s by a large margin. Host access bandwidth was
not the bottleneck the earlier architectural worry had suggested.
That does not solve the frame-rate control plane, nor the later observed
zero substitutions in the continuous ring. Feasibility and correctness
are separate claims.
''','modem-dsp-emu/docs/mmap_media_ring_scope_20261023.md')
    log('native',g,'13 Oct: native ISDN profiles pass payload and PPP in virtual time', '''
The host-native backend exercised nine fixed profiles, transferring
32768 known bytes each way and answering 100/100 PPP pings per mode.
The clean controls reported no bearer/PPP FCS errors or retransmitted
I frames. That backend implements the bearer protocols itself; it is
not evidence that closed firmware executed those same data paths.
The clock was deterministic simulated bearer time, not measured realtime
hardware throughput. Several signalling and negotiation features remain
outside the fixed-profile subset.
''','modem-dsp-emu/docs/isdn_native_20261013.md')
    log('acceptance',g,'N_DATA accepted is not N_DATA received', '''
The original MIPS firmware probes reached native call setup and accepted
a 256-byte N_DATA request after the simulated network CONNECT path was
corrected. The firmware owned the state transitions; the harness supplied
missing network events rather than pinning the target state.
An accepted request only establishes that boundary. No remote bearer or
physical peer received the bytes in that probe, and it must not be
reported as an original-firmware end-to-end PPP pass.
''','modem-dsp-emu/docs/isdn_native_20261013.md, firmware control-plane evidence','native')
    log('bri',g,'Board-model milestone: firmware publishes its own ready signatures', '''
The stock reentrant BRI protocol boots eight ADSP cores and publishes
0x4447 on all four controllers. ASSIGN, REMOVE and signalling reallocation
operate on the separate queues with interrupts enabled. The model does
not synthesise the ready signature, entity ID or IDI success code.
The board-model note also records bearer progress, including native
HDLC/X.75 and bounded modem transfers. It explicitly stops short of
claiming a complete PCI/ISDN replacement.
''','modem-dsp-emu/docs/bri_card_model.md')
    log('qemu',g,'QEMU frontend: an unmodified guest driver is a useful boundary test', '''
The PCI frontend forwards register operations into the existing board
model instead of returning canned protocol responses. A Linux 4.9 guest
with the unmodified Debian Diva driver bound the device, read the serial,
registered the IRQ and exposed four controller entries. Legacy loading
also wrote firmware through the virtual BAR and executed RESET/START.
Those checks extend beyond a hand-written host probe, but guest attachment
still does not establish every physical ISDN behaviour.
''','modem-dsp-emu/qemu/README.md','bri')
    g='dev.v90modem'
    log('shortwrite',g,'20 Oct audit: acknowledged bytes were lost after LAPM', '''
Six drain sites read bytes out of the RX ring and ignored how many the
PTY actually accepted. A short write therefore discarded the remainder.
LAPM had already acknowledged those bytes; line retransmission could not
recover them. The replacement drain peeks, writes and consumes only the
accepted count. In the recorded six-second DTE-stall regression, the old
path delivered only 1039 bytes of a 12.5 KB payload; the corrected path
retained the complete payload.
''','v90modem/docs/v42_reliability.md')
    log('busy',g,'Receiver busy needs to reach the peer before the ring overflows', '''
A slow DTE can be overwhelmed even when the line is error controlled,
especially when compression expands incoming data. Merely setting a
local busy flag did not send the required RNR. The updated data stack
watches available RX room, signals busy early and sends RR or REJ when
clearing it as appropriate. Negative controls lost hundreds of thousands
of bytes in slow-DTE rows; the recorded flow-controlled rows lost none.
''','v90modem/docs/v42_reliability.md, receiver-busy flow control','shortwrite')
    log('percall',g,'Queues belong to calls, not the lifetime of the server process', '''
The engine's rings used to initialise only at process startup. Bytes
left by one call could consequently be transmitted on, or delivered from,
the next. Disconnect now permits a bounded drain and then clears the
rings, logging any remaining losses. This is separate from a temporary
retrain on an established call: disconnect cleanup and recovery within
the same call must not be confused.
''','v90modem/docs/v42_reliability.md, per-call rings')
    log('training',g,'Fixing a lossy drain exposed training junk it had been hiding', '''
Once short PTY writes stopped dropping data, training-time V.14 output
became visible after CONNECT. The old bug had accidentally concealed it.
The receive ring is now cleared at the transition into online data and
in data-stack preparation. The reliability audit also added LAPM frame
length/address validation and stopped ignoring a received FRMR.
A fix exposing another defect is not evidence that dropping the data
was correct behaviour.
''','v90modem/docs/v42_reliability.md, training junk and frame validation','shortwrite')
    log('mnp',g,'MNP is a real fallback path, not a synonym for no error control', '''
The project implements MNP classes 2-4 as the alternative procedure from
V.42 Annex A. The factory path can try MNP when LAPM detection does not
find a peer, before falling back to buffered V.14 where permitted.
Required and forbidden error-control modes still constrain that choice.
MNP5 compression is a separate proprietary negotiation extension; it is
not specified by V.42 merely because MNP error control appears there.
''','v90modem/docs/mnp.md, protocol source and mode selection')
    log('mnp5ref',g,'MNP5 independent vectors: exact wire bytes, not self-roundtrip only', '''
The retained reference fixture has 284 segments and 73080 plaintext bytes
from execution of the original HSF encoder, checked with its decoder.
It covers byte values, run-length boundaries, rescaling, flushes and
persistent dictionaries. The tests compare encoded wire bytes and decode
fragmented input. A codec agreeing with itself is not as strong a check
as agreement with an independent implementation.
The note explicitly does not count the timed-out macOS ASan run as a pass.
''','v90modem/docs/mnp.md, independent reference and tests','mnp')
    log('mnp5live',g,'1 Nov MNP5: a bounded pass did not cure the insertion problem', '''
One 120-second capped hardware call verified 430080 downstream and 98304
upstream DTE bytes, while modem diagnostics still recorded recovered frame
errors and a retrain. A repetition inserted an extra byte after 311296
downstream bytes. Independent HSF decoding checked our emitted LT prefix
across that position. The note therefore claims bounded hardware traffic,
not a reliable insertion workaround or long-hold qualification.
''','v90modem/docs/mnp.md, MT9234 V.90/MNP5 hardware follow-up','mnp5ref')
    log('v42bis',g,'V.42bis reset must use the old escape value before replacing state', '''
Compression state persists across arbitrary input boundaries. The stream
implementation negotiates dictionary/string limits and manages transparent
versus compressed mode rather than treating each API call as a new block.
The documented reset sequence flushes pending data, sends ETM where
needed and RESET using the old escape value, then initialises the encoder.
The data-stack compression offer is also distinct from enabling a codec
on a bare V.42 library caller.
''','v90modem/docs/v42bis.md')
    log('v44',g,'V.44 selection is explicit, and refusal falls back to plain LAPM', '''
The C data stack has a streaming V.44 implementation ported from the
Python project. Production does not need a Python interpreter. Selecting
V.44 offers that codec with its documented limits; a peer which does not
agree falls back to plain LAPM, not silently to a different compression
algorithm. Bare V.42 API users must attach their own codecs before enabling
the compression offer. Configuration and negotiation are both part of
the result, not just whether a codec function exists.
''','v90modem/docs/v44.md')
    log('s10',g,'A 30 ms carrier dip should not necessarily end a call', '''
A CX93001 peer briefly dropped signal during V.22bis/MNP calls. Our
callback hung up on the first carrier-down report, while a receiver
restart also disturbed both directions. The carrier-loss work distinguishes
a temporary loss from the S10 hold interval. The project documents its
factory 1.4-second value and its explicit interpretation of 255; those
are implementation choices, not a default supplied by V.250.
''','v90modem/docs/carrier_loss.md')
    log('profiles',g,'Stored AT profiles: failure must not half-save configuration', '''
AT&W and AT&Y save through a temporary file and rename. If writing fails,
the command returns ERROR and leaves the in-memory selection unchanged.
ATZ restores the appropriate profile; AT&F is factory configuration only.
Stored dial strings live beside the profiles but are not part of each
profile's reset semantics. Without a profile file, stored configuration
lasts only for the current process. Those distinctions matter when a
test looks correct until restart.
''','v90modem/docs/at_stored_profiles.md')
    log('qc',g,'Quick Connect implemented does not mean a foreign peer is qualified', '''
The digital-answering short-startup path has its own G.711 codeword
module and engine integration. The documented +PQC default keeps short
Phase 1/2 disabled because foreign-peer qualification is not established;
+PSS=2 forces full startup. V.92 must also be offered. Implementation
coverage, engine tests and an actual compatible peer are separate gates.
This entry records the interface and qualification boundary, not a new
successful Quick Connect hardware call.
''','v90modem/docs/v92_quick_connect.md, controls and short Phase 1')
