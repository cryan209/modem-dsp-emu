#!/usr/bin/env python3
"""A pseudo-terminal on the far side of the emulated modem's V.42 link.

`eicon_adsp_sip.py --tx-v42 --v42-pty` allocates a PTY, prints the slave path,
and moves bytes between it and `LapmEndpoint`. Anything typed into the slave
becomes LAPM I frames; payload the peer acknowledges is written back to it. So
`screen /dev/ttysNNN` puts a terminal on whatever dialled in.

    screen /dev/ttys012            # or: minicom -D /dev/ttys012

The PTY carries no line speed, parity or flow control: those belong to the
asynchronous side of a real modem's UART, and this link starts at the
synchronous V.42 boundary, where the data pump has already framed the bits.
`stty` on the slave will appear to work and change nothing.

Flow control is the one thing that is real. LAPM's window is the only backing
store, so once it fills, reads from the PTY stop and the terminal blocks --
which is the correct behaviour, and the reason the writer must never be allowed
to outrun the link.
"""
from __future__ import annotations

import errno
import os
import pty
import tty


class PtyLink:
    """Bridge a PTY to a LapmEndpoint, pumped once per media tick.

    With an `AtParser` attached the terminal comes up in command mode instead:
    input is parsed rather than framed, and only the payload the parser passes
    through reaches LAPM.  That is the difference between `screen` showing a
    modem and `screen` showing raw link bytes.
    """

    def __init__(self, read_chunk: int = 4096, log=print,
                 at_parser=None, on_action=None) -> None:
        self.master, self.slave = pty.openpty()
        # Raw: no echo and no CR/LF translation on the way through. The far
        # modem's terminal is responsible for its own line discipline, and a
        # local echo here would double every character it sends -- except in
        # AT command mode, where ATE1 echo is the parser's job and this stays
        # the right setting for the same reason.
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.name = os.ttyname(self.slave)
        self.read_chunk = read_chunk
        self.log = log
        self.at = at_parser
        self.on_action = on_action
        self.to_link = 0
        self.from_link = 0
        self.blocked_ticks = 0
        # CONNECT may be reported before LAPM completes SABME/UA. Preserve
        # terminal data written during that interval instead of dropping it.
        self._pending_link = bytearray()
        self._closed = False
        self.log(f'[v42-pty] terminal ready on {self.name} '
                 f'-- attach with: screen {self.name}')

    def write_terminal(self, payload: bytes) -> None:
        """Write to whoever has the slave open, tolerating nobody being there.

        EIO means the slave has no reader yet.  Dropping is right: buffering
        for an absent reader would replay stale session output at whoever
        attaches next.
        """
        if self._closed or not payload:
            return
        try:
            os.write(self.master, payload)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EIO):
                raise

    def pump(self, lapm) -> None:
        """Move bytes both ways. Safe to call when the link is still down.

        ``lapm`` may be None when the PTY is attached for AT call control only
        (no ``--tx-v42``): the terminal is a modem command console, not a
        data link, so only the AT parser path runs.
        """
        if self._closed:
            return
        if self.at is not None:
            # The escape sequence's trailing guard time cannot elapse inside
            # feed(), so the parser needs a tick to finish it.
            self.write_terminal(self.at.poll())
        # Link -> terminal. Drain whatever LAPM has accepted in sequence.
        if lapm is not None and lapm.rx_data:
            payload = bytes(lapm.rx_data)
            del lapm.rx_data[:]
            self.write_terminal(payload)
            self.from_link += len(payload)
        # Flush data accepted while CONNECT was reported but LAPM was still
        # negotiating. Respect the same window/backing-store limit used below.
        if (self.at is not None and lapm is not None and lapm.data_ready
                and self._pending_link):
            capacity = (len(self._pending_link) if lapm.raw_mode else
                        ((lapm.window - lapm.outstanding) * lapm.n401
                         - len(lapm.tx_stream)))
            if capacity > 0:
                count = min(capacity, len(self._pending_link))
                lapm.send(bytes(self._pending_link[:count]))
                del self._pending_link[:count]
                self.to_link += count

        # Terminal -> link. Only read while the window can still take frames,
        # so the PTY itself provides the back-pressure.  In command mode the
        # window is irrelevant: read regardless, or a terminal issuing AT
        # commands before the link exists would never be serviced.
        if self.at is None and (lapm is None or not lapm.data_ready):
            return
        while True:
            if lapm is not None and lapm.data_ready and not lapm.raw_mode and not (
                    lapm.outstanding < lapm.window
                    and len(lapm.tx_stream) < lapm.n401):
                self.blocked_ticks += 1
                return
            try:
                data = os.read(self.master, self.read_chunk)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EIO):
                    return
                raise
            if not data:
                return
            if self.at is None:
                lapm.send(data)
                self.to_link += len(data)
                continue
            to_terminal, to_link = self.at.feed(data)
            self.write_terminal(to_terminal)
            self.dispatch_actions()
            if to_link and lapm is not None and lapm.data_ready:
                capacity = (len(to_link) if lapm.raw_mode else
                            ((lapm.window - lapm.outstanding) * lapm.n401
                             - len(lapm.tx_stream)))
                count = max(0, min(len(to_link), capacity))
                if count:
                    lapm.send(to_link[:count])
                    self.to_link += count
                if count < len(to_link):
                    self._pending_link.extend(to_link[count:])
            elif to_link:
                # Carrier may be up before LAPM. Preserve bytes until the
                # peer completes link establishment instead of dropping them.
                if lapm is not None:
                    self._pending_link.extend(to_link)
                else:
                    self.log(f'[v42-pty] dropped {len(to_link)} bytes written in '
                             'data mode with no LAPM link')

    def dispatch_actions(self) -> None:
        """Hand anything the parser could not do itself to the caller."""
        if self.at is None:
            return
        while self.at.actions:
            action = self.at.actions.pop(0)
            if self.on_action is None:
                self.log(f'[at] {action} (no handler; ignored)')
                continue
            reply = self.on_action(action)
            if reply:
                self.write_terminal(reply)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        self.log(f'[v42-pty] {self.name} closed after {self.to_link} bytes to '
                 f'the link and {self.from_link} from it '
                 f'({self.blocked_ticks} ticks with the window full)')
