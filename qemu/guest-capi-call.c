/* Freestanding Linux x86-64 CAPI application. All protocol responses come
 * from the guest driver and board firmware; this program is their client.
 * Test fixture: TE/NT ports 1/2 and 3/4, two B channels per port.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
static long fd = -1, last_result;
static u16 app, next_number = 0x200;
static u8 tx[4096], rx[4096];
static u32 tx_len, rx_len, expected, callers, signal_mask, bearer_mask;
static u32 received_mask, sent_mask, b3_closed, b3_conf, signal_closed, signal_conf, signal_requested;
#define PAYLOAD_BYTES 2048
static int phase, call_count, round_index=1;
struct endpoint { u32 plci, ncci; u16 b3_msg, data_msg, b3_disc_msg, sig_disc_msg; };
static struct endpoint endpoints[8];
static char output[1024];
static u32 output_len;

static long syscall3(long n, long a, long b, long c) {
    long r;
    __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c) : "rcx", "r11", "memory");
    return r;
}
static void os_exit(int status) __attribute__((noreturn));
static void os_exit(int status) { syscall3(60, status, 0, 0); for (;;) {} }
static void out_s(const char *s) { while (*s && output_len < sizeof(output)-1) output[output_len++] = *s++; }
static void out_d(u32 v) { char b[12]; int n=0; do { b[n++]=(char)('0'+v%10); v/=10; } while(v); while(n) output[output_len++]=b[--n]; }
static void out_x(u32 v) { const char *hex="0123456789abcdef"; int i; for(i=7;i>=0;i--) output[output_len++]=hex[(v>>(i*4))&15]; }

static void out_line(void) { long n; u32 at=0; output[output_len++]='\n'; while(at<output_len) { n=syscall3(1,1,(long)(output+at),output_len-at);if(n<=0)os_exit(99);at+=(u32)n; } output_len=0; }
static void line(const char *s) { out_s(s);out_line(); }
static void fail(int stage, const char *detail) __attribute__((noreturn));
static void fail(int stage, const char *detail) {
    u16 error=0;
    if(fd>=0)syscall3(16,fd,0x80024321,(long)&error);
    out_s("BRI_CAPI_CALL_FAILED stage=");out_d((u32)stage);out_s(" detail=");out_s(detail);
    out_s(" code=");out_x(error);out_s(" result=");out_x((u32)last_result);out_line();os_exit(stage);
}
static void need(int good, int stage, const char *detail) { if(!good)fail(stage,detail); }
static u16 word(const u8 *p) { return (u16)(p[0]|(p[1]<<8)); }
static u32 dword(const u8 *p) { return (u32)p[0]|((u32)p[1]<<8)|((u32)p[2]<<16)|((u32)p[3]<<24); }
static void byte(u8 v) { need(tx_len<sizeof(tx),90,"tx-overflow");tx[tx_len++]=v; }
static void half(u16 v) { byte((u8)v);byte((u8)(v>>8)); }
static void full(u32 v) { half((u16)v);half((u16)(v>>16)); }
static void header(u16 command,u32 control,u16 number) { tx_len=0;half(0);half(app);half(command);half(number);full(control); }
static void send_body(const u8 *body,u32 size) {
    u32 i, length=tx_len;
    tx[0]=(u8)length;tx[1]=(u8)(length>>8);
    for(i=0;i<size;i++)byte(body[i]);
    last_result=syscall3(1,fd,(long)tx,tx_len);
    need(last_result==(long)tx_len,20,"write");
}
static void send(void) { send_body((const u8 *)0,0); }
static void b_protocol(void) { byte(9);half(0);half(1);half(0);byte(0);byte(0);byte(0); }
static void response(u16 command,u32 control,u16 number) { header(command,control,number);send(); }
static void receive(void) {
    struct { int fd;u16 events,revents; } pollfd;
    u32 mlen;
    pollfd.fd=(int)fd;pollfd.events=1;pollfd.revents=0;
    last_result=syscall3(7,(long)&pollfd,1,2000);
    need(last_result==1 && (pollfd.revents&1),21,"read-timeout-or-poll-error");
    last_result=syscall3(0,fd,(long)rx,sizeof(rx));
    need(last_result>=12,22,"read");rx_len=(u32)last_result;
    mlen=word(rx);
    need(mlen>=12 && mlen<=rx_len && word(rx+2)==app,23,"header");
    if(word(rx+4)==0x8286)need(mlen>=22 && rx_len==mlen+word(rx+16),23,"data-length");
    else need(rx_len==mlen,23,"message-length");
    out_s("BRI_CAPI_EVENT command=");out_x(word(rx+4));out_s(" control=");out_x(dword(rx+8));
    out_s(" number=");out_d(word(rx+6));out_s(" length=");out_d(rx_len);out_line();
}
static int by_plci(u32 control) { int i;for(i=0;i<8;i++)if((expected&(1u<<i))&&endpoints[i].plci==(control&0xffff))return i;return -1; }
static int by_ncci(u32 control) { int i;for(i=0;i<8;i++)if((expected&(1u<<i))&&endpoints[i].ncci==control)return i;return -1; }
static void endpoint_line(const char *name,int i,u32 control) { out_s(name);out_s(" endpoint=");out_d((u32)i);out_s(" control=");out_x(control);out_line(); }
static void payload(u8 *p,int i) {
    static const u8 prefix[16]={0,255,126,125,17,19,'B','R','I','C','A','P','I',128,85,170};
    u32 j;for(j=0;j<PAYLOAD_BYTES;j++)p[j]=(u8)(j*37+(i+1)*11+round_index*17);
    for(j=0;j<16;j++)p[j]=prefix[j];
    p[16]=(u8)(i+1);p[17]=(u8)round_index;
}
static u32 crc32(const u8 *p,u32 n) {
    u32 crc=0xffffffff,i,j;for(i=0;i<n;i++){crc^=p[i];for(j=0;j<8;j++)crc=(crc>>1)^((crc&1)?0xedb88320:0);}
    return crc^0xffffffff;
}
static void baseline(void) {
    struct {long seconds,nanoseconds;} delay={0,100000000};
    char path[]="/proc/net/eicon/adapter1/info";long input,n,w;int i;
    syscall3(35,(long)&delay,0,0);
    for(i=0;i<4;i++) {
        path[23]=(char)('1'+i);
        out_s("BRI_CAPI_BASELINE_INFO controller=");out_d((u32)i+1);out_line();
        input=syscall3(2,(long)path,0,0);need(input>=0,7,"baseline-open");
        while((n=syscall3(0,input,(long)rx,sizeof(rx)))>0) {
            w=syscall3(1,1,(long)rx,n);need(w==n,7,"baseline-print");
        }
        need(n==0,7,"baseline-read");syscall3(3,input,0,0);
    }
}

static void start_calls(void) {
    int i,e;u32 controller;
    for(i=0;i<call_count;i++) {
        e=(i/2)*4+i%2;controller=(u32)(e/2+1);
        header(0x8002,controller,(u16)(0x100+i));half(2); /* unrestricted digital */
        byte(4);byte(0x80);byte('1');byte('0');byte((u8)('1'+i%2));
        byte(0);byte(0);byte(0);b_protocol();byte(0);byte(0);byte(0);byte(0);send();
    }
}
static void advance(void) {
    int i;u8 data[PAYLOAD_BYTES];
    if(phase==0 && signal_mask==expected) {
        phase=1;line("BRI_CAPI_SIGNALS_CONNECTED");
        for(i=0;i<8;i++)if(callers&(1u<<i)) {
            endpoints[i].b3_msg=next_number++;header(0x8082,endpoints[i].plci,endpoints[i].b3_msg);byte(0);send();
        }
    }
    if(phase==1 && bearer_mask==expected) {
        phase=2;out_s("BRI_CAPI_CALLS_CONNECTED endpoints=");out_d((u32)call_count*2);out_line();
        for(i=0;i<8;i++)if(expected&(1u<<i)) {
            payload(data,i);endpoints[i].data_msg=next_number++;
            header(0x8086,endpoints[i].ncci,endpoints[i].data_msg);full(0);half(PAYLOAD_BYTES);half((u16)(0x400+i));half(0);send_body(data,PAYLOAD_BYTES);
        }
    }
    if(phase==2 && received_mask==expected && sent_mask==expected) {
        phase=3;out_s("BRI_CAPI_MEDIA_OK endpoints=");out_d((u32)call_count*2);out_s(" bytes=");out_d(PAYLOAD_BYTES);out_s(" round=");out_d((u32)round_index);out_line();
        for(i=0;i<8;i++)if(expected&(1u<<i)) {
            endpoints[i].b3_disc_msg=next_number++;header(0x8084,endpoints[i].ncci,endpoints[i].b3_disc_msg);byte(0);send();
        }
    }
    if(phase==3 && b3_closed==expected && b3_conf==expected) {
        phase=4;
        for(i=0;i<8;i++)if((callers&(1u<<i)) && !(signal_closed&(1u<<i))) {
            signal_requested|=1u<<i;endpoints[i].sig_disc_msg=next_number++;
            header(0x8004,endpoints[i].plci,endpoints[i].sig_disc_msg);byte(0);send();
        }
    }
    if(phase==4 && signal_closed==expected && signal_conf==signal_requested) {
        phase=5;out_s("BRI_CAPI_TEARDOWN_OK endpoints=");out_d((u32)call_count*2);out_line();
    }
}
static void dispatch(void) {
    u16 command=word(rx+4),number=word(rx+6),reason;u32 control=dword(rx+8),i,j,length;int e=-1;u8 data[PAYLOAD_BYTES];
    switch(command) {
    case 0x8102: /* CONNECT_CONF */
        need(rx_len==14 && !word(rx+12) && number>=0x100 && number<0x100+call_count,30,"connect-conf");
        i=number-0x100;e=(int)((i/2)*4+i%2);need((control&0x7f)==(u32)(e/2+1) && (control>>8),30,"caller-plci");endpoints[e].plci=control;break;
    case 0x8202: /* CONNECT_IND: identify the caller's requested slot by dialled digit */
        need(rx_len>=16 && (control&0x7f)>=1 && (control&0x7f)<=4,31,"connect-ind");
        length=rx[14];need(length>=2 && 15+length<=rx_len,31,"called-number");
        i=rx[14+length];need(i=='1'||i=='2',31,"called-slot");e=(int)(((control&0x7f)-1)*2+i-'1');
        need((expected&(1u<<e)) && !(callers&(1u<<e)) && !endpoints[e].plci,31,"answer-slot");endpoints[e].plci=control;
        header(0x8302,control,number);half(0);b_protocol();byte(0);byte(0);byte(0);byte(0);send();break;
    case 0x8203: /* CONNECT_ACTIVE_IND */
        e=by_plci(control);need(e>=0 && !(signal_mask&(1u<<e)),32,"signal-active");response(0x8303,control,number);
        signal_mask|=1u<<e;endpoint_line("BRI_CAPI_SIGNAL_ACTIVE",e,control);break;
    case 0x8182: /* CONNECT_B3_CONF */
        e=by_plci(control);need(e>=0 && rx_len==14 && !word(rx+12) && number==endpoints[e].b3_msg,33,"b3-conf");endpoints[e].ncci=control;break;
    case 0x8282: /* CONNECT_B3_IND */
        e=by_plci(control);need(e>=0 && !(callers&(1u<<e)),34,"b3-ind");endpoints[e].ncci=control;
        header(0x8382,control,number);half(0);byte(0);send();break;
    case 0x8283: /* CONNECT_B3_ACTIVE_IND */
        e=by_ncci(control);need(e>=0 && !(bearer_mask&(1u<<e)),35,"b3-active");response(0x8383,control,number);
        bearer_mask|=1u<<e;endpoint_line("BRI_CAPI_BEARER_ACTIVE",e,control);break;
    case 0x8186: /* DATA_B3_CONF */
        e=by_ncci(control);need(e>=0 && rx_len==16 && word(rx+12)==0x400+e && !word(rx+14) && number==endpoints[e].data_msg,36,"data-conf");
        need(!(sent_mask&(1u<<e)),36,"duplicate-data-conf");sent_mask|=1u<<e;endpoint_line("BRI_CAPI_TX_OK",e,control);break;
    case 0x8286: /* DATA_B3_IND */
        e=by_ncci(control);need(e>=0 && !(received_mask&(1u<<e)) && word(rx+16)==PAYLOAD_BYTES,37,"data-ind");
        payload(data,e^2);for(j=0;j<PAYLOAD_BYTES;j++)need(rx[word(rx)+j]==data[j],37,"data-mismatch");
        header(0x8386,control,number);half(word(rx+18));send();received_mask|=1u<<e;
        out_s("BRI_CAPI_DATA_OK endpoint=");out_d((u32)e);out_s(" bytes=");out_d(PAYLOAD_BYTES);out_s(" crc32=");out_x(crc32(rx+word(rx),PAYLOAD_BYTES));out_line();break;
    case 0x8184: /* DISCONNECT_B3_CONF */
        e=by_ncci(control);need(e>=0 && rx_len==14 && !word(rx+12) && number==endpoints[e].b3_disc_msg,38,"b3-disconnect-conf");b3_conf|=1u<<e;break;
    case 0x8284: /* DISCONNECT_B3_IND */
        e=by_ncci(control);need(e>=0 && rx_len>=15 && !word(rx+12),39,"b3-disconnect-ind");response(0x8384,control,number);b3_closed|=1u<<e;endpoint_line("BRI_CAPI_B3_CLEARED",e,control);break;
    case 0x8104: /* DISCONNECT_CONF */
        e=by_plci(control);need(e>=0 && rx_len==14 && !word(rx+12) && number==endpoints[e].sig_disc_msg,40,"disconnect-conf");signal_conf|=1u<<e;break;
    case 0x8204: /* DISCONNECT_IND */
        e=by_plci(control);need(e>=0 && rx_len==14,41,"disconnect-ind");reason=word(rx+12);need(reason==0 || reason==0x3490,41,"disconnect-reason");
        response(0x8304,control,number);signal_closed|=1u<<e;endpoint_line("BRI_CAPI_SIGNAL_CLEARED",e,control);break;
    case 0x8208: /* INFO_IND */
        need(rx_len>=15,42,"info-ind");response(0x8308,control,number);break;
    default:fail(43,"unexpected-message");
    }
    advance();
}
int main(int argc,char **argv) {
    u32 params[3]={8,8,PAYLOAD_BYTES},profile[16],controller;int i,e;u32 messages=0;
    call_count=4;
    if(argc>1) { need(argv[1][0]>='1'&&argv[1][0]<='4'&&!argv[1][1],1,"call-count");call_count=argv[1][0]-'0'; }
    if(argc>2) {
        const char *p=argv[2];u32 value=0;
        need(*p>='1' && *p<='9',1,"round-index");
        while(*p) {
            need(*p>='0' && *p<='9',1,"round-index");
            value=value*10+(u32)(*p++-'0');
            need(value<=64,1,"round-index");
        }
        round_index=(int)value;
    }
    for(i=0;i<call_count;i++) { e=(i/2)*4+i%2;expected|=(1u<<e)|(1u<<(e+2));callers|=1u<<e; }
    out_s("BRI_CAPI_CALL_BEGIN calls=");out_d((u32)call_count);out_s(" round=");out_d((u32)round_index);out_line();
    last_result=syscall3(2,(long)"/dev/capi20",2050,0);need(last_result>=0,2,"open");fd=last_result;
    last_result=syscall3(16,fd,0x400c4301,(long)params);need(last_result>0 && last_result<65536,3,"register");app=(u16)last_result;
    profile[0]=0;last_result=syscall3(16,fd,0xc0404309,(long)profile);need(!last_result && (profile[0]&0xffff)==4,4,"controller-count");
    for(controller=1;controller<=4;controller++) {
        profile[0]=controller;last_result=syscall3(16,fd,0xc0404309,(long)profile);need(!last_result && (profile[0]>>16)==2,5,"profile");
        out_s("BRI_CAPI_PROFILE_OK controller=");out_d(controller);out_line();
        header(0x8005,controller,(u16)controller);full(0);full(1);full(0);byte(0);byte(0);send();receive();
        need(rx_len==14 && word(rx+4)==0x8105 && word(rx+6)==controller && dword(rx+8)==controller && !word(rx+12),6,"listen-conf");
        out_s("BRI_CAPI_LISTEN_OK controller=");out_d(controller);out_line();
    }
    baseline();
    start_calls();
    while(phase!=5 && messages++<1000) { receive();dispatch(); }
    need(phase==5,44,"message-budget");
    last_result=syscall3(3,fd,0,0);need(!last_result,45,"close");fd=-1;
    line("BRI_CAPI_CALLS_OK");line("BRI_CAPI_CLIENT_OK");return 0;
}
