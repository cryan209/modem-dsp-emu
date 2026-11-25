"""Create an explicitly experimental Exar-ID INF adaptation; never patch binaries."""
from pathlib import Path
import zipfile,hashlib,json,sys
source=Path(sys.argv[1]);target=Path(sys.argv[2])
root='MIport-1.0.1/Windows/Drivers/MIport/98_ME/'
readme='''EXPERIMENTAL EXAR PCI UART DRIVER TEST
======================================
Target PCI ID: PCI\\VEN_13A8&DEV_0152 (your MultiTech MT9234ZPX interface).

This is a test adaptation of the B&B MIport 1.0.1 Windows 98/Me VxD driver.
It is NOT a verified Windows 95 driver and NOT an official MultiTech package.
The original driver labels itself Windows 9x/Me; its port stub says Windows
98/Me. Loading on Windows 95 may fail or destabilize Windows. Do not treat
this as established compatibility. Keep your existing dial-up modem working.

Changes: one PCI ID added to bb17d15x.inf, selecting the original one-port
installation section. The original VxD/DLL binaries are unchanged. This
assumes the modem is attached to UART channel 0; that remains untested.
Original INF files are preserved in ORIGINAL/. No kernel binaries are patched.

If you elect to test:
1. Save your current hardware configuration and have a working recovery boot.
2. Extract these files to C:\\EXAR9X on the Windows 95 PC.
3. Device Manager -> Other devices -> your PCI serial controller -> Properties
   -> Driver -> Update/Change Driver -> Have Disk. Point to C:\\EXAR9X.
4. Select 'Exar XR17C152 one-port modem (EXPERIMENTAL)'.
5. If another hardware wizard requests the port driver, use bbeport.inf from
   the same folder. Do not force a Windows 2000/XP driver.
6. Reboot only if requested. Check for a new COM port and any Device Manager
   error code before trying to install a modem on that port.
7. If a working COM port appears, test AT in HyperTerminal (8N1, RTS/CTS).
   A response of OK is the first hardware confirmation. Do not dial until then.

If it fails: record the exact error code/message. Remove the test device
in Device Manager. If Windows cannot boot normally, use F8 Safe Mode to
remove the test device. Do not remove unrelated serial ports or system files.

Source: https://www.driverguide.com/driver/detail.php?driverid=948701
Original archive: MIport-1.0.1-drivers.zip. All original copyrights apply.
'''
with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as out:
    hashes={}
    for name in ['bb17d15x.inf','bbeport.inf','bb17d15x.vxd','bbestub.vxd','BBEPORT.DLL']:
        data=archive.read(root+name);hashes[name]=hashlib.sha256(data).hexdigest()
        if name=='bb17d15x.inf':
            out.writestr('ORIGINAL/'+name,data)
            text=data.decode('latin1')
            text=text.replace('[DeviceList]\r\n','[DeviceList]\r\n; Experimental Exar-ID adaptation, not vendor-certified.\r\n%DESCEXAR%=DriverInstall1,PCI\\VEN_13A8&DEV_0152\r\n')
            text += '\r\nDESCEXAR="Exar XR17C152 one-port modem (EXPERIMENTAL)"\r\n'
            data=text.encode('latin1')
        elif name=='bbeport.inf':out.writestr('ORIGINAL/'+name,data)
        out.writestr(name,data)
    out.writestr('README.TXT',readme.replace('\n','\r\n'))
    out.writestr('ORIGINAL-SHA256.JSON',json.dumps(hashes,indent=2))
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    for name,digest in hashes.items():
        if name.lower().endswith(('.vxd','.dll')):assert hashlib.sha256(archive.read(name)).hexdigest()==digest
    text=archive.read('bb17d15x.inf').decode('latin1')
    assert 'PCI\\VEN_13A8&DEV_0152' in text and '%DESCEXAR%=DriverInstall1' in text
print(target, target.stat().st_size,hashlib.sha256(target.read_bytes()).hexdigest())
