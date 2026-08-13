"""Native Analog DSPDAA ownership regression tests."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import dial_tikrnl_drive as drive
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
