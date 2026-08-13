"""Build-109 MIPS address-space regression tests."""
import struct
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from analog_line import AnalogLineInterface
from analog_mips_boot import AnalogMipsBoot


IMAGE = Path(__file__).resolve().parents[1] / "docs/firmware/build-109/te_dmlt.am"


def test_build109_low_heap_does_not_alias_kseg_image():
    boot = AnalogMipsBoot(IMAGE)
    original_code = bytes(boot.uc.mem_read(0x93C40, 12))

    boot.run(5_000_000)

    root = struct.unpack("<I", boot.uc.mem_read(0x112C0, 4))[0]
    assert root == 0x802C9000
    assert boot.self_loop not in (0x801057EC, 0x801058CC)
    assert bytes(boot.uc.mem_read(0x93C40, 12)) == original_code
    pots = struct.unpack("<I", boot.uc.mem_read((root + 0xD14) & 0x1FFFFFFF, 4))[0]
    assert pots == 0x804364C0

    selected, bitmap = boot.configure_audio(channel=1, timeslot=1)
    assert selected == 1
    assert bitmap == b"\x01" + bytes(7)

    sensors = struct.unpack("<I", boot.uc.mem_read((root + 0x1294) & 0x1FFFFFFF, 4))[0]
    boot.set_hook_input(True)
    assert boot.uc.mem_read((sensors + 8) & 0x1FFFFFFF, 1)[0] & 1
    boot.set_hook_input(False)
    assert not boot.uc.mem_read((sensors + 8) & 0x1FFFFFFF, 1)[0] & 1


def test_native_pots_line_service_comes_from_daa_battery():
    boot = AnalogMipsBoot(IMAGE)
    line = AnalogLineInterface(line_voltage=48)
    boot.attach_analog_line(line)
    assert boot.line_in_service()
    line.set_connected(False)
    assert not boot.line_in_service()


def test_native_idi_signaling_assign_uses_pr_ram_queue():
    boot = AnalogMipsBoot(IMAGE)
    boot.run(5_000_000)
    before = boot.uc.mem_read(0x1000 + 6, 1)[0]
    entity = boot.assign_signaling()
    assert entity == 2
    assert boot.signaling_entity == entity
    assert boot.uc.mem_read(0x1000 + 7, 1)[0] != before


def test_native_call_request_reaches_build_109_dispatch():
    boot = AnalogMipsBoot(IMAGE)
    boot.attach_analog_line(AnalogLineInterface())
    boot.run(5_000_000)
    assert boot.request_outgoing_call("6001")
    # The staged Analog archive makes modem resource 0x11 available. CALL_REQ
    # now reaches native dsp_assign and asks for TIKRNL81.ANA (0x0258), rather
    # than failing immediately with "Resource unavailable 07 11".
    boot.step(200_000)
    assert any(text.startswith('Conf: Prot=') and args[0] == 34
               for text, *args in boot.trace_records)
    assert boot.trace_formats['[%s,%d] Download %d requested'] > 0
    assert boot.native_download_requests[-1][1] == 0x0258
    assert boot.native_download_blocks[-1] == 0xBF804800
    request = boot.native_download_requests[-1][0]
    assert boot.complete_native_download(request) == 1
    assert not any('Resource unavailable' in text
                   for text in boot.trace_formats)
    # Publish a healthy idle FXO line before CAS evaluates DAA sensor planes.
    # The call remains pending for later POTS work rather than immediately
    # releasing the DSP as an all-lines-out-of-service cable error.
    for _ in range(4):
        boot.step(200_000)
    assert not any('all lines out of service' in text
                   for text in boot.trace_formats)
    assert boot.trace_formats['[%s,%d] dsp_release %d'] == 0
    assert boot.dial_generator_calls == 0


def test_indexed_mailbox_handlers_are_firmware_callable():
    boot = AnalogMipsBoot(IMAGE)
    boot.run(5_000_000)
    registers = {}
    boot.set_mailbox_handlers(
        lambda base, register: registers.get((base, register), 0),
        lambda base, register, value: registers.__setitem__((base, register), value),
    )
    base = 0xBF803800
    boot.call(0x8010444C, base, 0x4009, 0xA5A5)
    assert registers[(base, 0x4009)] == 0xA5A5
    assert boot.call(0x80104418, base, 0x4009) == 0xA5A5
