"""Execute shipping BRI firmware through board registers and host queues.

Requires Unicorn JIT execution permission on macOS. No boot or ASSIGN
responses are synthesized by the test or board model.
"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_card import BriCard
from eicon_bri_host import BriHost
from eicon_bri_machine import BriMachine
import eicon_idi as idi

ROOT = Path(__file__).resolve().parents[1]


class BriMachineTest(unittest.TestCase):
    def test_stock_boot_and_four_controller_entity_lifecycle(self):
        with BriCard(reentrant=True) as card:
            card.load_reentrant(ROOT / 'docs/firmware/te_dmlt.2qm',
                                ROOT / 'docs/firmware/dspdload.bin')
            machine = BriMachine(card)
            with self.assertRaises(RuntimeError):
                machine.run(10)
            card.write(3, 0x200, 4, 1)
            card.write(3, 0x200, 4, 3)
            result = machine.run(20000000)
            self.assertIsNone(result['fault'], result['fault'])
            self.assertEqual(len(card.dsps), 8)
            from eicon_4bri_boot import xlog
            self.assertTrue(any('Diva Server 4BRI-8M 2.0 (2620)' in text
                                for _, text in xlog(machine)))
            self.assertGreater(result['clock']['timer_interrupts'], 0)
            self.assertGreater(result['clock']['device_interrupts'], 0)
            hosts = [BriHost(card, i) for i in range(4)]
            self.assertTrue(all(host.ready for host in hosts))
            for host in hosts:
                self.assertTrue(host.request(idi.DSIG_ID, idi.ASSIGN,
                                             parameters=idi.sig_assign_payload()))
            result = machine.run(2000000)
            self.assertIsNone(result['fault'], result['fault'])
            sig_ids = []
            for host in hosts:
                returns = host.collect_returns()
                self.assertEqual(len(returns), 1)
                self.assertEqual(returns[0]['code'], idi.ASSIGN_OK)
                sig_ids.append(returns[0]['entity'])
                self.assertEqual(host.collect_returns(), [])
            for host, entity in zip(hosts, sig_ids):
                self.assertTrue(host.request(idi.NL_ID, idi.ASSIGN,
                    parameters=idi.nl_assign_payload(signaling_id=entity)))
            result = machine.run(2000000)
            self.assertIsNone(result['fault'], result['fault'])
            nl_ids = []
            for host in hosts:
                returns = host.collect_returns()
                self.assertEqual(len(returns), 1)
                self.assertEqual(returns[0]['code'], idi.ASSIGN_OK)
                nl_ids.append(returns[0]['entity'])
            for host, entity in zip(hosts, nl_ids):
                self.assertTrue(host.request(entity, idi.REMOVE))
            result = machine.run(2000000)
            self.assertIsNone(result['fault'], result['fault'])
            for host in hosts:
                returns = host.collect_returns()
                self.assertEqual(len(returns), 1)
                self.assertEqual(returns[0]['code'], idi.RC_OK)
            for host, entity in zip(hosts, sig_ids):
                self.assertTrue(host.request(entity, idi.REMOVE))
            result = machine.run(2000000)
            self.assertIsNone(result['fault'], result['fault'])
            for host in hosts:
                self.assertEqual(host.collect_returns()[0]['code'], idi.RC_OK)
                self.assertTrue(host.request(idi.DSIG_ID, idi.ASSIGN,
                                             parameters=idi.sig_assign_payload()))
            result = machine.run(2000000)
            self.assertIsNone(result['fault'], result['fault'])
            for host in hosts:
                self.assertEqual(host.collect_returns()[0]['code'], idi.ASSIGN_OK)

    def test_concurrent_pairs_binary_bearer_and_full_call_lifecycle(self):
        from eicon_bri_pair_probe import probe
        result = probe(ROOT / 'docs/firmware', 42000000, profile=9, answer=True, pair='both',
                       hangup=True, network=True, raw_sport=True, payload=(bytes(range(256)) * 8)[:2044])
        self.assertIsNone(result['fault'])
        self.assertTrue(result['completed'], result.get('events'))
        self.assertEqual(result['connected'], [0, 1, 2, 3])
        self.assertEqual(result['network_connected'], [0, 1, 2, 3])
        self.assertEqual(result['received'], result['expected'])
        self.assertEqual(len(set(result['received'].values())), 4)
        self.assertEqual(result['answered_controllers'], [1, 3])
        self.assertTrue(result['data_posted'])
        for sender in result['senders'].values():
            self.assertEqual(sender['posted_fragments'], 8)
            self.assertEqual(sender['accepted_bytes'], 2048)
            self.assertTrue(sender['idle'])
        self.assertTrue(all(len(bytes.fromhex(frame)) == 2048
                            for frame in result['received'].values()))
        self.assertTrue(result['answered'])
        self.assertEqual(result['disconnected'], [0, 1, 2, 3])
        self.assertEqual(result['stage'], 'call-cleared')
        self.assertEqual(result['receiver_nl_assign'][0]['code'], idi.ASSIGN_OK)
        # SABME/UA and Q.931 travel through real firmware D-channel FIFOs.
        self.assertIn('00017f', result['frames'][0])
        self.assertIn('000173', result['frames'][1])
        self.assertTrue(any(bytes.fromhex(frame)[7:8] == b'\x07'
                            for frame in result['frames'][1]))

    def test_all_eight_bearers_transfer_fragmented_binary_frames(self):
        from eicon_bri_full_probe import probe
        result = probe(ROOT / 'docs/firmware', 68000000,
                       payload=(bytes(range(256)) * 8)[:2044])
        self.assertIsNone(result['fault'])
        self.assertIsNone(result['probe_error'])
        self.assertTrue(result['completed'], result.get('endpoints'))
        self.assertEqual(result['stage'], 'all-bearers-cleared')
        self.assertEqual(result['peak_active_bearers'], 8)
        self.assertEqual(result['bearer_channels'], {i: [1, 2] for i in range(4)})
        self.assertEqual(len(result['endpoints']), 8)
        self.assertEqual(len({e['received'] for e in result['endpoints']}), 8)
        for endpoint in result['endpoints']:
            self.assertEqual(endpoint['received'], endpoint['expected'])
            self.assertEqual(len(bytes.fromhex(endpoint['received'])), 2048)
            self.assertEqual(endpoint['accepted_bytes'], 2048)
            self.assertEqual(endpoint['posted_fragments'], 8)
            self.assertTrue(endpoint['sender_idle'])
            self.assertTrue(endpoint['cleared'])
            peer = result['endpoints'][endpoint['peer']]
            self.assertEqual(peer['peer'], endpoint['index'])
        for controller in range(4):
            local = [e for e in result['endpoints'] if e['controller'] == controller]
            self.assertEqual(len({e['sig'] for e in local}), 2)
            self.assertEqual(len({e['nl'] for e in local}), 2)

    def test_modem_listener_admits_audio_and_starts_stock_training(self):
        from eicon_bri_pair_probe import probe
        result = probe(ROOT / 'docs/firmware', 20000000, profile=0, answer=True,
                       raw_sport=True, modulation='v21', law='pcma')
        self.assertTrue(result['completed'], result.get('events'))
        self.assertEqual(result['connected'], [0, 1])
        self.assertEqual(result['rejected'], {})
        self.assertEqual(result['stage'], 'call-connected')
        self.assertEqual(result['receiver_nl_assign'][0]['code'], idi.ASSIGN_OK)
        logs = [bytes.fromhex(record) for record in result['controller_logs']['1']]
        self.assertTrue(any(b'Supervisor training start' in record for record in logs))
        active = result['board']['dsp_state']['3']
        self.assertFalse(active['held'])
        self.assertNotEqual(active['first_isr'], 0x2a7eea)

    def test_stock_v22bis_modem_waits_for_carrier_and_transmits_binary_data(self):
        from eicon_bri_pair_probe import probe
        result = probe(ROOT / 'docs/firmware', 430000000, profile=0,
                       raw_sport=True, modulation='v22b', law='pcmu',
                       payload=b'whole-board-v22bis\x00\xff', hangup=True)
        self.assertIsNone(result['fault'])
        self.assertTrue(result['completed'], {key: result.get(key) for key in
                        ('stage', 'received', 'expected', 'modem_signals')})
        self.assertEqual(result['stage'], 'call-cleared')
        self.assertEqual(result['network_connected'], [0, 1])
        self.assertEqual(result['received'], result['expected'])
        self.assertEqual(len(set(result['received'].values())), 2)
        self.assertEqual(result['disconnected'], [0, 1])
        self.assertTrue(all(sender['idle'] for sender in result['senders'].values()))
        logs = [bytes.fromhex(record) for records in result['controller_logs'].values()
                for record in records]
        self.assertFalse(any(b'Receive overflow' in record for record in logs))
        self.assertTrue(all(state['tx_bps'] == state['rx_bps'] == 2400
                            for state in result['modem_signals'].values()))
        self.assertTrue(any(b'V.42 detection:' in record and b'FALSE' in record for record in logs))
        self.assertFalse(any(b'V.42 detection:' in record and b'TRUE' in record for record in logs))

    def test_stock_v21_all_eight_modem_bearers_deliver_binary_data(self):
        from eicon_bri_full_probe import probe
        result = probe(ROOT / 'docs/firmware', 430000000, profile=0,
                       modulation='v21', law='pcma', payload=b'\x00\xff\x7e\x7d\x11\x13')
        self.assertIsNone(result['fault'])
        self.assertIsNone(result['probe_error'])
        self.assertTrue(result['completed'], result['stage'])
        self.assertEqual(result['stage'], 'all-bearers-cleared')
        self.assertEqual(result['peak_active_bearers'], 8)
        self.assertEqual(result['bearer_channels'], {i: [1, 2] for i in range(4)})
        self.assertEqual(len({e['received'] for e in result['endpoints']}), 8)
        for endpoint in result['endpoints']:
            self.assertEqual(endpoint['received'], endpoint['expected'])
            self.assertEqual(endpoint['accepted_bytes'], 10)
            self.assertTrue(endpoint['sender_idle'])
            self.assertTrue(endpoint['cleared'])

    def test_bearer_reset_and_masked_status_match_physical_card(self):
        import json
        from eicon_bri_probe import build
        from eicon_dsp_bench import read_results
        fixture = json.loads((ROOT / 'tests/data/bri_card12196.json').read_text())
        with BriCard() as card:
            image, meta = build('ipac-b-reset')
            card.ram[:len(image)] = image
            machine = BriMachine(card)
            card.write(3, 0x200, 4, 3)
            result = machine.run(3000000)
            self.assertIsNone(result['fault'])
            probe = read_results(card.ram, meta)
            self.assertTrue(probe['finished'])
            for actual, expected in zip(probe['dsps'], fixture['ipac_b_reset']['ports']):
                self.assertEqual(actual['dm'], expected['values'])

    def test_probe_matches_physical_card_ci_buffering(self):
        import json
        from eicon_bri_probe import build
        from eicon_dsp_bench import read_results
        fixture = json.loads((ROOT / 'tests/data/bri_card12196.json').read_text())
        with BriCard() as card:
            image, meta = build('ipac-ci')
            card.ram[:len(image)] = image
            machine = BriMachine(card)
            card.write(3, 0x200, 4, 3)
            result = machine.run(10000000)
            self.assertIsNone(result['fault'], result['fault'])
            probe = read_results(card.ram, meta)
            self.assertTrue(probe['finished'])
            for actual, expected in zip(probe['dsps'], fixture['ipac_ci']['ports']):
                self.assertEqual(actual['dm'], expected['values'])

    def test_reset_entry_and_high_sdram_aperture_match_physical_card(self):
        import hashlib
        import json
        from eicon_bri_probe import build
        from eicon_dsp_bench import read_results
        fixture = json.loads((ROOT / 'tests/data/bri_boot_entry_card12196.json').read_text())
        for key, kind in (('entry', 'boot-entry'), ('high_pages', 'boot-high-sweep')):
            with self.subTest(kind=kind), BriCard() as card:
                image, meta = build(kind)
                evidence = fixture[key]
                self.assertEqual(hashlib.sha256(image).hexdigest(), evidence['image_sha256'])
                self.assertEqual(meta, evidence['meta'])
                card.ram[:len(image)] = image
                machine = BriMachine(card)
                card.write(3, 0x200, 4, 3)
                result = machine.run(2000000)
                self.assertIsNone(result['fault'], result['fault'])
                actual = read_results(card.ram, meta)
                self.assertTrue(actual['finished'])
                self.assertEqual(actual['dsps'][0]['dm'],
                                 evidence['capture']['dsps'][0]['dm'])

    def test_reset_and_high_aperture_writes_match_physical_card(self):
        import hashlib
        import json
        from eicon_bri_probe import build
        from eicon_dsp_bench import read_results
        fixture = json.loads((ROOT / 'tests/data/bri_boot_entry_card12196.json').read_text())
        for kind in ('boot-alias', 'boot-high-alias'):
            with self.subTest(kind=kind), BriCard() as card:
                image, meta = build(kind)
                evidence = fixture[kind]
                self.assertEqual(hashlib.sha256(image).hexdigest(), evidence['image_sha256'])
                card.ram[:len(image)] = image
                machine = BriMachine(card)
                card.write(3, 0x200, 4, 3)
                result = machine.run(100000)
                self.assertIsNone(result['fault'], result['fault'])
                probe = read_results(card.ram, meta)
                self.assertTrue(probe['finished'])
                values = probe['dsps'][0]['dm']
                self.assertEqual(values[:2048], values[2048:4096])
                self.assertEqual(values[4096:], evidence['word_tests'])
                self.assertEqual(card.ram[:len(image)], image)

    def test_legacy_four_image_firmware_boots_all_controllers_and_dsps(self):
        import struct
        from eicon_bri_legacy import stage
        with BriCard() as card:
            layout = stage(card, ROOT / 'docs/firmware')
            self.assertGreater(layout['downloads'], 0)
            for region in layout['regions']:
                self.assertLessEqual(region['end'], region['limit'])
            machine = BriMachine(card)
            card.write(3, 0x200, 4, 3)
            result = machine.run(30000000)
            self.assertIsNone(result['fault'], result['fault'])
            self.assertEqual(len(card.dsps), 8)
            for i in range(4):
                self.assertEqual(struct.unpack_from('<H', card.ram,
                    i * 0x400000 + 0x3f001e)[0], 0x4447)

    def test_processor_identification_probe_matches_physical_card(self):
        import json
        from eicon_bri_probe import build
        from eicon_dsp_bench import read_results
        fixture = json.loads((ROOT / 'tests/data/bri_card12196.json').read_text())
        with BriCard() as card:
            image, meta = build('cpu')
            card.ram[:len(image)] = image
            machine = BriMachine(card)
            card.write(3, 0x200, 4, 3)
            result = machine.run(10000)
            self.assertIsNone(result['fault'], result['fault'])
            probe = read_results(card.ram, meta)
            self.assertTrue(probe['finished'])
            self.assertEqual(probe['dsps'][0]['dm'], fixture['cp0']['values'])

    def test_four_ipac_cpu_interrupt_routes_match_physical_card(self):
        import hashlib
        import json
        from eicon_bri_probe import build
        fixture = json.loads((ROOT / 'tests/data/bri_ipac_irq_card12196.json').read_text())
        image, meta = build('ipac-ci-irq')
        self.assertTrue(fixture['finished'])
        self.assertEqual(hashlib.sha256(image).hexdigest(), fixture['program_sha256'])
        self.assertEqual(len(fixture['phases']), 8)
        with BriCard() as card:
            machine = BriMachine(card)
            for device in card.ipacs:
                device.read(0x2e)
                device.serial_tick()
                device.read(0x2e)
            for index, phase in enumerate(fixture['phases']):
                device = card.ipacs[index // 2]
                values = [int(v, 16) for v in phase['values']]
                device.write(0x60, 0xef)
                # Replay measured C/I input. This verifies routing and read
                # acknowledgment, not the full retained transceiver state.
                device._indication(values[3] >> 4)
                device.serial_tick()
                self.assertEqual(machine._cause() & 0x6c00, values[0] & 0x6c00)
                self.assertEqual(device.read(0x60), values[2])
                self.assertEqual(device.read(0x2e), values[3])
                self.assertEqual(device.read(0x2e), values[4])
                self.assertEqual(machine._cause() & 0x6c00, values[6] & 0x6c00)
                device.write(0x60, 0xff)

    def test_cpu_reset_restarts_changed_code_without_clearing_sdram(self):
        import struct
        # Increment v0 once, then loop. Replace increment across reset to
        # catch both stale CPU state and stale translated instruction cache.
        for reset in ('board', 'plx'):
            with BriCard() as card:
                card.ram[:16] = struct.pack('<IIII', 0x24420001, 0x1000ffff, 0, 0)
                machine = BriMachine(card)
                card.write(3, 0x200, 4, 3)
                machine.run(20)
                if reset == 'plx':
                    pins = card.read(0, 0x6e, 2)
                    for value in (pins | 0x4000, pins, pins | 0x2000, pins):
                        card.write(0, 0x6e, 2, value)
                else:
                    card.write(3, 0x200, 4, 0)
                card.write(2, 0, 4, 0x24420005)
                card.write(2, 0x100, 4, 0x12345678)
                card.write(3, 0x200, 4, 3)
                result = machine.run(20)
                self.assertIsNone(result['fault'], result['fault'])
                from unicorn.mips_const import UC_MIPS_REG_V0
                self.assertEqual(machine.uc.reg_read(UC_MIPS_REG_V0), 5)
                self.assertEqual(card.read(2, 0x100, 4), 0x12345678)

    def test_host_and_cpu_share_sdram_and_unknown_mmio_stops(self):
        import struct
        with BriCard() as card:
            # lui t0,bf80; lbu v0,7ffc(t0): unsupported board register.
            card.ram[:8] = struct.pack('<II', 0x3c08bf80, 0x91027ffc)
            machine = BriMachine(card)
            card.write(3, 0x200, 4, 3)
            result = machine.run(10)
            self.assertIn('unsupported BAR3 read', result['fault']['reason'])
            machine.uc.mem_write(0x100, b'ABCD')
            self.assertEqual(card.read(2, 0x100, 4), 0x44434241)
            card.write(2, 0x104, 4, 0x12345678)
            self.assertEqual(bytes(machine.uc.mem_read(0x104, 4)), b'\x78\x56\x34\x12')


if __name__ == '__main__':
    unittest.main()
