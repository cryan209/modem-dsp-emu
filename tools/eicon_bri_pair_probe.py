"""Bounded stock-firmware TE/NT call setup, answer and teardown probe.

Run the actual board firmware and report where CALL_REQ gets to. Never
synthesize CALL_IND/CONNECT or replace signalling/DSP firmware routines.
"""
import argparse
import json
import struct
from pathlib import Path
from eicon_bri_card import BriCard
from eicon_bri_machine import BriMachine
from eicon_bri_host import BriHost, BriDataSender, BriModemSignals
import eicon_idi as idi


def serializable(entries):
    return [dict(entry, **({'payload': entry['payload'].hex()} if 'payload' in entry else {}))
            for entry in entries]


def probe(firmware_dir, wait_instructions, profile=1, answer=False, hangup=False, pair=0, network=False, raw_sport=False, payload=None, dump_dsps=None, sport_delay_words=2, modulation=None, law='pcmu', progress=None, card_factory=None):
    if wait_instructions <= 0:
        raise ValueError('wait_instructions must be positive')
    if modulation and profile:
        raise ValueError('modulation selection requires modem profile 0')
    if law not in ('pcmu', 'pcma'):
        raise ValueError('law must be pcmu or pcma')
    if pair not in (0, 1, 'both'):
        raise ValueError("pair must be 0, 1 or 'both'")
    pairs = ((0, 1), (2, 3)) if pair == 'both' else ((pair * 2, pair * 2 + 1),)
    callers, receivers = {a for a, b in pairs}, {b for a, b in pairs}
    participants = callers | receivers
    peers = {endpoint: peer for a, b in pairs for endpoint, peer in ((a, b), (b, a))}
    if payload is not None:
        if not 1 <= len(payload) <= 2044:
            raise ValueError('probe payload must contain 1..2044 bytes')
        network = True
    answer = answer or hangup or network
    with (card_factory or BriCard)(reentrant=True) as card:
        card.load_reentrant(firmware_dir / 'te_dmlt.2qm', firmware_dir / 'dspdload.bin')
        for i in range(4):
            card.configure_port(i, 'nt' if i & 1 else 'te', law=law)
        card.ipacs[0].connect(card.ipacs[1])
        card.ipacs[2].connect(card.ipacs[3])
        if raw_sport:
            for caller, receiver in pairs:
                for offset in (0, 1):
                    card.connect_sport1(caller * 2 + offset, receiver * 2 + offset, latency_words=sport_delay_words)
        machine = BriMachine(card)
        card.write(3, 0x200, 4, 3)
        result = machine.run(20000000)
        result['completed'] = False
        if result['fault'] or not all(c['signature'] == 0x4447 for c in result['board']['controllers']):
            return result
        options = idi.IsdnOptions(profile) if profile else None
        modem_options = idi.select_modulation(modulation, automode=0) if modulation else None
        # CAPI add_b1 advertises MaxDataLength in CAI as well as DLC.
        # A zero CAI limit lets the modem train and transmit, but the stock
        # MIPS receiver discards assembled characters as receive overflows.
        modem_max_frame = 2048
        def nl_payload(entity, answering=True):
            if options:
                return idi.isdn_nl_assign_payload(options, answering=answering, signaling_id=entity)
            return idi.transparent_modem_nl_assign_payload(signaling_id=entity,
                answering=answering, max_data_length=2048, lli=idi.LLI_DEFAULT)
        sig_payload = (idi.idi_parameters((idi.IDI_CAI, idi.isdn_cai(options)),
                                           (idi.IDI_UID, b'Capi20'))
                       if options else idi.sig_assign_payload(modem_options, max_frame=modem_max_frame))
        hosts = [BriHost(card, i) for i in range(4)]
        for index, host in enumerate(hosts):
            host.request(idi.DSIG_ID, idi.ASSIGN, parameters=(
                idi.listener_sig_assign_payload() if index in receivers else sig_payload))
        result = machine.run(2000000)
        returns = [host.collect_returns() for host in hosts]
        if result['fault'] or any(len(rc) != 1 or rc[0]['code'] != idi.ASSIGN_OK for rc in returns):
            return dict(result, completed=False, assign=returns)
        ids = [rc[0]['entity'] for rc in returns]
        for index, (host, entity) in enumerate(zip(hosts, ids)):
            if index & 1:
                host.request(entity, idi.INDICATE_REQ, parameters=idi.indicate_req_payload())
        result = machine.run(2000000)
        listening = [host.collect_returns() for host in hosts]
        if result['fault']:
            return dict(result, completed=False, listen=listening)
        receiver_nl_assign = []
        nl_ids = {}
        for receiver in sorted(receivers):
            if not hosts[receiver].request(idi.NL_ID, idi.ASSIGN,
                    parameters=nl_payload(ids[receiver])):
                raise RuntimeError('no host request buffer for receiver NL ASSIGN')
        result = machine.run(1000000)
        for receiver in sorted(receivers):
            rc = hosts[receiver].collect_returns()
            receiver_nl_assign.extend(rc)
            if result['fault'] or len(rc) != 1 or rc[0]['code'] != idi.ASSIGN_OK:
                return dict(result, completed=False, stage='receiver-network-assignment',
                            receiver_nl_assign=receiver_nl_assign)
            nl_ids[receiver] = rc[0]['entity']
        for caller in sorted(callers):
            if not hosts[caller].request(ids[caller], idi.CALL_REQ, parameters=(
                    idi.isdn_call_req_payload('1234', options) if options else idi.call_req_payload('1234', options=modem_options, max_frame=modem_max_frame))):
                raise RuntimeError('no host request buffer for CALL_REQ')
            if not hosts[caller].request(idi.NL_ID, idi.ASSIGN,
                    parameters=nl_payload(ids[caller], answering=False)):
                raise RuntimeError('no host request buffer for caller NL ASSIGN')
        senders = {}
        data_posted = False
        data_started_at = None
        received = {index: bytearray() for index in sorted(participants)}
        expected = {index: b'BRI' + bytes((peers[index],)) + (payload or b'')
                    for index in sorted(participants)}
        network_ack_pending = {}
        network_started = set()
        network_connected = set()
        modem_signals = {index: BriModemSignals() for index in participants}
        events = []
        spent = 0
        completed = False
        hangup_posted = False
        disconnected = set()
        rejected = {}
        answered = set()
        call_seen = set()
        connected = set()
        while spent < wait_instructions:
            step = min(2000000, wait_instructions - spent)
            result = machine.run(step)
            spent += step
            for index, host in enumerate(hosts):
                rc, indications = host.collect_returns(), host.collect_indications()
                if rc or indications:
                    events.append(dict(controller=index, instructions=spent,
                                       returns=rc, indications=serializable(indications)))
                if index in senders:
                    senders[index].observe_returns(rc)
                if index in network_ack_pending and any(
                        entry['entity'] == nl_ids.get(index) and entry['code'] == idi.RC_OK
                        and entry['reference'] == network_ack_pending[index]
                        for entry in rc):
                    network_connected.add(index)
                    del network_ack_pending[index]
                if index in callers:
                    for entry in rc:
                        if entry['code'] == idi.ASSIGN_OK:
                            nl_ids[index] = entry['entity']
                for entry in indications:
                    if network and entry['entity'] == nl_ids.get(index):
                        if not options and entry['code'] == idi.N_UDATA:
                            modem_signals[index].observe(entry['payload'])
                        if data_posted and entry['code'] in (1, idi.N_DATA):
                            received[index].extend(entry['payload'])
                        if entry['code'] == idi.N_CONNECT:
                            network_started.add(index)
                            if not host.request(entry['entity'], idi.N_CONNECT_ACK,
                                                channel=entry['channel']):
                                raise RuntimeError('no host request buffer for N_CONNECT_ACK')
                            network_ack_pending[index] = host.last_reference
                        if entry['code'] == idi.N_CONNECT_ACK:
                            network_connected.add(index)
                        if entry['code'] == idi.N_DISC:
                            if not host.request(entry['entity'], idi.N_DISC_ACK, channel=entry['channel']):
                                raise RuntimeError('no host request buffer for N_DISC_ACK')
                    was_answered = index in answered
                    if index in receivers and entry['entity'] == ids[index] and entry['code'] in (idi.CALL_IND, idi.INDICATE_IND):
                        call_seen.add(index)
                        if answer and index not in answered:
                            answer_payload = (idi.idi_parameters((idi.IDI_CAI, idi.isdn_cai(options)))
                                       if options else idi.call_res_payload(modem_options, max_frame=modem_max_frame))
                            if not host.request(ids[index], idi.CALL_RES,
                                                channel=entry['channel'], parameters=answer_payload):
                                raise RuntimeError('no host request buffer for CALL_RES')
                            answered.add(index)
                    if entry['entity'] == ids[index] and (
                            (index in callers and entry['code'] == idi.CALL_CON) or
                            (index in receivers and was_answered and entry['code'] == idi.CALL_IND)):
                        connected.add(index)
                    if entry['entity'] == ids[index] and entry['code'] == idi.HANGUP:
                        if hangup_posted:
                            disconnected.add(index)
                        else:
                            rejected[index] = serializable([entry])[0]
                completed = connected.issuperset(participants) if answer else call_seen.issuperset(receivers)
            if network and connected.issuperset(participants):
                for index in sorted(participants):
                    if index in nl_ids and index not in network_started:
                        if not hosts[index].request(nl_ids[index], idi.N_CONNECT):
                            raise RuntimeError('no host request buffer for N_CONNECT')
                        network_started.add(index)
                completed = network_connected.issuperset(participants)
            modem_ready = bool(options) or all(state.ready for state in modem_signals.values())
            if payload is not None and (data_posted or
                    network_connected.issuperset(participants) and modem_ready):
                if not data_posted:
                    for index, peer in sorted(peers.items()):
                        sender = BriDataSender(hosts[index], nl_ids[index], max_frame=options.max_frame if options else 2048)
                        sender.queue(expected[peer])
                        senders[index] = sender
                    data_posted = True
                    data_started_at = spent
                for index, sender in senders.items():
                    if options or modem_signals[index].ready:
                        sender.pump()
                completed = all(bytes(received[index]) == expected[index] and senders[index].idle
                                for index in participants)
            if payload is not None and not data_posted:
                completed = False
            if hangup and connected.issuperset(participants) and (not network or completed):
                if not hangup_posted:
                    if network:
                        for index in sorted(participants):
                            if not hosts[index].request(nl_ids[index], idi.N_DISC, channel=1):
                                raise RuntimeError('no host request buffer for N_DISC')
                    for caller in sorted(callers):
                        if not hosts[caller].request(ids[caller], idi.HANGUP, parameters=b'\0'):
                            raise RuntimeError('no host request buffer for HANGUP')
                    hangup_posted = True
                completed = disconnected.issuperset(participants)
            if progress is not None:
                progress(dict(instructions=spent, fault=result['fault'],
                    connected=sorted(connected), network_connected=sorted(network_connected),
                    data_posted=data_posted, rejected=sorted(rejected), received_bytes={i: len(v) for i, v in received.items()},
                    modem_signals={i: state.report() for i, state in modem_signals.items()},
                    clock=result['clock'], dsps=result['board']['dsp_state']))
            if rejected:
                completed = False
                break
            if result['fault'] or completed:
                break
        log_base = struct.unpack_from('<I', card.ram, 0x70)[0] & 0x1fffffff
        records = []
        offset = 0
        while len(records) < 512 and log_base + offset + 8 < len(card.ram):
            end = struct.unpack_from('<H', card.ram, log_base + offset)[0]
            if not offset < end <= 0x4000:
                break
            data = bytes(card.ram[log_base + offset + 8:log_base + end])
            records.append(dict(offset=offset, header=bytes(card.ram[log_base+offset:log_base+offset+8]).hex(),
                                data=data.hex()))
            offset = (end + 1) & ~1
        result['log_records'] = records
        result['controller_logs'] = {}
        for controller in range(4):
            base = card.ram.find(f'Instance({controller})='.encode()) - 8
            offset = 0
            logs = []
            if base >= 0:
                while len(logs) < 512 and base + offset + 8 < len(card.ram):
                    end = struct.unpack_from('<H', card.ram, base + offset)[0]
                    if not offset < end <= 0x4000:
                        break
                    logs.append(bytes(card.ram[base+offset+8:base+end]).hex())
                    offset = (end + 1) & ~1
            result['controller_logs'][str(controller)] = logs
        if dump_dsps is not None:
            for index, core in card.dsps.items():
                core.dump(Path(dump_dsps) / f'dsp-{index}')
        if rejected:
            stage = 'call-rejected'
        elif result['fault']:
            stage = 'device-fault'
        elif completed:
            stage = ('call-cleared' if hangup else 'bearer-payload' if payload is not None else
                     'network-connected' if network else 'call-connected' if answer else 'peer-call-indication')
        elif payload is not None and data_posted:
            stage = 'waiting-for-bearer-payload'
        elif payload is not None and not options and network_connected.issuperset(participants) and not modem_ready:
            stage = 'waiting-for-modem-signals'
        elif network and connected.issuperset(participants):
            stage = 'waiting-for-network-connect'
        elif call_seen.issuperset(receivers):
            stage = 'waiting-for-call-connect'
        else:
            stage = 'waiting-for-peer-call-indication'
        if hangup_posted and not completed:
            stage = 'waiting-for-call-clear'
        result.update(configuration=dict(profile=profile, modulation=modulation, law=law,
                                         max_frame=options.max_frame if options else modem_max_frame,
                                         listener_assignment=idi.listener_sig_assign_payload().hex()),
                      completed=completed, events=events, listen=listening,
                      frames=[[frame.hex() for frame in device.transmitted] for device in card.ipacs],
                      receiver_nl_assign=receiver_nl_assign,
                      senders={index: dict(accepted_bytes=sender.accepted_bytes,
                                  posted_fragments=sender.posted_fragments, idle=sender.idle,
                                  flow_controlled=sender.flow_controlled)
                               for index, sender in senders.items()},
                      data_posted=data_posted, data_started_at=data_started_at,
                      received={i: bytes(v).hex() for i, v in received.items()},
                      expected={i: v.hex() for i, v in expected.items()} if payload is not None else {},
                      network_connected=sorted(network_connected), nl_ids=nl_ids,
                      modem_signals={i: state.report() for i, state in modem_signals.items()},
                      pair=pair, call_seen=call_seen.issuperset(receivers), answered=answered.issuperset(receivers),
                      incoming_controllers=sorted(call_seen), answered_controllers=sorted(answered),
                      connected=sorted(connected),
                      rejected=rejected, hangup_posted=hangup_posted, disconnected=sorted(disconnected),
                      stage=stage)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path, default=Path('docs/firmware'))
    parser.add_argument('--wait-instructions', type=int, default=10000000)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', type=int, default=1, help='native ISDN profile, or 0 for modem')
    parser.add_argument('--answer', action='store_true', help='answer CALL_IND and require both signalling confirmations')
    parser.add_argument('--hangup', action='store_true', help='answer, connect, then require both firmware disconnect indications')
    parser.add_argument('--pair', choices=('0', '1', 'both'), default='0',
                        help='TE/NT controller pair: 0/1, 2/3, or both concurrently')
    parser.add_argument('--network', action='store_true', help='require actual N_CONNECT_ACK from both firmware network entities')
    parser.add_argument('--raw-sport', action='store_true', help='experimental matching-DSP raw-word peer wiring; physical slot mapping is unqualified')
    parser.add_argument('--sport-delay-words', type=int, choices=range(1, 65), default=2,
                        metavar='1..64', help='raw SPORT peer delay; two words align the stock kernel receive slot')
    parser.add_argument('--payload', help='UTF-8 bearer probe payload; require exact delivery in both directions')
    parser.add_argument('--dump-dsps', type=Path, help='save actual PM/DM per DSP for offline disassembly')
    parser.add_argument('--modulation', help='modem modulation with automode disabled; use with --profile 0')
    parser.add_argument('--law', choices=('pcmu', 'pcma'), default='pcmu')
    parser.add_argument('--progress', type=Path, help='write bounded-run progress as JSON lines')
    args = parser.parse_args()
    if args.modulation and args.profile:
        parser.error('--modulation requires --profile 0')
    if args.wait_instructions <= 0:
        parser.error('--wait-instructions must be positive')
    def progress(state):
        with args.progress.open('a') as output:
            output.write(json.dumps(state) + '\n')
    if args.progress is not None:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text('')
    result = probe(args.firmware_dir, args.wait_instructions, args.profile, args.answer or args.hangup, args.hangup, int(args.pair) if args.pair != 'both' else 'both', args.network, args.raw_sport,
                   args.payload.encode() if args.payload is not None else None, args.dump_dsps, args.sport_delay_words, args.modulation, args.law, progress if args.progress is not None else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result.get(key) for key in ('completed', 'stage', 'fault')}))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
