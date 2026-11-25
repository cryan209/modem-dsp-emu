"""Exercise both B channels on all four controllers with stock board firmware.

Only BAR/IDI queues and explicit SPORT peers are used. Entity IDs, channel
selection, call responses and bearer frames come from the shipping firmware.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from eicon_bri_card import BriCard
from eicon_bri_machine import BriMachine
from eicon_bri_host import BriHost, BriDataSender, BriModemSignals
from eicon_bri_pair_probe import serializable
import eicon_idi as idi


@dataclass
class Endpoint:
    index: int
    controller: int
    slot: int
    caller: bool
    sig: int | None = None
    nl: int | None = None
    peer: int | None = None
    sig_channel: int = 0
    channel: int = 0
    bearer: int | None = None
    started: bool = False
    listening: bool = False
    listen_requests: int = 0
    listen_reference: int | None = None
    answered: bool = False
    connected: bool = False
    network_started: bool = False
    network_connected: bool = False
    ack_reference: int | None = None
    cleared: bool = False
    received: bytearray = field(default_factory=bytearray)
    expected: bytes = b''
    sender: object = None
    modem_signals: BriModemSignals = field(default_factory=BriModemSignals)

    def report(self):
        return dict(index=self.index, controller=self.controller, slot=self.slot,
                    caller=self.caller, sig=self.sig, nl=self.nl, peer=self.peer,
                    started=self.started, listen_requests=self.listen_requests,
                    sig_channel=self.sig_channel, channel=self.channel, bearer=self.bearer,
                    connected=self.connected, network_connected=self.network_connected,
                    cleared=self.cleared, received=bytes(self.received).hex(),
                    expected=self.expected.hex(),
                    accepted_bytes=self.sender.accepted_bytes if self.sender else 0,
                    posted_fragments=self.sender.posted_fragments if self.sender else 0,
                    sender_idle=self.sender.idle if self.sender else False,
                    modem_signals=self.modem_signals.report())


def probe(firmware_dir, wait_instructions=42000000, payload=b'full-card', profile=9,
          *, modulation=None, law='pcmu', progress=None):
    if wait_instructions <= 0 or not 1 <= len(payload) <= 2044:
        raise ValueError('positive budget and 1..2044 payload bytes required')
    if modulation and profile:
        raise ValueError('modulation selection requires modem profile 0')
    if law not in ('pcmu', 'pcma'):
        raise ValueError('law must be pcmu or pcma')
    options = idi.IsdnOptions(profile) if profile else None
    modem_options = idi.select_modulation(modulation, automode=0) if modulation else None
    max_frame = options.max_frame if options else 2048
    def nl_payload(endpoint, answering=True):
        if options:
            return idi.isdn_nl_assign_payload(options, answering=answering,
                                              signaling_id=endpoint.sig)
        return idi.transparent_modem_nl_assign_payload(max_data_length=max_frame,
                                     answering=answering, signaling_id=endpoint.sig,
                                     lli=idi.LLI_DEFAULT)
    endpoints = [Endpoint(i * 2 + slot, i, slot, not bool(i & 1))
                 for i in range(4) for slot in range(2)]
    numbers = {f'700{e.index}': e for e in endpoints if e.caller}
    pending_assign = {}
    entities = {}
    events = []
    spent = 0
    with BriCard(reentrant=True) as card:
        card.load_reentrant(firmware_dir / 'te_dmlt.2qm', firmware_dir / 'dspdload.bin')
        for controller in range(4):
            card.configure_port(controller, 'nt' if controller & 1 else 'te', law=law)
        for controller in (0, 2):
            card.ipacs[controller].connect(card.ipacs[controller + 1])
            for offset in (0, 1):
                card.connect_sport1(controller * 2 + offset,
                                    (controller + 1) * 2 + offset, latency_words=2)
        machine = BriMachine(card)
        hosts = [BriHost(card, i) for i in range(4)]
        card.write(3, 0x200, 4, 3)
        result = machine.run(20000000)
        if result['fault'] or not all(host.ready for host in hosts):
            return dict(result, completed=False, stage='boot')

        def post(endpoint, entity, code, parameters=b'', channel=0):
            host = hosts[endpoint.controller]
            if not host.request(entity, code, channel=channel, parameters=parameters):
                raise RuntimeError('no host request buffer')
            return host.last_reference

        def assign(endpoint, kind, parameters):
            entity = idi.DSIG_ID if kind == 'sig' else idi.NL_ID
            reference = post(endpoint, entity, idi.ASSIGN, parameters)
            pending_assign[endpoint.controller, reference] = endpoint, kind

        def listen(endpoint):
            endpoint.listen_requests += 1
            endpoint.listening = False
            endpoint.listen_reference = post(endpoint, endpoint.sig, idi.INDICATE_REQ, idi.indicate_req_payload())

        def start_call(endpoint):
            endpoint.started = True
            endpoint.cleared = False
            post(endpoint, endpoint.sig, idi.CALL_REQ,
                 idi.isdn_call_req_payload(f'700{endpoint.index}', options) if options else
                 idi.call_req_payload(f'700{endpoint.index}', options=modem_options,
                                      max_frame=max_frame))
            assign(endpoint, 'nl', nl_payload(endpoint, answering=False))

        def pump(instructions):
            nonlocal result, spent
            result = machine.run(instructions)
            spent += instructions
            for controller, host in enumerate(hosts):
                returns, indications = host.collect_returns(), host.collect_indications()
                if returns or indications:
                    events.append(dict(controller=controller, instructions=spent,
                                       returns=returns, indications=serializable(indications)))
                for entry in returns:
                    key = controller, entry['reference']
                    if key in pending_assign:
                        endpoint, kind = pending_assign.pop(key)
                        if entry['code'] != idi.ASSIGN_OK:
                            raise RuntimeError(f'{kind} ASSIGN failed: {entry}')
                        setattr(endpoint, kind, entry['entity'])
                        entities[controller, entry['entity']] = endpoint, kind
                    match = entities.get((controller, entry['entity']))
                    if match is None:
                        continue
                    endpoint, kind = match
                    if kind == 'sig' and endpoint.listen_reference == entry['reference']:
                        if entry['code'] != idi.RC_OK:
                            raise RuntimeError(f'INDICATE_REQ rejected: {entry}')
                        endpoint.listening = True
                        endpoint.listen_reference = None
                    if kind == 'nl':
                        if endpoint.sender:
                            endpoint.sender.observe_returns([entry])
                        if endpoint.ack_reference == entry['reference']:
                            if entry['code'] != idi.RC_OK:
                                raise RuntimeError(f'N_CONNECT_ACK rejected: {entry}')
                            endpoint.network_connected = True
                            endpoint.ack_reference = None
                for entry in indications:
                    match = entities.get((controller, entry['entity']))
                    if match is None:
                        raise RuntimeError(f'indication for unknown entity: {entry}')
                    endpoint, kind = match
                    code = entry['code']
                    if kind == 'sig':
                        parameters = idi.parse_idi_parameters(entry['payload'])
                        bearer = next((data[-1] & 3 for key, data in parameters
                                       if key == idi.IDI_CHI and data and data[-1] & 3 in (1, 2)), None)
                        if not endpoint.caller and code == idi.INDICATE_IND:
                            endpoint.listening = False
                            called = next((data[1:].decode('ascii') for key, data in parameters
                                           if key == idi.IDI_CPN), None)
                            caller = numbers.get(called)
                            if caller is None or caller.controller + 1 != controller:
                                raise RuntimeError(f'unexpected called number {called!r}')
                            # INDICATE notifications are broadcast to listeners.
                            # Claim each call once, on an available notified entity.
                            if caller.peer is not None or endpoint.answered:
                                continue
                            endpoint.bearer = bearer
                            endpoint.started = True
                            caller.peer, endpoint.peer = endpoint.index, caller.index
                            endpoint.sig_channel = entry['channel']
                            post(endpoint, endpoint.sig, idi.CALL_RES,
                                 idi.idi_parameters((idi.IDI_CAI, idi.isdn_cai(options))) if options else
                                 idi.call_res_payload(modem_options, max_frame=max_frame),
                                 endpoint.sig_channel)
                            endpoint.answered = True
                        elif (endpoint.caller and code == idi.CALL_CON or
                              not endpoint.caller and endpoint.answered and code == idi.CALL_IND):
                            endpoint.sig_channel = entry['channel']
                            if bearer is not None:
                                endpoint.bearer = bearer
                            endpoint.connected = True
                        elif code == idi.HANGUP:
                            if not endpoint.caller and not endpoint.answered:
                                listen(endpoint)
                            else:
                                endpoint.cleared = True
                    else:
                        if not options and code == idi.N_UDATA:
                            endpoint.modem_signals.observe(entry['payload'])
                        if code in (idi.N_CONNECT, idi.N_CONNECT_ACK):
                            endpoint.channel = entry['channel']
                            endpoint.network_started = True
                            if code == idi.N_CONNECT:
                                endpoint.ack_reference = post(endpoint, endpoint.nl,
                                    idi.N_CONNECT_ACK, channel=entry['channel'])
                            else:
                                endpoint.network_connected = True
                        elif code == idi.N_DISC:
                            post(endpoint, endpoint.nl, idi.N_DISC_ACK, channel=entry['channel'])
                        elif code in (1, idi.N_DATA):
                            endpoint.received.extend(entry['payload'])

        sig_payload = (idi.idi_parameters((idi.IDI_CAI, idi.isdn_cai(options)),
                                         (idi.IDI_UID, b'Capi20')) if options else
                       idi.sig_assign_payload(modem_options, max_frame=max_frame))
        for endpoint in endpoints:
            assign(endpoint, 'sig', sig_payload if endpoint.caller else idi.listener_sig_assign_payload())
        pump(2000000)
        if pending_assign or result['fault']:
            return dict(result, completed=False, stage='signalling-assignment', events=events)
        for endpoint in endpoints:
            if not endpoint.caller:
                listen(endpoint)
                assign(endpoint, 'nl', nl_payload(endpoint))
        pump(2000000)
        if pending_assign or result['fault']:
            return dict(result, completed=False, stage='receiver-assignment', events=events)
        for endpoint in endpoints:
            if endpoint.caller and endpoint.slot == 0:
                start_call(endpoint)
        peak_active_bearers = 0
        data_started_at = None
        probe_error = None
        data_posted = hangup_posted = completed = False
        while spent < wait_instructions:
            try:
                pump(min(2000000, wait_instructions - spent))
            except (RuntimeError, ValueError) as exc:
                probe_error = str(exc)
                break
            if result['fault']:
                break
            peak_active_bearers = max(peak_active_bearers,
                sum(e.connected and not e.cleared for e in endpoints))
            for endpoint in endpoints:
                if endpoint.caller and not endpoint.started:
                    previous = endpoints[endpoint.index - 1]
                    available = any(e.controller == endpoint.controller + 1 and e.listening
                                    and not e.answered for e in endpoints)
                    if previous.connected and available:
                        start_call(endpoint)
                if endpoint.connected and endpoint.nl is not None:
                    if not endpoint.network_started:
                        post(endpoint, endpoint.nl, idi.N_CONNECT)
                        endpoint.network_started = True
            modem_ready = bool(options) or all(e.modem_signals.ready for e in endpoints)
            if data_posted or (modem_ready and all(e.network_connected and not e.cleared for e in endpoints)):
                if not data_posted:
                    for endpoint in endpoints:
                        if endpoint.peer is None:
                            raise RuntimeError('connected call has no matched peer')
                        endpoint.expected = b'BRI' + bytes((endpoint.peer,)) + payload
                        endpoint.sender = BriDataSender(hosts[endpoint.controller], endpoint.nl,
                                                        channel=endpoint.channel, max_frame=max_frame)
                        endpoint.sender.queue(b'BRI' + bytes((endpoint.index,)) + payload)
                    data_started_at = spent
                    data_posted = True
                for endpoint in endpoints:
                    if options or endpoint.modem_signals.ready:
                        endpoint.sender.pump()
                delivered = all(bytes(e.received) == e.expected and e.sender.idle for e in endpoints)
                if delivered and not hangup_posted:
                    for endpoint in endpoints:
                        post(endpoint, endpoint.nl, idi.N_DISC, channel=endpoint.channel)
                        if endpoint.caller:
                            post(endpoint, endpoint.sig, idi.HANGUP, b'\0', endpoint.sig_channel)
                    hangup_posted = True
            if progress is not None:
                progress(dict(instructions=spent, fault=result['fault'],
                              connected=sum(e.connected and not e.cleared for e in endpoints),
                              network_connected=sum(e.network_connected for e in endpoints),
                              data_posted=data_posted,
                              modem_signals={e.index: e.modem_signals.report() for e in endpoints},
                              received_bytes={e.index: len(e.received) for e in endpoints}))
            if hangup_posted and all(e.cleared for e in endpoints):
                completed = True
                break
        bearer_channels = {i: sorted(e.bearer for e in endpoints if e.controller == i and e.bearer is not None)
                           for i in range(4)}
        completed = completed and peak_active_bearers == 8 and all(channels == [1, 2] for channels in bearer_channels.values())
        stage = ('all-bearers-cleared' if completed else 'waiting-for-teardown' if hangup_posted else
                 'waiting-for-payload' if data_posted else 'waiting-for-network' if all(e.connected for e in endpoints)
                 else 'waiting-for-calls')
        if probe_error is not None:
            stage = 'host-protocol-error'
        elif result['fault'] is not None:
            stage = 'device-fault'
        elif not data_posted and not options and all(e.network_connected for e in endpoints) and not modem_ready:
            stage = 'waiting-for-modem-signals'
        elif hangup_posted and all(e.cleared for e in endpoints) and not completed:
            stage = 'channel-selection-unverified'
        return dict(result, completed=completed, stage=stage, probe_error=probe_error, events=events,
                    configuration=dict(profile=profile, modulation=modulation, law=law,
                                       max_frame=max_frame, sport_delay_words=2),
                    frames=[[frame.hex() for frame in device.transmitted] for device in card.ipacs],
                    endpoints=[e.report() for e in endpoints], bearer_channels=bearer_channels,
                    data_posted=data_posted, data_started_at=data_started_at,
                    peak_active_bearers=peak_active_bearers, hangup_posted=hangup_posted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path, default=Path('docs/firmware'))
    parser.add_argument('--wait-instructions', type=int, default=42000000)
    parser.add_argument('--profile', type=int, default=9)
    parser.add_argument('--modulation', help='forced modem modulation for profile 0')
    parser.add_argument('--law', choices=('pcmu', 'pcma'), default='pcmu')
    parser.add_argument('--payload', default='full-card')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--progress', type=Path, help='write periodic JSON-line progress')
    args = parser.parse_args()
    if args.progress:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text('')
    def progress(state):
        if args.progress:
            with args.progress.open('a') as stream:
                stream.write(json.dumps(state) + '\n')
    result = probe(args.firmware_dir, args.wait_instructions, args.payload.encode(), args.profile,
                   modulation=args.modulation, law=args.law,
                   progress=progress if args.progress else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result.get(key) for key in ('completed', 'stage', 'fault', 'probe_error', 'peak_active_bearers', 'bearer_channels')}))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
