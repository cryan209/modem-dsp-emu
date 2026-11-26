"""Bounded receipts for an opt-in, fresh-input-only V.34 recovery experiment.

No policy is enabled by importing this module. Endpoint code supplies *current*
firmware/link state at a between-quanta decision, never a cached data-mode flag.
"""
from collections import Counter, deque
from pathlib import Path


def configuration_supported(args, environment):
    """Endpoint modulation is configured by environment, not argparse."""
    return (bool(args.ppp or args.ppp_client) and args.media_trace_dir is not None
            and args.bind == '127.0.0.1' and args.realtime
            and environment.get('EICON_MODULATION', '').split(',')[0].strip().lower() == 'v34'
            and not args.native_mips and not args.bri_kernel_dispatch)


class DataRxResume:
    def __init__(self, capacity=128):
        if capacity < 1:
            raise ValueError('capacity must be positive')
        self.capacity = capacity
        self.events = deque(maxlen=capacity)
        self.denials = deque(maxlen=capacity)
        self.reasons = Counter()
        self.attempts = self.resumes = 0
        self.closed = False

    def decide(self, call, now, *, current_trn, ppp_up, lapm_ready):
        if self.closed:
            raise RuntimeError('RX resume receipt already closed')
        # Caller establishes the candidate window; keep it independently checked.
        if not (call.rx_started and call.packets > 0 and call.rx_hold_until is not None
                and 160 <= len(call.rx) < 320):
            raise ValueError('not a held full-quantum candidate')
        reason = ('not_data' if current_trn != 0x00d0 else
                  'ppp_down' if ppp_up is not True else
                  'lapm_down' if lapm_ready is not True else 'resumed')
        self.attempts += 1
        self.reasons[reason] += 1
        event = dict(time=now, current_trn=current_trn, ppp_up=ppp_up,
                     lapm_ready=lapm_ready, rx_samples=len(call.rx),
                     samples=call.samples, next_send=call.next_send,
                     next_tick=call.next_tick, tx_packets=len(call.tx_queue),
                     rx_hold_until=call.rx_hold_until, reason=reason)
        if reason == 'resumed':
            self.resumes += 1
            self.events.append(event)
            return True
        self.denials.append(event)
        return False

    def result(self):
        return dict(schema_version=1, closed=self.closed, attempts=self.attempts,
                    resumes=self.resumes, reasons=dict(self.reasons), capacity=self.capacity,
                    events=list(self.events), denials=list(self.denials),
                    dropped_events=self.resumes - len(self.events),
                    dropped_denials=self.attempts - self.resumes - len(self.denials))

    def dump(self, path):
        from v34_payload import write_json
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.closed = True
        write_json(path, self.result())


def valid_receipt(receipt, logged_resumes):
    if not isinstance(receipt, dict):
        return False
    n, a, cap = (receipt.get(k) for k in ('resumes', 'attempts', 'capacity'))
    if (any(type(v) is not int for v in (n, a, cap, logged_resumes))
            or not 0 <= n <= a or cap < 1 or n != logged_resumes):
        return False
    events, denials, reasons = (receipt.get(k) for k in ('events', 'denials', 'reasons'))
    if (not isinstance(events, list) or not isinstance(denials, list) or not isinstance(reasons, dict)
            or not all(isinstance(e, dict) for e in events + denials)):
        return False
    if (set(reasons) - {'resumed', 'not_data', 'ppp_down', 'lapm_down'}
            or any(type(v) is not int or v < 0 for v in reasons.values())):
        return False
    return (receipt.get('schema_version') == 1 and receipt.get('closed') is True
            and sum(reasons.values()) == a and reasons.get('resumed', 0) == n
            and len(events) == min(n, cap) and len(denials) == min(a - n, cap)
            and receipt.get('dropped_events') == n - len(events)
            and receipt.get('dropped_denials') == a - n - len(denials)
            and all(e.get('reason') == 'resumed' and e.get('current_trn') == 0xd0
                    and e.get('ppp_up') is True and e.get('lapm_ready') is True
                    and type(e.get('rx_samples')) is int and 160 <= e['rx_samples'] < 320
                    for e in events)
            and all(e.get('reason') in ('not_data', 'ppp_down', 'lapm_down') for e in denials))
