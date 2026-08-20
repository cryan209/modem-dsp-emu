"""Native Analog DSPDAA ownership regression tests."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import dial_tikrnl_drive as drive
from analog_line import AnalogLineInterface
from analog_mips_modem import AnalogMipsModem, ADSP

IMAGE = Path(__file__).resolve().parents[1] / "docs/firmware/build-109/te_dmlt.am"


def test_native_dspdaa_kernel_and_idle_task_boot_on_separate_core():
    drive.select_firmware_set("analog109")
    modem = AnalogMipsModem(IMAGE)
    modem.card.boot()
    media_cpu = modem.card.cpu

    modem._boot_native_dspdaa()

    assert modem._native_dspdaa_running
    assert modem._dspdaa_cpu != media_cpu
    assert ADSP.adsp2181_idle(modem._dspdaa_cpu)
    assert ADSP.adsp2181_pc(modem._dspdaa_cpu) == 0x02A6
    assert modem._dspdaa_pm[0x0900] != 0


def test_si3056_audio_word_is_clocked_through_native_sport1():
    drive.select_firmware_set("analog109")
    modem = AnalogMipsModem(IMAGE)
    modem.card.boot()
    modem._boot_native_dspdaa()

    modem._clock_dspdaa_sport1(-1234)

    assert modem._dspdaa_rx_word == (-1234 & 0xFFFF)
    # The native ISR consumes RX1 even when its transmit latch is not updated.
    assert modem._dspdaa_dm[0x2E22] == (-1234 & 0xFFFF)
    assert ADSP.adsp2181_idle(modem._dspdaa_cpu)


def test_si3056_frames_do_not_fabricate_kernel_status_words():
    drive.select_firmware_set("analog109")
    modem = AnalogMipsModem(IMAGE)
    modem.card.boot()
    modem._boot_native_dspdaa()
    line = AnalogLineInterface(line_voltage=48, loop_current_ma=24)
    modem.attach_analog_line(line)
    before = tuple(modem._dspdaa_dm[address]
                   for address in (0x2E5E, 0x2E5F, 0x2E60))

    modem._clock_dspdaa_sport1(0)

    assert tuple(modem._dspdaa_dm[address]
                 for address in (0x2E5E, 0x2E5F, 0x2E60)) == before


def test_native_dspdaa_foreground_processes_command_to_idle():
    drive.select_firmware_set("analog109")
    modem = AnalogMipsModem(IMAGE)
    modem.card.boot()
    modem._boot_native_dspdaa()
    # Reproduce the native IDLE.ANA command envelope written by MIPS.
    for address, value in ((0x0000, 0x0229), (0x0001, 0x3FE5),
                           (0x0002, 1), (0x0003, 0x00F5),
                           (0x0004, 0), (0x2E4F, 0x8000)):
        modem._dspdaa_dm[address] = value

    modem._run_dspdaa_foreground()

    assert modem._dspdaa_dm[0] == 0x2E47
    assert modem._dspdaa_dm[9] == 0
    assert modem._dspdaa_dm[0x2E4F] == 0x8000
    assert ADSP.adsp2181_idle(modem._dspdaa_cpu)


def test_stale_mailbox_descriptor_does_not_trigger_mips_every_sample():
    """A completed descriptor is level-stable, not a per-sample interrupt."""
    modem = object.__new__(AnalogMipsModem)
    modem.card = type('CardStub', (), {})()
    modem.card.dm = [0] * 0x4000
    modem.card.dm[0] = 0x1234  # completed command pointer remains published
    modem.card.dm[0x3fb4] = 0
    modem.card.frame_fast = lambda word, sample_index: 0
    modem._native_dspdaa_running = False
    modem._samples = 0
    modem.mips_interval = 160
    modem._last_mailbox_signature = None
    calls = []
    modem._step_mips = lambda instructions: calls.append(instructions)

    for sample in range(1, 161):
        modem.frame_fast(0, sample)

    # One edge-triggered service plus the normal 20 ms supervisor poll.
    assert calls == [50_000, 50_000]
