# Private retro ISP radio

Live catalogue: http://10.150.150.157:8089/ (linked from ISP services).
Source: https://station.thecheese.co.nz/listen/the_cheese/128.mp3

On-demand mono conversions:

- MP3: 8, 16, 24, 32, 40, 48, 56, 64 kbit/s.
- WMA v2 / ASF: 24, 32, 40, 48, 56, 64 kbit/s. Both FFmpeg WMA
  encoders reject 8/16 kbit/s, independently of sample rate.
- RealAudio 1.0 (`real_144`) in RM: 8 kbit/s only. The codec's 14.4K
  name refers to modem class; the encoded audio is fixed at 8,000 bit/s.
  It cannot produce other requested bitrates.

Stream URLs: /cheese/RATE.mp3, /cheese/RATE.wma and /cheese/8.rm.
Append .m3u, .asx or .ram respectively for external-player playlists.
Unsupported combinations return 404. HTML 3.2, HTTP/1.0, no JavaScript,
no chunked transfer. 8 kbit/s MP3 uses 11025 Hz / MPEG-2.5; remaining
MP3/WMA streams use 22050 Hz, RealAudio 8000 Hz. Older MP3 players may
not support MPEG-2.5. Actual vintage Winamp/WMP/RealPlayer versions have
not been tested. WMA is ordinary ASF over HTTP, not an MMS server; RM is
HTTP, not RTSP/PNM. Player support for these transports must be checked.

Rates are codec audio rates, not total line usage. RM has a 12-byte packet
header per 20-byte/20-ms audio frame, giving about 12.8 kbit/s before TCP/IP.
WMA/ASF and MP3 have their own overhead. Start with 16 kbit/s MP3 on dial-up;
48/56/64 kbit/s are for sufficiently fast multilink or LAN connections.

Each listener owns one FFmpeg process and one upstream 128 kbit/s connection.
No shared daemon fetch or audio archive; processes stop after disconnect,
write timeout or source inactivity. Eight listeners globally, two per source
IP; a third receives 503. Stops/restarts of radio disconnect its listeners.
State is in memory only. Fixed source URL and allowlisted profiles, no shell
execution or caller-supplied URL/codec. HTTPS source certificate validation
is enabled against the system CA bundle. Source reads time out after 15 s,
first-output deadline 25 s, output inactivity deadline 30 s, client writes
20 s. Player reconnection may be necessary after a source failure.

FFmpeg's RM muxer writes zero audio timestamps with this codec. The service
parses complete RM chunks/packets and repairs timestamps in 20-ms frame
steps. Header and packet payloads are preserved. Validation checks strict
monotonicity; fragmented-read unit tests cover packet assembly. The standard
nonseekable RM header advertises a one-hour duration even for live sources;
players may display that duration or require reopening after an hour.

Installed Debian FFmpeg 7.1.5 via apt. Service retro-isp-radio runs DynamicUser,
read-only filesystem, no home access, private tmp, 1 GB memory / 2 CPU quota,
128 tasks. Source and unit installed under /usr/local/lib/retro-isp-radio and
/etc/systemd/system. Enabled at boot. Port 8089 is private RFC1918/loopback
only in the persistent retro_isp nftables table and in the HTTP handler.
Only this firewall table was replaced; PPP/NAT rules and modem services
were preserved. Portal web service restarted to expose navigation links.
Backup: /var/backups/isp-radio-20261105-134647.

Validation:

    python3 -m unittest discover -s tests -p test_isp_radio.py
    python3 /usr/local/lib/retro-isp-radio/verify.py

Five unit checks cover valid/invalid profiles, fixed verified upstream,
playlist URLs, HEAD/public-source rejection without encoder launch, listener
limits, failed-launch cleanup, fragmented RM data/timestamp repair.
verify.py keeps a two-second station sample only in memory, encodes all 15
advertised profiles, checks true codec bitrate/mono layout and decodes them;
RM packet timestamps must increase. Live HTTP MP3/WMA decode verified;
RealAudio live decode verified after timestamp repair with a small probe
buffer. Older software/hardware playback remains a caller-side test.
