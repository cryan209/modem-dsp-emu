/* SPDX-License-Identifier: GPL-2.0-or-later
 * Native macOS TUSB3410 PTY bridge, protocol from Linux ti_usb_3410_5052.c.
 * Build: cc -O2 -Wall -Wextra bridge.c -framework IOKit -framework CoreFoundation -o bridge
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/IOKitLib.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <termios.h>
#include <signal.h>
#include <pthread.h>
#include <poll.h>
#include <errno.h>

typedef IOUSBDeviceInterface650 **Dev;
typedef IOUSBInterfaceInterface650 **Intf;
static Dev dev;
static Intf intf;
static UInt8 pin, pout, pint;
static int master = -1;
static volatile sig_atomic_t quit;
static uint64_t tx, rx;
static void stop(int s) { (void)s; quit=1; }
static void check(IOReturn r, const char *what) {
    if (r) { fprintf(stderr,"%s: %08x\n",what,r); exit(1); }
}
static int attach(void) {
    CFMutableDictionaryRef match=IOServiceMatching("IOUSBHostDevice");
    io_iterator_t it; check(IOServiceGetMatchingServices(kIOMainPortDefault,match,&it),"match");
    io_service_t svc;
    while ((svc=IOIteratorNext(it))) {
        IOCFPlugInInterface **plug=NULL; SInt32 score;
        IOReturn r=IOCreatePlugInInterfaceForService(svc,kIOUSBDeviceUserClientTypeID,kIOCFPlugInInterfaceID,&plug,&score);
        IOObjectRelease(svc); if(r || !plug) continue;
        Dev d=NULL; (*plug)->QueryInterface(plug,CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID650),(LPVOID*)&d);
        IODestroyPlugInInterface(plug); if(!d) continue;
        UInt16 pid,vid; (*d)->GetDeviceProduct(d,&pid); (*d)->GetDeviceVendor(d,&vid);
        if(vid==0x06e0 && (pid==0x0319 || pid==0xf115)) { dev=d; break; }
        (*d)->Release(d);
    }
    IOObjectRelease(it); if(!dev) return 0;
    check((*dev)->USBDeviceOpenSeize(dev),"open USB");
    UInt8 count; (*dev)->GetNumberOfConfigurations(dev,&count);
    IOUSBConfigurationDescriptorPtr desc;
    check((*dev)->GetConfigurationDescriptorPtr(dev,count>1?1:0,&desc),"configuration descriptor");
    check((*dev)->SetConfiguration(dev,desc->bConfigurationValue),"set configuration");
    IOUSBFindInterfaceRequest req={0xffff,0xffff,0xffff,0xffff};
    check((*dev)->CreateInterfaceIterator(dev,&req,&it),"interfaces");
    while((svc=IOIteratorNext(it))) {
        IOCFPlugInInterface **plug=NULL; SInt32 score;
        IOReturn r=IOCreatePlugInInterfaceForService(svc,kIOUSBInterfaceUserClientTypeID,kIOCFPlugInInterfaceID,&plug,&score);
        IOObjectRelease(svc); if(r || !plug) continue;
        (*plug)->QueryInterface(plug,CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID650),(LPVOID*)&intf);
        IODestroyPlugInInterface(plug); if(intf) break;
    }
    IOObjectRelease(it); if(!intf) { fprintf(stderr,"no interface\n"); exit(1); }
    check((*intf)->USBInterfaceOpenSeize(intf),"open interface");
    UInt8 nep; (*intf)->GetNumEndpoints(intf,&nep);
    pin=pout=pint=0;
    for(UInt8 p=1;p<=nep;p++) {
        UInt8 dir,num,type,interval; UInt16 size;
        check((*intf)->GetPipeProperties(intf,p,&dir,&num,&type,&size,&interval),"pipe");
        fprintf(stderr,"pipe %u: endpoint %02x type %u max %u\n",p,num|(dir==kUSBIn?128:0),type,size);
        if(type==kUSBBulk) { if(dir==kUSBIn) pin=p; else pout=p; }
        if(type==kUSBInterrupt && dir==kUSBIn) pint=p;
    }
    return pin?2:1;
}
static void release_usb(void) {
    if(intf) { (*intf)->USBInterfaceClose(intf); (*intf)->Release(intf); intf=NULL; }
    if(dev) { (*dev)->USBDeviceClose(dev); (*dev)->Release(dev); dev=NULL; }
}
static void command(UInt8 req,UInt16 module,UInt16 value,void *data,UInt16 len) {
    IOUSBDevRequestTO q={0x40,req,value,module,len,data,0,1000,1000};
    check((*dev)->DeviceRequestTO(dev,&q),"TI command");
}
static void mcr(int on) {
    UInt8 data[]={0x30,1,1,0,0,0xff,0xa4,0x30,on?0x30:0};
    command(0x80,5,0,data,sizeof data);
}
static void configure(void) {
    /* 115200 8N1, RTS/CTS, modem status interrupts, auto-start DMA; big endian. */
    UInt8 cfg[]={0,8,0x60,0x21,3,0,0,0x11,0x13,0};
    command(5,3,0,cfg,sizeof cfg); mcr(1);
}
static void *reader(void *arg) {
    UInt8 pipe=(UInt8)(uintptr_t)arg;
    while(!quit) {
        UInt8 buf[4096]; UInt32 n=sizeof buf;
        IOReturn r=(*intf)->ReadPipe(intf,pipe,buf,&n);
        if(quit) break;
        if(r) { fprintf(stderr,"USB read %08x\n",r); quit=1; break; }
        if(pipe==pint) continue;
        for(UInt32 off=0;off<n && !quit;) {
            ssize_t k=write(master,buf+off,n-off);
            if(k>0) { off+=(UInt32)k; rx+=(uint64_t)k; }
            else if(errno==EAGAIN || errno==EINTR) { struct pollfd p={master,POLLOUT,0}; poll(&p,1,100); }
            else { /* No client: discard unsolicited bytes. */ break; }
        }
    } return NULL;
}
int main(int argc,char **argv) {
    int reenum=0;
    const char *fw=NULL,*link="/tmp/mt9234zba";
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--reenumerate")) reenum=1;
        else if(!strcmp(argv[i],"--firmware") && i+1<argc) fw=argv[++i];
        else if(!strcmp(argv[i],"--pty-link") && i+1<argc) link=argv[++i];
        else { fprintf(stderr,"usage: %s [--firmware FILE] [--pty-link PATH]\n",argv[0]); return 2; }
    }
    int state=attach(); if(!state) { fprintf(stderr,"06e0:0319/f115 not found\n"); return 1; }
    if(reenum) { check((*dev)->USBDeviceReEnumerate(dev,0),"re-enumerate"); release_usb(); return 0; }
    if(state==1) {
        if(!fw || !pout) { fprintf(stderr,"bootloader needs --firmware mts_mt9234zba.fw\n"); return 1; }
        FILE *f=fopen(fw,"rb"); if(!f) { perror(fw); return 1; }
        UInt8 buf[16288]; size_t n=fread(buf,1,sizeof buf,f); fclose(f);
        if(n<3 || n>16284) { fprintf(stderr,"invalid firmware size\n"); return 1; }
        UInt8 sum=0; for(size_t i=3;i<n;i++) sum+=buf[i];
        buf[0]=(UInt8)(n-3); buf[1]=(UInt8)((n-3)>>8); buf[2]=sum;
        for(size_t i=0;i<n;i+=64) check((*intf)->WritePipeTO(intf,pout,buf+i,(UInt32)(n-i>64?64:n-i),1000,1000),"firmware write");
        fprintf(stderr,"loaded %zu firmware bytes; resetting USB controller\n",n);
        usleep(100000); check((*dev)->USBDeviceReEnumerate(dev,0),"reset"); release_usb();
        for(int tries=0;tries<30;tries++) { usleep(200000); state=attach(); if(state==2) break; release_usb(); }
        if(state!=2) { fprintf(stderr,"firmware did not enumerate\n"); return 1; }
    }
    if(!pout || !pint) { fprintf(stderr,"missing runtime endpoints\n"); return 1; }
    master=posix_openpt(O_RDWR|O_NOCTTY|O_NONBLOCK);
    if(master<0 || grantpt(master) || unlockpt(master)) { perror("PTY"); return 1; }
    char *slave=ptsname(master); int fd=open(slave,O_RDWR|O_NOCTTY);
    struct termios t; if(fd<0 || tcgetattr(fd,&t)) { perror("slave"); return 1; }
    cfmakeraw(&t); t.c_cflag|=CLOCAL|CREAD; tcsetattr(fd,TCSANOW,&t); close(fd);
    /* Never overwrite an existing filesystem object. */
    if(symlink(slave,link)) { perror(link); return 1; }
    signal(SIGINT,stop); signal(SIGTERM,stop); signal(SIGPIPE,SIG_IGN);
    pthread_t rt,it; pthread_create(&it,NULL,reader,(void*)(uintptr_t)pint);
    configure(); command(6,3,0x89,NULL,0); command(8,3,0,NULL,0);
    command(11,3,0x80,NULL,0); command(11,3,0,NULL,0);
    check((*intf)->ClearPipeStallBothEnds(intf,pout),"clear out");
    check((*intf)->ClearPipeStallBothEnds(intf,pin),"clear in");
    configure(); command(6,3,0x89,NULL,0); command(8,3,0,NULL,0);
    pthread_create(&rt,NULL,reader,(void*)(uintptr_t)pin);
    printf("%s -> %s\n",link,slave); fflush(stdout);
    int was_open=0;
    while(!quit) {
        struct pollfd p={master,POLLIN,0}; int r=poll(&p,1,100);
        if(r<0 && errno!=EINTR) break;
        if(p.revents&POLLHUP) { if(was_open) { mcr(0); was_open=0; } usleep(10000); continue; }
        if(!(p.revents&POLLIN)) continue;
        if(!was_open) { mcr(1); was_open=1; }
        UInt8 b[4096]; ssize_t n=read(master,b,sizeof b); if(n<=0) continue;
        /* Do not retry failed writes: a timeout may have sent a partial packet. */
        for(ssize_t off=0;off<n;off+=64) {
            UInt32 k=(UInt32)(n-off>64?64:n-off);
            IOReturn e=(*intf)->WritePipeTO(intf,pout,b+off,k,0,5000);
            if(e) { fprintf(stderr,"USB write %08x (not retried)\n",e); quit=1; break; } tx+=k;
        }
    }
    quit=1; mcr(0); command(7,3,0,NULL,0);
    (*intf)->AbortPipe(intf,pin); (*intf)->AbortPipe(intf,pint);
    pthread_join(rt,NULL); pthread_join(it,NULL);
    unlink(link); close(master); release_usb();
    fprintf(stderr,"TX %llu RX %llu bytes\n",(unsigned long long)tx,(unsigned long long)rx); return 0;
}
