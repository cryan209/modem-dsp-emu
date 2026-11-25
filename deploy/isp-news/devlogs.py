"""Sourced project notebook summaries, not fictional forum dialogue."""
def extend(posts):
    def log(key,group,subject,body,source,parent=None):
        posts.append(dict(key='devlog-'+key,group=group,author='Modem lab notebook',subject=subject,
                          body=body.strip()+'\n\nSource: '+source+'\n',
                          parent='devlog-'+parent if parent else None,kind='devlog'))

    g='dev.modem-dsp-emu'
    log('emu-intro',g,'Project map: running the original modem DSP firmware', '''
This group records work on the modem-dsp-emu project. The investigation
includes the ADSP firmware, the surrounding Eicon card model, native
B-channel transport and the eventual PPP path. It is not enough to make
the handshake look plausible: samples, state, frames and delivered bytes
need separate checks.

These entries summarise the repository's recorded findings. Dates identify
the experiments being described; publishing a summary is not a new test.
Some entries preserve an unsuccessful hypothesis because it explains why
we should not spend another afternoon testing the same wrong thing.
''','modem-dsp-emu/docs/handoff.md; git log through 7375b8a')
    log('emu-t401',g,'23 Oct: V.34 reached data mode; T401 was killing the link', '''
The V.34 19200 loopback reached TrnProgress 0x00d0 on both ends, then
failed with a V.42 FRMR. The timer counted datapump datagrams: 1200 at
2400 datagrams/s meant 500 ms, shorter than the measured 692-743 ms RTT.

A premature SABME retransmission made the answerer re-establish LAPM and
reset its send state. An acknowledgement from before that reset then
looked invalid. The link was obeying the protocol; our timeout was wrong.

Changing the default to 3600 datagrams gives 1.5 s at that rate. The
recorded loopback established CHAP/IPCP and returned 20/20 pings, with
zero FCS errors or link resets. This result is an emulated loopback,
not a qualification of the physical card or a real analogue line.
''','modem-dsp-emu/docs/v34_data_mode_t401_20261023.md')
    log('emu-sampling',g,'Correction: RXSAMPLE was not unwritten; the observation was misleading', '''
An earlier conclusion said the analogue receive sample array was never
filled. The later execution/write watches contradicted that conclusion.
The overlay itself copies samples into RXSAMPLE through its receive ring;
the kernel need not directly reference the destination addresses.

A frame-boundary snapshot was not sufficient evidence that writes never
occurred during the frame. The proposed replacement receive path would
have replaced something already working.

The physical follow-up recorded 4096 isolated routine calls matching
hardware for the V8.ANA biquad, magnitude and detector paths, including
history and counters. That establishes those tested routines, not the
correctness of complete live-call scheduling.
''','modem-dsp-emu/docs/analog_rxsample_correction.md')
    log('emu-ring',g,'24 Oct: continuous SPORT1 transport is not yet lossless transport', '''
Three saved 60-second hardware ring runs had no overruns or underruns
and retained a one-sample loop delay. They still contained 25-41 sample
anomalies per run. In the final run, all 36 were zero substitutions and
remained wrong on reread.

The old word "clean" had meant flow continuity. The runner now separates
flow_continuous from anomaly_free, and successful clean status requires
both. A stream can keep moving at the right rate while carrying wrong
words. Treating those as the same result hid the next investigation.
''','modem-dsp-emu/docs/sport_ring_status_20261024.md')
    log('emu-ring-follow',g,'Isolation controls point back at board receive configuration', '''
The recorded controls on the spare board included faster host polling,
a second DSP, autonomous TX, constant IPAC DU input and runs without live
host IDMA. The zeros did not disappear. An RX-only autonomous DMA control
captured one unexpected zero among 8192 retained words in two of three
trials; the third was exact.

The supported next target is receive clock/framing/DU configuration.
These observations do not justify changing emulator arithmetic or host
scheduling merely to hide the substituted samples. Stock firmware was
restored after the bounded hardware controls.
''','modem-dsp-emu/docs/handoff.md, 24 Oct SPORT1 isolation summary; docs/sport_ring_status_20261024.md','emu-ring')
    log('emu-law',g,'9 Oct result: a codec-law descriptor error kept V.90 off-grid', '''
The direct backend had not set the PCMU channel-descriptor law word.
The V.90 digital firmware consequently ran an A-law configuration on a
mu-law line. The recorded downstream constellation was off-grid, with
12.9 percent BER.

Setting the law through configure_g711_law() and adding V.90 mailbox
support allowed PPP/IPCP and 8/8 pings through that loopback. The recorded
downstream BER fell to 0.15 percent at 38666 bit/s. A retrain about 28 s
into data mode still dropped the link. This was progress, not a clean
long-duration V.90 qualification.
''','modem-dsp-emu/docs/handoff.md, 9 Oct IP follow-up; docs/v90a_live_caller_capture.md')
    log('emu-pty',g,'4 Nov: the shared PTY slave was the wrong suspect', '''
The live PPP investigation had already demonstrated the framing against
a real pppd, as well as the bearer and intermediate hops. The remaining
failure was intermittent and affected traffic in both directions.

A diagnostic released and retook the permanent PTY slave hold across two
live calls. It behaved as intended, but moved no PPP frames either way.
That disproved the proposed shared-slave ownership explanation for those
runs. The next inspection point in the recorded investigation was the
DTE ring and dte_payload() path, not another variation on tty ownership.
''','modem-dsp-emu commit 7375b8a; docs/mlppp_7910_20261104.md, shared-slave investigation')
    log('emu-mlppp',g,'4 Nov: aggregation worked, useful throughput still did not', '''
The hardware ISP work needs its own result boundary. Two connected
members did carry data, including a measured mixed-card interval with
almost equal transmitted MP frame volume on both channels. Useful TCP
goodput was still poor and retransmission/stalls remained.

The full-framing experiment disabled protocol/address-control compression
and the logs confirmed the client removed those options. That did not
prevent the later stall. A secondary CONNECT light and a joined bundle
are not substitutes for sustained receiver-side delivery measurements.
No specific remaining cause was established by that compatibility test.
''','modem-dsp-emu/docs/mlppp_7910_20261104.md, mixed-card and full-framing observations')

    g='dev.v90modem'
    log('v90-intro',g,'Project map: a SIP/G.711 softmodem, not an audio effect', '''
The main role is the digital side of V.90/V.92: an analogue modem calls
through an FXS path, while the softmodem trains over SIP/RTP and hands
data to a PTY. G.711 codewords represent the digital DS0 path; they are
not merely a recording of modem noises.

The project also contains other modem families, clear channel and fax.
Coverage varies by mode and peer. The readme's status matrix distinguishes
engine-pair tests, firmware emulation and actual hardware calls instead
of presenting one broad "supported" badge.

These are summaries of the real development notes, separate from the
fictional conversations elsewhere on this news server.
''','v90modem/readme.md; docs/README.md')
    log('v90-pcm',g,'Design boundary: preserve digital G.711 codewords exactly', '''
On the digital side, the RTP payload is the DS0 presented to the network
D/A. Resampling, speech enhancement, gain changes or decode/re-encode
operations can destroy the codeword relationship a PCM modem needs.

That is distinct from the analogue role's linear-PCM interface. Its
16 kHz audio timeline represents half-symbol timing and calibrated
headroom; it is not permission to change digital-side G.711 words.
Sample accounting is a protocol concern, not just an audio-quality knob.
''','v90modem/AGENTS.md, modem constraints; docs/v92_full_startup.md, endpoint interfaces')
    log('v90-soak',g,'8 Oct hardware milestone: more than CONNECT and a screenshot', '''
The recorded Eicon V.90 analogue-caller work includes two roughly
26.5-minute calls and 16177152 checked payload bytes with no sequence,
CRC or payload errors. Both codec laws passed PPP with 30/30 pings and
exact 1 MiB HTTP transfers.

The readme reports about 6.44 kB/s binary and 6.21 kB/s HTTP on that LAN
path with a 40 ms jitter buffer. Those measurements describe that rig
and those calls. They do not qualify every modem or remove the separate
open work in the digital answerer's upstream receiver.
''','v90modem/readme.md, hardware headline result; docs/eicon_soak_ppp.md')
    log('v90-gradient',g,'28 Oct: the equaliser gradient was in the wrong coordinates', '''
The MT9234 upstream receiver trained, then lost decoding and fell back.
The equaliser error was measured after carrier derotation, but the old
NLMS update multiplied it directly against raw input coordinates.

Returning the error through the inverse rotation fixes the coordinate
mismatch. The same-symbol phase matters: use the phase that derotated
that sample, before the carrier update advances it. The DD and CMA
branches share the corrected transformation.

The retained replay still contains acquisition damage. The correction
reduced damaged segments and final symbol error; it did not magically
turn the original recording into a zero-error capture.
''','v90modem/docs/mt9234_v90_gradient.md')
    log('v90-gradient-live',g,'MT9234 physical follow-up: bounded payload, bounded claim', '''
The corrected physical calls held V.90/LAPM/V42bis for the requested
60 seconds without retrains or renegotiations. The second call negotiated
46666 down / 26400 up and delivered a known 1920-byte upstream payload
byte-for-byte to the answerer.

The first corrected call reported zero TX/RX frame errors; the second
reported one TX frame error and zero RX frame errors. Those are the
recorded diagnostics, not a claim that both calls were error-free.
Longer multi-peer qualification remains a different task.
''','v90modem/docs/mt9234_v90_gradient.md, physical confirmation','v90-gradient')
    log('v92-diagnosis',g,'5 Nov: the 37333 upstream collapse was in our receive decisions', '''
The same live decoder-input capture produced 248957 wrong bits out of
3447416 with the old frame-local decoder. A continuous-trellis prototype
with about 6 ms lookahead produced zero on that compared input.

The old receiver retained only one winning end state after each short
frame. Discarding the other trellis alternatives could poison the next
frame's starting decision. Zero rejected-frame counts did not imply zero
BER: wrong outputs could still belong to the valid source alphabet.

This initial replay established a receive limitation. By itself it was
not a higher-rate live interoperability pass.
''','v90modem/docs/v92_upstream_trellis.md, diagnosis and controlled experiment')
    log('v92-production',g,'Streaming trellis integration: retain alternatives across frames', '''
The production receiver now retains all 16 trellis state metrics across
frame boundaries. A bounded survivor ring provides 48 symbols, or 6 ms,
of decision lookahead. That delay is an implementation choice, not a
new delay requirement imposed on the peer.

Delayed decisions must update the equaliser rows belonging to the earlier
input frame. Decoder delay is not a licence to add or remove samples.
The regression fixture requires 279776 guarded bits with zero errors
under input chunks of 1, 37, 160 and 320 samples. The old decoder made
13637 errors on that fixture's guarded bits.
''','v90modem/docs/v92_upstream_trellis.md, production receiver and regressions','v92-diagnosis')
    log('v92-wired',g,'Wired qualification: hashes beat nominal upstream rates', '''
The subsequent wired slmodemd campaign recorded 11 passing calls and
6029312 receiver-verified bytes in each direction, with complete hashes
acknowledged. Long 37333 and 38666 upstream calls passed 1 MiB each way.

The 37333 long-call upload goodput was about 33.983 kbit/s; the 38666
long call delivered about 34.344 kbit/s. Nominal 40000/41333 calls passed
smaller transfers but were not faster. A clean 42666 repeat failed B1u
acquisition. The unforced selector still chose 36000.

These are wired slmodemd results on the documented rig, not qualification
against a physical V.92 modem. Defaults were not raised merely because
some forced higher-rate calls passed.
''','v90modem/docs/v92_upstream_trellis.md, wired slmodemd qualification','v92-production')
