#!/usr/bin/python3
"""Verify every profile using a short source sample held only in memory."""
import json
import subprocess
import radio

sample = subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-tls_verify', '1', '-ca_file',
                         '/etc/ssl/certs/ca-certificates.crt', '-rw_timeout', '15000000',
                         '-i', radio.SOURCE, '-t', '2', '-c:a', 'copy', '-f', 'mp3', 'pipe:1'],
                        capture_output=True, timeout=30, check=True).stdout
for kind in ('mp3', 'wma', 'rm'):
    for rate in radio.RATES:
        path = '/cheese/%s.%s' % (rate, kind)
        try:
            radio.profile(path)
        except ValueError:
            continue
        encoded = subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-i', 'pipe:0'] + radio.encoding(rate, kind),
                                 input=sample, capture_output=True, timeout=20, check=True).stdout
        if kind == 'rm':
            encoded = radio.RealAudioTiming().feed(encoded)
        result = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,bit_rate,channels,sample_rate',
                                 '-of', 'json', '-i', 'pipe:0'], input=encoded, capture_output=True, timeout=10, check=True)
        info = json.loads(result.stdout)['streams'][0]
        assert int(info['bit_rate']) == rate * 1000, (path, info)
        assert info['channels'] == 1, (path, info)
        if kind == 'rm':
            packets = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', '-show_entries', 'packet=pts_time',
                                      '-of', 'json', '-i', 'pipe:0'], input=encoded, capture_output=True, timeout=10, check=True)
            timestamps = [float(x['pts_time']) for x in json.loads(packets.stdout)['packets']]
            assert len(timestamps) > 10 and all(b > a for a, b in zip(timestamps, timestamps[1:])), timestamps
        subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-i', 'pipe:0', '-f', 'null', '-'],
                       input=encoded, capture_output=True, timeout=10, check=True)
        print(path, info, flush=True)
