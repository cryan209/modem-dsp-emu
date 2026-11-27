"""Explicit portable gate; unlisted tests still run in the complete suite.

Keep this allowlist conservative: importing emulator modules is OK, but tests
that execute Unicorn MIPS, require untracked artifacts, or use external network/
TUN services belong in `make test-all`, not the portable CI gate. Controlled
localhost protocol-unit sockets do not depend on an external service.
"""
from pathlib import Path

import pytest


PORTABLE_MODULES = {
    'test_2185n_sport.py',
    'test_adsp_disassembler.py',
    'test_adsp_idle.py',
    'test_adsp_sport.py',
    'test_adsp_sport_wire.py',
    'test_analog_line.py',
    'test_analog_line_interface.py',
    'test_analog_rx_bandlimit.py',
    'test_eicon_at.py',
    'test_eicon_idi.py',
    'test_eicon_ipacx.py',
    'test_eicon_ipacx_crc.py',
    'test_eicon_ipacx_filter.py',
    'test_execution_history_compare.py',
    'test_g711_mulaw.py',
    'test_isdn_bchannel.py',
    'test_loopback_environment.py',
    'test_media_clock.py',
    'test_media_clock_trace.py',
    'test_mlog_audio_split.py',
    'test_pcm_resampler.py',
    'test_resampler_replay.py',
    'test_ppp_retry_recovery.py',
    'test_rtp_packet_size.py',
    'test_rtp_qualification.py',
    'test_qualification_faults.py',
    'test_producer_pause.py',
    'test_producer_profile.py',
    'test_producer_detail.py',
    'test_rx_recovery_probe.py',
    'test_data_rx_resume.py',
    'test_rtp_pcap_timing.py',
    'test_run_launcher.py',
    'test_rx_prime_sync_windows.py',
    'test_test_environment.py',
    'test_tx_prime_sync_windows.py',
    'test_v34_info.py',
    'test_v34_qualification.py',
    'test_v42_lapm.py',
    'test_v42bis.py',
    'test_v44.py',
}


SLOW_TESTS = {
    'test_eicon_bri_machine.py::BriMachineTest::test_stock_v21_all_eight_modem_bearers_deliver_binary_data',
    'test_eicon_bri_machine.py::BriMachineTest::test_stock_v22bis_modem_waits_for_carrier_and_transmits_binary_data',
}


def pytest_collection_modifyitems(items):
    for item in items:
        if Path(item.path).name in PORTABLE_MODULES:
            item.add_marker(pytest.mark.portable)
        if item.nodeid.removeprefix('tests/') in SLOW_TESTS:
            item.add_marker(pytest.mark.slow)
