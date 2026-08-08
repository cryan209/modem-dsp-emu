#!/usr/bin/env python3
"""A per-call-site cap on printing, for a rig that is paced by the wall clock.

The harness does not overlog in steady state: a live PPP call writes about 870
lines for everything the shim, the ADSP watches and the media thread have to
say.  What it has no defence against is *one* line running away, and that has
now cost three separate investigations a live call to notice:

  * Session 81 -- a rangeless `UC_HOOK_CODE` made every MIPS instruction a
    Python callback and grew an 813 MB trace over a 20 s call.
  * `--trace-v90d-state` on page 14 -- one formatted line per 3200 Hz symbol,
    2,021,167 lines and 816 MB in a single run.
  * The portable bulk-delay "active" edge -- coded as a one-shot, but the edge
    flaps on a live V.34 call, so it printed 136,949 times across three calls
    while everything else in the log came to 2,609.

The cost is not the disk.  It is that `print()` from inside the media tick is
synchronous, the tick has 20 ms, and losing wall time is what makes a call
flaky -- the pump gets handed substituted samples, which to a V.90 receiver is
a line that changed underneath it.  A runaway line is therefore a functional
fault that presents as a modem fault, which is exactly the kind that is
expensive to find.

`emit()` keys a counter on the caller's file and line, so a single format site
is capped however many different values it formats.  Past the cap it stops
printing and counts, and `summary()` reports every site that was throttled --
so the next runaway announces itself in one line at the end of a run rather
than being found by reading a 10 MB log.

    from logcap import emit, summary

    emit(f'[native-mips] portable V.34 bulk delay active: near={n}')
    ...
    print(summary())     # or let the atexit hook do it

The cap deliberately does not sample-and-continue.  A line that has fired 200
times has said what it has to say, and the point is to stop paying for it, not
to pay less often.
"""
from __future__ import annotations

import atexit
import os
import sys
import threading

# 200 lines is well clear of anything the harness says legitimately -- the
# busiest honest site in a live call is TrnProgress at roughly 350 -- while
# still being three orders of magnitude below a runaway.
DEFAULT_LIMIT = int(os.environ.get('EICON_LOG_CAP', '200'))

# Counters are keyed by call site and touched from the media thread as well as
# the main one, so the increment is taken under a lock.  It is only paid on
# lines that actually print, which is the traffic being limited.
#
# The effective limit is stored alongside the count rather than assumed to be
# DEFAULT_LIMIT: a site with its own `limit=` is precisely the one most likely
# to run away, and comparing it against the default hid it from the summary.
_lock = threading.Lock()
_counts: dict[tuple[str, int], tuple[int, int]] = {}


def emit(message: str, *, limit: int | None = None, file=None) -> bool:
    """Print `message` unless this call site has already printed `limit` times.

    Returns True if it was printed.  The site is the caller's file and line, so
    two different sites never share a budget and one site never escapes its own
    by formatting a different value each time.
    """
    cap = DEFAULT_LIMIT if limit is None else limit
    frame = sys._getframe(1)
    key = (frame.f_code.co_filename, frame.f_lineno)
    with _lock:
        seen = _counts.get(key, (0, cap))[0] + 1
        _counts[key] = (seen, cap)
    if seen > cap:
        return False
    print(message, file=file)
    if seen == cap:
        print(f'[logcap] {_site(key)} reached {cap} lines -- further '
              'output from this site is suppressed and counted',
              file=file)
    return True


def _site(key: tuple[str, int]) -> str:
    return f'{os.path.basename(key[0])}:{key[1]}'


def throttled() -> list[tuple[str, int, int]]:
    """Every capped site, as (site, printed, suppressed), worst first."""
    with _lock:
        items = list(_counts.items())
    out = []
    for key, (seen, cap) in items:
        if seen > cap:
            out.append((_site(key), cap, seen - cap))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def summary() -> str:
    """One line naming what ran away, or what didn't.  Safe to call always."""
    rows = throttled()
    if not rows:
        return '[logcap] no site hit the cap'
    parts = ', '.join(f'{site} +{dropped}' for site, _, dropped in rows)
    total = sum(row[2] for row in rows)
    return f'[logcap] {len(rows)} site(s) capped, {total} lines suppressed: {parts}'


def reset() -> None:
    """Forget every counter.  For tests, and for a rig that runs many calls."""
    with _lock:
        _counts.clear()


@atexit.register
def _report_on_exit() -> None:
    # Only speak up when there is something to report: a clean run should not
    # gain a line for a guard that never fired.
    if throttled():
        print(summary())
