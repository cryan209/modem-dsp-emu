"""Exar-ID Win9x candidate using the OS serial settings UI, not BBEPORT.DLL."""
from pathlib import Path
import zipfile,hashlib,json,sys
source=Path(sys.argv[1]);target=Path(sys.argv[2])
root='MIport-1.0.1/Windows/Drivers/MIport/98_ME/'
readme=r'''EXAR / MT9234ZPX STANDARD-UI TEST V2
==================================
Target: PCI\VEN_13A8&DEV_0152. Experimental; not vendor-certified or verified
on Windows 95. No kernel binary has been patched. The VxDs originate from
B&B MIport 1.0.1 (98/Me directory); Windows 95 runtime compatibility remains
unproven. This revision specifically addresses a crash in BBEPORT.DLL.

Why this revision:
The reported BBEPORT.DLL 0001:2E1E crash points to an indirect far callback
in the 16-bit settings DLL. V1 installed that DLL as both ConfigDialog and
EnumPropPages. V2 changes the PORT settings hooks to Windows' SERIALUI.DLL,
omits the B&B class-page hook, and does not distribute/copy BBEPORT.DLL.
The same original bb17d15x.vxd/bbestub.vxd perform serial I/O.

CHANGES
- Adds Exar 13A8:0152 to the one-port parent installation section.
- Uses ConfigDialog=serialui.dll and EnumPropPages=serialui.dll,EnumPropPages.
- Removes BBEPORT.DLL copy entries and the parent class property-page hook.
- Keeps the original FIFO/RS-232 configuration and UART binaries unchanged.
- Does not increase modem INF speed limits or change modem AT commands.

INSTALL / UPDATE
1. Close HyperTerminal, dial-up and fax programs using the PCI modem. Keep a
   separate working way to reconnect and a recovery boot available.
2. Extract to a NEW folder, C:\EXARV2. Do not merge with V1.
3. In Device Manager, select COM5 under Ports -> Properties -> Driver ->
   Change/Update Driver -> Have Disk -> C:\EXARV2. Choose
   "Exar modem port 1 (standard UI test V2)". Use the Driver tab rather than
   the currently crashing settings tab. If Windows crashes before you can
   reach Driver, use Safe Mode to remove only this test port and parent,
   then reinstall from this folder during detection on normal startup.
4. If reinstalling the parent, choose
   "Exar XR17C152 one-port modem (standard UI test V2)".
   If a child-port wizard appears, use this same folder.
5. Reboot if requested. The new COM number may differ. Check Device Manager
   status first. Open and CLOSE its settings: the B&B UI should not load.
6. Test the COM port in HyperTerminal at 57600, 8 data bits, no parity,
   1 stop bit, hardware RTS/CTS. Type AT, then ATI. Close HyperTerminal,
   reopen the port and repeat. Finally reboot and repeat.
7. Only if these checks pass, try a dial-up call. Report separately whether
   settings close cleanly, the port reopens, and it works after reboot.

This does not prove that the driver handles clocking, interrupts or flow
control correctly. If 'port already open' remains, do not keep reinstalling
modem INFs: report the port's Device Manager status and test results.
Do not delete or replace SERIALUI.DLL, RUNDLL32.EXE or other Windows files.

ROLLBACK
If startup becomes unreliable, use F8 Safe Mode and remove the experimental
Exar parent and its associated port. Leave other COM ports alone. No global
Windows serial driver or shared system DLL is replaced by this package.

PROVENANCE
Original: MIport-1.0.1-drivers.zip from
https://www.driverguide.com/driver/detail.php?driverid=948701
Original INFs and binary SHA256 values are in ORIGINAL/ and
ORIGINAL-SHA256.JSON. Original copyrights apply.
'''
with zipfile.ZipFile(source) as original,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as out:
 hashes={}
 for name in ['bb17d15x.inf','bbeport.inf','bb17d15x.vxd','bbestub.vxd']:
  data=original.read(root+name);hashes[name]=hashlib.sha256(data).hexdigest()
  if name.endswith('.inf'):
   out.writestr('ORIGINAL/'+name,data)
   text=data.decode('latin1')
   if name=='bb17d15x.inf':
    text=text.replace('[DeviceList]\r\n','[DeviceList]\r\n%DESCEXAR%=DriverInstall1,PCI\\VEN_13A8&DEV_0152\r\n')
    text=text.replace('bbeport.dll=1\r\n','').replace('bbeport.dll,,,2\r\n','')
    text=text.replace('HKR,,EnumPropPages,,"bbeport.dll,NoSuchPage"\r\n','')
    text+='\r\nDESCEXAR="Exar XR17C152 one-port modem (standard UI test V2)"\r\n'
   else:
    text=text.replace('HKR,,ConfigDialog,,bbeport.dll','HKR,,ConfigDialog,,serialui.dll')
    text=text.replace('"bbeport.dll,PortEnumPropPages"','"serialui.dll,EnumPropPages"')
    text=text.replace('Port 1 on MIport Card','Exar modem port 1 (standard UI test V2)')
   data=text.encode('latin1')
  out.writestr(name,data)
 out.writestr('README.TXT',readme.replace('\n','\r\n'))
 out.writestr('ORIGINAL-SHA256.JSON',json.dumps(hashes,indent=2))
with zipfile.ZipFile(target) as z:
 assert z.testzip() is None
 assert not any(n.lower().endswith('bbeport.dll') for n in z.namelist())
 for name in ('bb17d15x.inf','bbeport.inf'):
  assert b'bbeport.dll' not in z.read(name).lower()
 assert b'serialui.dll,EnumPropPages' in z.read('bbeport.inf')
 for name in ('bb17d15x.vxd','bbestub.vxd'):
  assert hashlib.sha256(z.read(name)).hexdigest()==hashes[name]
print(target,target.stat().st_size,hashlib.sha256(target.read_bytes()).hexdigest())
