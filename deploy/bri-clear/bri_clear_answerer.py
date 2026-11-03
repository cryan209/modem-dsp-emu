#!/usr/bin/env python3
"""Digital-peer answerer for one Diva ttyds channel.

A clear-channel or HDLC/PPP call has no analogue carrier to train, so the
modem answerers (TTY profile 14, autodetect) are the wrong peer for one: a
clear-channel caller just samples whatever audio comes back. This answers
with an explicit TTY profile and then echoes the B-channel bytes, so a 64k
digital call has something on the other end that counts what it received.

Profiles, from divas4linux AT.txt "Supported TTY Profiles":
   8   bit-transparent B-channel access (clear channel); does not enter
       data mode by itself, so ATO is sent after CONNECT
   9   HDLC/transparent, the stack used for ISDN PPP at 64k
   6   V.120 64k      2   V.110 synchronous      3   V.110 asynchronous
"""
import argparse, errno, fcntl, json, logging, os, select, termios, time

LOG = logging.getLogger('bri-clear')


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = 0
    a[1] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL | termios.CRTSCTS
    a[3] = 0
    a[4] = a[5] = termios.B115200
    a[6][termios.VMIN] = 0
    a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def command(fd, cmd, timeout=5):
    os.write(fd, cmd.encode() + b'\r')
    data = bytearray()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if select.select([fd], [], [], .2)[0]:
            try:
                data.extend(os.read(fd, 4096))
            except BlockingIOError:
                pass
        if b'ERROR' in data:
            raise RuntimeError('%s: ERROR' % cmd)
        if b'OK' in data:
            return
    raise RuntimeError('%s: no OK' % cmd)


def session(fd, channel, hold):
    """Echo B-channel bytes until the peer stops or hold expires."""
    stats = {'device': channel['device'], 'profile': channel['profile'],
             'rx_bytes': 0, 'tx_bytes': 0}
    start = time.monotonic()
    last_rx = start
    while time.monotonic() - start < hold:
        if not select.select([fd], [], [], 1)[0]:
            if time.monotonic() - last_rx > 30:
                break
            continue
        try:
            data = os.read(fd, 4096)
        except (BlockingIOError, OSError) as e:
            if isinstance(e, OSError) and e.errno not in (errno.EAGAIN, errno.EIO):
                raise
            continue
        if not data:
            break
        last_rx = time.monotonic()
        stats['rx_bytes'] += len(data)
        written = 0
        while written < len(data):
            if select.select([], [fd], [], 1)[1]:
                try:
                    written += os.write(fd, data[written:])
                except BlockingIOError:
                    pass
            else:
                break
        stats['tx_bytes'] += written
    stats['seconds'] = round(time.monotonic() - start, 3)
    return stats


def worker(channel, hold):
    path = channel['device']
    while True:
        fd = None
        try:
            fd = open_port(path)
            for cmd in ('AT&F%d' % channel['profile'],
                        'AT+iQ=a%d' % channel['controller'],
                        'AT+iA%s' % channel['number'],
                        'ATE0V1S0=1'):
                command(fd, cmd)
            LOG.info('%s armed profile=%s controller=%s number=%s',
                     path, channel['profile'], channel['controller'], channel['number'])
            data = bytearray()
            while b'CONNECT' not in data:
                if select.select([fd], [], [], 1)[0]:
                    data.extend(os.read(fd, 4096))
                if len(data) > 8192:
                    del data[:-4096]
            line = bytes(data[data.index(b'CONNECT'):]).split(b'\n')[0]
            LOG.info('%s %s', path, line.decode(errors='replace').strip())
            # Profile 8 stays in command mode after CONNECT (AT.txt note [a]).
            if channel['profile'] == 8:
                os.write(fd, b'ATO\r')
                time.sleep(.5)
            stats = session(fd, channel, hold)
            LOG.info('%s result %s', path, json.dumps(stats))
        except Exception:
            LOG.exception('%s worker restarting', path)
            time.sleep(2)
        finally:
            if fd is not None:
                os.close(fd)
        time.sleep(.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    args = ap.parse_args()
    cfg = json.load(open(args.config))
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    hold = cfg.get('hold_seconds', 600)
    channels = cfg['channels']
    if len(channels) != 1:
        raise SystemExit('this answerer serves exactly one channel')
    worker(channels[0], hold)


if __name__ == '__main__':
    main()
