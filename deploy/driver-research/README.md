# Exar 13A8:0152 Windows 95 driver investigation

Uploaded PCI report identifies 13A8:0152 at bus 0 slot 0E, consistent with
XR17C/D152. The report aliases devices across functions and includes invalid
entries; these duplicates were not treated as additional hardware.

MIport 3.0.0 supplied by the user is NT-family-only and supports B&B vendor
1896 device IDs, not this Exar ID. An older MIport 1.0.1 archive was retrieved
from https://www.driverguide.com/driver/detail.php?driverid=948701 using its
normal browser download flow. It contains a real Windows 9x/Me VCOMM VxD
stack, but the directory is 98_ME and bbestub.vxd identifies Windows 98/Me.
This is not evidence of confirmed Windows 95 compatibility.

`build_exar_win9x_test.py` creates a clearly experimental test archive. It adds
PCI\VEN_13A8&DEV_0152 to bb17d15x.inf using the original DriverInstall1 section
(OnePort=1), assuming the modem is connected to channel 0. All VxD/DLL bytes
remain unchanged, with SHA256 verification and original INFs preserved.
No binary patches or automatic installation. Hardware behavior and Win95 OS
service compatibility remain untested. A generic ID match alone cannot prove
runtime compatibility, especially if binaries expect B&B-specific behavior.

Published as mt9234zpx-win9x-test.zip under Experimental drivers, with these
limitations on the catalogue and in README.TXT. ZIP CRC, the added hardware
ID, unchanged binary hashes and local HTTP byte-range delivery verified.
Package SHA256: 1293588c80ec30d0482d902329f79a6b4d840805459d8ce2e660931d9098cd31.
This is a candidate for user-directed testing, not a verified Win95 solution.

## Standard settings UI experiment (V2)

User reported initial ATI responses, then port busy after reboot and a GPF
in BBEPORT.DLL at 0001:2E1E on opening/closing COM5 settings. NE segment-table
inspection and 16-bit disassembly locate that exact offset at an indirect far
call `lcall es:[si+8]` in a callback/lifecycle routine. This supports targeting
the user-mode settings DLL; it does not prove the UART VxDs are compatible.

`build_exar_standard_ui_test.py` keeps both original VxDs byte-identical but
replaces child-port ConfigDialog/EnumPropPages hooks with Windows SERIALUI.DLL
and drops BBEPORT.DLL copy entries and the parent class hook from the new INF.
No shared Windows DLLs are replaced. Existing class-level registry values may
survive from V1; updating the child port from the new folder is needed to
replace its settings hooks. The package cannot repair a VxD startup/interrupt
problem and does not change the installed modem INF's speed declarations.

V2 is published as mt9234zpx-win9x-v2.zip. SHA256:
57e143b9b6c420fd9c7e050775cc52ce710667d9d653da2ea04086a7aaa0b594.
Archive CRC, absence of active BBEPORT.DLL references, expected SERIALUI hooks,
unchanged VxD hashes and HTTP range delivery verified. Windows runtime remains
untested; user needs settings-close, reopen and post-reboot checks. V1 is marked
superseded with its reported crash in the library.
