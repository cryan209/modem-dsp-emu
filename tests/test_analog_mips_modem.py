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
