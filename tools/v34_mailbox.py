"""Host polling interface for V.34 and V.90 on direct ADSP backends.

Matches the native shim's ADDSP synchronous interface: TXD0 is MSB first,
RXD0/1 are left aligned, and the DSP acknowledges transmit bit F. Service
before and after each bearer sample, never once per RTP packet.

The two V.90 pages carry one wide direction each:

- V90D (`0x026A`, the answering digital end) transmits the PCM downstream:
  `21 + (DATASTATEspeedTx & 0x1f)` bits per 8000/6-Hz datagram across
  TXD0..TXD2, selected by DATASTATEspeedTx bit 5. It receives the V.34
  upstream in the V.34 convention.
- V90A (`0x026B`, the calling analogue end) transmits the V.34 upstream in the
  V.34 convention and receives the downstream: `21 + (DATASTATESpeed & 0x1f)`
  bits, selected by DATASTATESpeed bit 13, published left aligned in RXD0,
  RXD1 and DM(0x3FB1) under DI_control bit 13 (`PM 0x2F9D..0x2FA8` of the
  109-789 page).
"""
import os

from eicon_idi import v34_rate

V34_ID = 0x0261
V90D_ID = 0x026A
V90A_ID = 0x026B
PAGES = (V34_ID, V90D_ID, V90A_ID)
RXD2 = 0x3FB1
V90A_TX_SCHEDULE = 0x1214   # datagrams left in the V90A upstream period

# ADDSP guide: V90D's TXD0 bit 0 is the oldest bit. Nothing has checked that
# against a receiver that cares, so the opposite order stays selectable.
V90D_TX_MSB_FIRST = os.environ.get('EICON_V90D_TX_MSB_FIRST', '0') != '0'
# Diagnostic: append every bit fed to LAPM, as ASCII '0'/'1', one datagram per
# line, so the receive stream can be searched offline for flags and frames.
RX_TRACE = os.environ.get('EICON_MAILBOX_RX_TRACE', '')
TX_TRACE = os.environ.get('EICON_MAILBOX_TX_TRACE', '')


def claim_tx_mailbox(pm):
    """Give the explicit host source ownership, as the native shim does.

    TIKRNL otherwise overwrites TXD0 with mark or its unused host ring.
    The two shipped task families differ by one instruction after mark fill.
    Validate the whole resident signature before suppressing any store.
    """
    matches = []
    for offsets in ((0, 0x62, 0x64, 0x68, 0x70),
                    (0, 0x63, 0x65, 0x69, 0x71)):
        for base in range(0x900 - offsets[-1]):
            addresses = [base + offset for offset in offsets]
            if all(pm[a] == op for a, op in zip(addresses,
                    (0x93F05A, 0x93F05F, 0x93F05F, 0x93F06F, 0x93F07F))):
                matches.append(addresses)
    if len(matches) != 1:
        raise RuntimeError(f'V.34 TX ownership signature matched {len(matches)} times')
    for address in matches[0]:
        pm[address] = 0
    print('[v34-mailbox] host owns TXD stores at ' +
          '/'.join(f'{address:04x}' for address in matches[0]))


def claim_wide_rx_mailbox(pm):
    """Stop the analogue kernel consuming wide receive datagrams itself.

    The analogue kernel's data path (`PM 0x0798..0x07CD` live) reads RXD0 and
    leaves DI_control bit 13 for the host while the datagram is narrower than
    16 bits, which is why V.34 works through this mailbox. For a wider
    datagram -- the V.90 downstream -- it reads RXD0/RXD1/RXD2 into its own
    host ring and clears bit 13 itself (`AR = $DFFF; AR = AR AND AY0;
    DM($3FAD) = AR`) inside the same sample, so a host polling between samples
    never sees one. NOP the store, as claim_tx_mailbox() does for TXD. Kernels
    without this path (the PRI kernel) match nothing and are left alone.
    """
    signature = (0x4DFFFA, 0x23820F, 0x93FADA)
    matches = [base for base in range(0x900)
               if tuple(pm[base:base + 3]) == signature]
    if len(matches) > 1:
        raise RuntimeError(
            f'wide RX ownership signature matched {len(matches)} times')
    if not matches:
        return None
    pm[matches[0] + 2] = 0
    print(f'[v34-mailbox] host owns wide RX datagrams '
          f'(kernel clear at {matches[0] + 2:04x} suppressed)')
    return matches[0] + 2


def v90_datagram_bits(value, format_mask):
    """Bits in one 8000/6-Hz V.90 datagram, or None if not the V.90 table."""
    return 21 + (value & 0x1F) if value & format_mask else None


def v34_datagram_bits(value, format_mask):
    rate = v34_rate(value, format_mask)
    return rate // 2400 if rate is not None else None


class V34Mailbox:
    def __init__(self, card, lapm):
        self.card = card
        self.lapm = lapm
        self.active = False
        self.pending = False
        self.tx_width = None
        self.rx_width = None
        self.tx_requests = 0
        self.tx_accepted = 0
        self.rx_datagrams = 0
        self.tx_token = None
        self.rx_trace = open(RX_TRACE, 'w', buffering=1) if RX_TRACE else None
        self.tx_trace = open(TX_TRACE, 'w', buffering=1) if TX_TRACE else None

    def _take(self):
        bits = self.lapm.take(self.tx_width)[:self.tx_width]
        if self.tx_trace is not None:
            self.tx_trace.write(''.join(map(str, bits)) + '\n')
        return bits

    def _feed(self, bits):
        if self.rx_trace is not None:
            self.rx_trace.write(''.join(map(str, bits)) + '\n')
        self.lapm.feed(bits)

    def _widths(self):
        """(tx bits, rx bits) for the resident page, either possibly None."""
        dm = self.card.dm
        page = self.card.resident
        if page == V90D_ID:
            return (v90_datagram_bits(dm[0x3F61], 0x0020),
                    v34_datagram_bits(dm[0x3F62], 0x2000))
        if page == V90A_ID:
            return (v34_datagram_bits(dm[0x3F61], 0x0020),
                    v90_datagram_bits(dm[0x3F62], 0x2000))
        rx = v34_datagram_bits(dm[0x3F62], 0x2000)
        return (v34_datagram_bits(dm[0x3F61], 0x0020) or rx, rx)

    def _rates(self, tx, rx):
        """Line rates for the two widths, for the card's negotiated fields."""
        page = self.card.resident
        tx_hz = 8000 / 6 if page == V90D_ID else 2400
        rx_hz = 8000 / 6 if page == V90A_ID else 2400
        return round(tx * tx_hz), round(rx * rx_hz)

    def before_sample(self):
        dm = self.card.dm
        state = dm[0x3FC2]
        page = self.card.resident
        if self.active:
            if page not in PAGES or state < 0xB0:
                self.lapm.line_disturbed(
                    'V.90 retrain' if page in (V90D_ID, V90A_ID)
                    else 'V.34 retrain')
            elif state >= 0xC6:
                self.lapm.line_restored('synchronous state')
        if page not in PAGES:
            self.pending = False
            return
        if page == V90A_ID:
            # The V90A page (109-789) schedules the upstream as 3 datagrams per
            # 4 symbols: DM(0x1213) counts symbols, DM(0x1214) the datagrams
            # left in the period, and each step of DM(0x1214) is one TXD0 read
            # (PM 0x3D84) and one new request. Bit F stays set across a whole
            # period, so neither "F set" nor "F cleared" counts datagrams --
            # waiting for F to clear supplied 800/s against the page's 2400.
            # Supply on the scheduler step, as the kernel's own fill does.
            if not dm[0x3FAD] & 0x8000:
                return
        elif self.pending or not dm[0x3FAD] & 0x8000:
            return
        # V90A publishes its downstream width transiently through 0xC6..0xCD
        # (0x21e4, 25 bits, before the final 0x21f1) and its receive is
        # training until 0xD0, so start LAPM -- and its T400 -- only there.
        ready = (state == 0xD0 if page == V90A_ID else 0xC6 <= state <= 0xD0)
        if self.active or ready:
            tx, rx = self._widths()
            if tx:
                if self.tx_width is not None and tx != self.tx_width:
                    self.lapm.line_disturbed('rate change')
                self.tx_width = tx
            if rx:
                if (self.active and self.rx_width is not None
                        and rx != self.rx_width):
                    print(f'[v34-mailbox] RX width {self.rx_width} -> {rx} '
                          f'bits/datagram at TrnProgress 0x{state:04x}')
                self.rx_width = rx
            if self.tx_width and self.rx_width:
                down, up = self._rates(self.tx_width, self.rx_width)
                if page == V90A_ID:
                    down, up = up, down
                self.card.negotiated_downstream_bps = down
                self.card.negotiated_upstream_bps = up
            if not self.active and self.tx_width and self.rx_width:
                self.active = True
                # Drop training-era receive words, as the native shim does.
                dm[0x3FAD] &= ~0x6000
                print(f'[v34-mailbox] synchronous data: TX {self.tx_width}, '
                      f'RX {self.rx_width} bits/datagram at TrnProgress '
                      f'0x{state:04x} (speedTx 0x{dm[0x3F61]:04x}, '
                      f'speed 0x{dm[0x3F62]:04x})')
        if page == V90A_ID:
            token = dm[V90A_TX_SCHEDULE]
            if token == self.tx_token:
                return
            self.tx_token = token
        if page == V90D_ID:
            bits = self._take() if self.active else [1] * 48
            bits = bits + [1] * (48 - len(bits))
            if V90D_TX_MSB_FIRST:
                words = [sum(bits[w * 16 + i] << (15 - i) for i in range(16))
                         for w in range(3)]
            else:
                words = [sum(bits[w * 16 + i] << i for i in range(16))
                         for w in range(3)]
            dm[0x3F05], dm[0x3F06], dm[0x3F07] = words
        else:
            bits = self._take() if self.active else [1] * 16
            bits = bits + [1] * (16 - len(bits))
            dm[0x3F05] = sum(bit << (15 - i) for i, bit in enumerate(bits))
            dm[0x3F06] = dm[0x3F07] = 0
        self.tx_requests += 1
        if page == V90A_ID:
            self.tx_accepted += 1
            return
        self.pending = True

    def after_sample(self):
        if self.card.resident not in PAGES:
            self.pending = False
            return
        dm = self.card.dm
        if self.pending and not dm[0x3FAD] & 0x8000:
            self.pending = False
            self.tx_accepted += 1
        if not self.active:
            return
        if self.card.resident == V90A_ID:
            # One wide datagram per flag: RXD0, RXD1, RXD2, oldest at b15.
            if dm[0x3FAD] & 0x2000:
                words = (dm[0x3FAE], dm[0x3FAF], dm[RXD2])
                self._feed([(words[i // 16] >> (15 - i % 16)) & 1
                            for i in range(self.rx_width)])
                dm[0x3FAD] &= ~0x2000
                self.rx_datagrams += 1
            return
        for mask, address in ((0x2000, 0x3FAE), (0x4000, 0x3FAF)):
            if dm[0x3FAD] & mask:
                word = dm[address]
                self._feed([(word >> (15 - bit)) & 1
                            for bit in range(self.rx_width)])
                dm[0x3FAD] &= ~mask
                self.rx_datagrams += 1
