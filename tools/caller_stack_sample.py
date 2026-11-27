"""Opt-in macOS stack observations of an owned caller; not CPU-time attribution.

The external sampler can perturb scheduling. No endpoint instrumentation,
per-sample timers, deadline changes or attach-by-user-supplied-PID interface.
"""
from collections import Counter
import hashlib
import math
from pathlib import Path
import re
import subprocess
import sys
import time

from v34_payload import write_json

TOOL = Path('/usr/bin/sample')
MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 20000
MAX_LOG_BYTES = 8192


def available():
    return sys.platform == 'darwin' and TOOL.is_file()


def category(path):
    """Exclusive PC/path labels, not runnable state, elapsed time or causation."""
    if any(re.search(r'\b(take_gil|_PyEval_AcquireThread)\b', s) for s in path):
        return 'gil_path'
    if any('PyThread_acquire_lock' in s for s in path):
        return 'python_lock_path'
    leaf = path[-1]
    symbol = leaf.split('  (in ')[0]
    if re.match(r'(_+)?(select|poll|kevent)(\b|_nostack)', symbol):
        return 'selector_leaf'
    if re.search(r'(__semwait_signal|__psynch_cvwait|__ulock_wait|semaphore_wait|nanosleep)', symbol):
        return 'wait_or_sleep_leaf'
    if re.match(r'_*([pw]?read|[pw]?write|fsync|open|close|fstat)(\b|_nocancel)', symbol):
        return 'io_syscall_leaf'
    if '(in libadsp2181.dylib)' in leaf:
        return 'adsp_native_leaf'
    if '(in Python)' in leaf:
        return 'python_runtime_leaf'
    if '(in _ctypes.' in leaf or '(in libffi.' in leaf:
        return 'ctypes_ffi_leaf'
    if '(in libsystem_malloc.' in leaf:
        return 'allocator_leaf'
    return 'other_leaf'


def parse_report(text, expected_pid):
    if len(text.encode()) > MAX_BYTES:
        raise ValueError('stack report exceeds bound')
    pid = re.findall(r'^Process:\s+.+?\[(\d+)\]\s*$', text, re.M)
    if pid != [str(expected_pid)] or not re.search(r'^Report Version:\s+7\s*$', text, re.M):
        raise ValueError('wrong PID, missing header, or unsupported report version')
    if text.count('Call graph:\n') != 1 or '\nBinary Images:' not in text:
        raise ValueError('missing/ambiguous graph or incomplete report')
    graph = text.split('Call graph:\n', 1)[1]
    if '\nTotal number in stack' not in graph:
        raise ValueError('missing graph terminator')
    graph = graph.split('\nTotal number in stack', 1)[0]
    roots, stack, nodes = [], [], 0
    for line in graph.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([ +!:|`-]*)(\d+) (.+)', line)
        if not match:
            raise ValueError('unrecognized graph line')
        depth, count, frame = match.start(2), int(match[2]), match[3]
        if depth > 516:
            raise ValueError('graph depth exceeds bound')
        if count <= 0:
            raise ValueError('nonpositive sample count')
        nodes += 1
        if nodes > MAX_NODES:
            raise ValueError('too many graph nodes')
        while stack and depth <= stack[-1]['depth']:
            stack.pop()
        node = dict(depth=depth, count=count, frame=frame, children=[])
        if not stack:
            if not re.match(r'Thread_\d+\b', frame) or depth != 4:
                raise ValueError('missing thread root')
            roots.append(node)
        else:
            if depth != stack[-1]['depth'] + 2 or re.match(r'Thread_\d+\b', frame):
                raise ValueError('invalid graph depth')
            stack[-1]['children'].append(node)
        stack.append(node)
    main = [r for r in roots if 'com.apple.main-thread' in r['frame']]
    if len(main) != 1:
        raise ValueError('main thread missing or ambiguous')
    threads = []
    for root in roots:
        leaves, counts = [], Counter()
        # Iterative traversal avoids recursion limits on damaged/deep reports.
        todo = [(root, [])]
        while todo:
            node, parents = todo.pop()
            exclusive = node['count'] - sum(n['count'] for n in node['children'])
            if exclusive < 0 or (node is root and exclusive):
                raise ValueError('inconsistent inclusive counts')
            path = parents + [node['frame']]
            if exclusive:
                label = category(path[1:])
                counts[label] += exclusive
                leaves.append(dict(count=exclusive, category=label, path=path[1:]))
            todo.extend((child, path) for child in reversed(node['children']))
        if sum(counts.values()) != root['count']:
            raise ValueError('exclusive counts do not conserve samples')
        threads.append(dict(thread=root['frame'], main=root is main[0], samples=root['count'],
                            categories=dict(counts), exclusive_paths=leaves))
    return dict(schema_version=1, scope='stack residency observations, not CPU time or causal wait attribution',
                pid=expected_pid, nodes=nodes, threads=threads)


def read_report(path, pid):
    with Path(path).open() as handle:
        text = handle.read(MAX_BYTES + 1)
    return parse_report(text, pid)


def align_incidents(receipt, trace, expected_underruns):
    """Possible overlap only: marker delivery can lag; snapshots lack timestamps."""
    from media_clock_trace import valid_receipt
    if not isinstance(receipt, dict) or not isinstance(trace, dict):
        return dict(available=False, reason='invalid receipt/trace')
    timing = receipt.get('collection_timing', {})
    timing = timing if isinstance(timing, dict) else {}
    begin = receipt.get('begin_monotonic_ns')
    start_marker = timing.get('start_marker_received_monotonic_ns')
    end = timing.get('completion_marker_received_monotonic_ns')
    process_end = receipt.get('end_observed_monotonic_ns')
    if (receipt.get('schema_version') != 2 or timing.get('complete') is not True
            or not all(type(v) is int for v in [begin, start_marker, end, process_end])
            or not 0 <= begin <= start_marker <= end <= process_end):
        return dict(available=False, reason='missing/invalid collection markers; no process-envelope fallback')
    dropped = trace.get('dropped_incidents')
    events = trace.get('underruns')
    if (not valid_receipt(trace, expected_underruns) or type(dropped) is not int or dropped < 0
            or not isinstance(events, list) or len(events) + dropped != expected_underruns):
        return dict(available=False, reason='incomplete/count-mismatched media trace')
    incidents = []
    counts = Counter(before_launch=0, possible_collection_overlap=0, after_collection_marker=0)
    for event in events:
        stamp = event.get('time') if isinstance(event, dict) else None
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not math.isfinite(stamp * 1e9)):
            return dict(available=False, reason='invalid incident time')
        ns = round(stamp * 1e9)
        label = ('before_launch' if ns < begin else
                 'after_collection_marker' if ns > end else 'possible_collection_overlap')
        counts[label] += 1
        incidents.append(dict(time_monotonic_ns=ns, classification=label))
    return dict(available=True, scope='possible collection envelope, not individual stack/incident correlation',
                begin_monotonic_ns=begin, end_monotonic_ns=end,
                counts=dict(counts), incidents=incidents,
                dropped_incidents=trace['dropped_incidents'])


class CallerStackSample:
    """Owned Popen only. Starts on verified progress; cleanup signals sampler only."""
    def __init__(self, target, directory, seconds, threshold):
        self.target, self.directory = target, Path(directory)
        self.seconds, self.threshold = seconds, threshold
        self.process = None
        self.handle = None
        self.finished = False
        self.last_log_poll_ns = None
        self.receipt = dict(schema_version=2, collection_timing=dict(complete=False, polls=0,
                            max_poll_gap_ns=0, observed_bytes=0), requested_seconds=seconds, interval_ms=10,
                            threshold_bytes=threshold, target_pid=target.pid, attempted=False)
        self.report_path = self.directory / 'caller-stack-sample.txt'
        self.receipt_path = self.directory / 'caller-stack-sample.json'
        self.analysis_path = self.directory / 'caller-stack-analysis.json'
        self.log_path = self.directory / 'caller-stack-sampler.log'
        if any(p.exists() for p in (self.report_path, self.receipt_path, self.analysis_path, self.log_path)):
            raise ValueError('stack evidence already exists')

    def __enter__(self):
        return self

    def on_progress(self, receiver):
        if self.finished:
            return
        if self.process is None:
            if receiver.verified_bytes < self.threshold:
                return
            if receiver.verified_bytes >= receiver.size or self.target.poll() is not None:
                raise RuntimeError('stack sample missed active owned caller')
            command = [str(TOOL), str(self.target.pid), str(self.seconds), '10',
                       '-mayDie', '-file', str(self.report_path)]
            self.receipt.update(attempted=True, command=command,
                                tool_sha256=hashlib.sha256(TOOL.read_bytes()).hexdigest() if TOOL.is_file() else None,
                                verified_bytes_at_request=receiver.verified_bytes,
                                begin_monotonic_ns=time.monotonic_ns(), begin_wall_ns=time.time_ns())
            self.handle = self.log_path.open('w')
            self.process = subprocess.Popen(command, stdout=self.handle, stderr=subprocess.STDOUT,
                                            start_new_session=True)
            write_json(self.receipt_path, self.receipt)
        else:
            if self._observe_log():
                write_json(self.receipt_path, self.receipt)
            if self.process.poll() is not None:
                self._collect(receiver)

    def _observe_log(self):
        """Controller polling only. Reception is an upper bound, never emission time."""
        timing = self.receipt['collection_timing']
        try:
            with self.log_path.open('rb') as handle:
                data = handle.read(MAX_LOG_BYTES + 1)
            received = time.monotonic_ns()  # after read: conservatively late
            timing['polls'] += 1
            previous = self.last_log_poll_ns or self.receipt['begin_monotonic_ns']
            timing['max_poll_gap_ns'] = max(timing['max_poll_gap_ns'], received - previous)
            self.last_log_poll_ns = received
            if len(data) > MAX_LOG_BYTES or len(data) < timing['observed_bytes']:
                raise ValueError('oversized/regressing sampler log')
            timing['observed_bytes'] = len(data)
            # Ignore incomplete trailing lines until the newline arrives.
            lines = [line.decode('utf-8') for line in data.split(b'\n')[:-1]]
            starts = [i for i, line in enumerate(lines) if line.startswith('Sampling process ')]
            ends = [i for i, line in enumerate(lines) if line.startswith('Sampling completed')]
            if len(starts) > 1 or len(ends) > 1:
                raise ValueError('duplicate collection markers')
            expected = (rf'Sampling process {self.target.pid} for {self.seconds} seconds? '
                        r'with 10 milliseconds of run time between samples')
            if starts and not re.fullmatch(expected, lines[starts[0]]):
                raise ValueError('mismatched collection-start marker')
            if ends and (not starts or ends[0] <= starts[0]
                         or lines[ends[0]] != 'Sampling completed, processing symbols...'):
                raise ValueError('missing/malformed/out-of-order collection markers')
            changed = False
            for indices, key in [(starts, 'start_marker_received_monotonic_ns'),
                                 (ends, 'completion_marker_received_monotonic_ns')]:
                if indices and key not in timing:
                    timing[key] = received
                    changed = True
            timing['complete'] = bool(starts and ends and 'error' not in timing)
            return changed
        except (OSError, ValueError) as exc:
            timing.update(complete=False, error=str(exc))
            return False

    def _collect(self, receiver):
        self._observe_log()
        self.receipt.update(returncode=self.process.returncode,
                            end_observed_monotonic_ns=time.monotonic_ns(),
                            end_observed_wall_ns=time.time_ns(),
                            target_alive_at_end=self.target.poll() is None,
                            verified_bytes_after_observed=receiver.verified_bytes if receiver else None)
        if self.process.returncode == 0:
            try:
                analysis = read_report(self.report_path, self.target.pid)
                write_json(self.analysis_path, analysis)
                self.receipt['report_complete'] = True
            except (OSError, ValueError) as exc:
                self.receipt['error'] = str(exc)
        self.finished = True
        if self.handle:
            self.handle.close()
            self.handle = None
        write_json(self.receipt_path, self.receipt)

    def finish(self, receiver):
        if not self.finished and self.process is not None:
            if self.process.poll() is None:
                # Never prolong the media call to finish sampling or signal target.
                self.receipt['aborted_at_call_end'] = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self._collect(receiver)
        final = receiver.verified_bytes if receiver else None
        r = self.receipt
        after = r.get('verified_bytes_after_observed')
        r['application_passed'] = (r['attempted'] and r.get('returncode') == 0
            and r.get('report_complete') is True and r.get('collection_timing', {}).get('complete') is True
            and r.get('target_alive_at_end') is True
            and not r.get('aborted_at_call_end', False) and type(after) is int
            and type(final) is int and r['verified_bytes_at_request'] < after < final)
        r['final_verified_bytes'] = final
        write_json(self.receipt_path, r)
        return r

    def __exit__(self, *args):
        try:
            if not self.finished and self.process is not None:
                self.finish(None)
        finally:
            if self.handle:
                self.handle.close()
                self.handle = None
