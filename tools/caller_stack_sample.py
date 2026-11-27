"""Opt-in macOS stack observations of an owned caller; not CPU-time attribution.

The external sampler can perturb scheduling. No endpoint instrumentation,
per-sample timers, deadline changes or attach-by-user-supplied-PID interface.
"""
from collections import Counter
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import time

from v34_payload import write_json

TOOL = Path('/usr/bin/sample')
MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 20000


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


class CallerStackSample:
    """Owned Popen only. Starts on verified progress; cleanup signals sampler only."""
    def __init__(self, target, directory, seconds, threshold):
        self.target, self.directory = target, Path(directory)
        self.seconds, self.threshold = seconds, threshold
        self.process = None
        self.handle = None
        self.finished = False
        self.receipt = dict(schema_version=1, requested_seconds=seconds, interval_ms=10,
                            threshold_bytes=threshold, target_pid=target.pid, attempted=False)
        self.report_path = self.directory / 'caller-stack-sample.txt'
        self.receipt_path = self.directory / 'caller-stack-sample.json'
        self.analysis_path = self.directory / 'caller-stack-analysis.json'
        if any(p.exists() for p in (self.report_path, self.receipt_path, self.analysis_path)):
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
            self.handle = (self.directory / 'caller-stack-sampler.log').open('w')
            self.process = subprocess.Popen(command, stdout=self.handle, stderr=subprocess.STDOUT,
                                            start_new_session=True)
            write_json(self.receipt_path, self.receipt)
        elif self.process.poll() is not None:
            self._collect(receiver)

    def _collect(self, receiver):
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
            and r.get('report_complete') is True and r.get('target_alive_at_end') is True
            and not r.get('aborted_at_call_end', False) and type(after) is int
            and type(final) is int and r['verified_bytes_at_request'] < after < final)
        r['final_verified_bytes'] = final
        write_json(self.receipt_path, r)
        return r

    def __exit__(self, *args):
        if not self.finished and self.process is not None:
            self.finish(None)
        if self.handle:
            self.handle.close()
            self.handle = None
