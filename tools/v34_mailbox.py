"""Host polling interface for V.34 on direct ADSP backends.

Matches the native shim's ADDSP synchronous interface: TXD0 is MSB first,
RXD0/1 are left aligned, and the DSP acknowledges transmit bit F. Service
before and after each bearer sample, never once per RTP packet.
"""
from eicon_idi import v34_rate


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

    def before_sample(self):
        dm = self.card.dm
        state = dm[0x3FC2]
        if self.active:
            if self.card.resident != 0x0261 or state < 0xB0:
                self.lapm.line_disturbed('V.34 retrain')
            elif state >= 0xC6:
                self.lapm.line_restored('V.34 synchronous state')
        if self.card.resident != 0x0261:
            self.pending = False
            return
        if self.pending or not dm[0x3FAD] & 0x8000:
            return
        if self.active or 0xC6 <= state <= 0xD0:
            rx_rate = v34_rate(dm[0x3F62])
            tx_rate = v34_rate(dm[0x3F61], 0x20) or rx_rate
            if tx_rate:
                width = tx_rate // 2400
                if self.tx_width is not None and width != self.tx_width:
                    self.lapm.line_disturbed('V.34 rate change')
                self.tx_width = width
                self.card.negotiated_downstream_bps = tx_rate
            if rx_rate:
                self.rx_width = rx_rate // 2400
                self.card.negotiated_upstream_bps = rx_rate
            if not self.active and self.tx_width and self.rx_width:
                self.active = True
                # Drop training-era receive words, as the native shim does.
                dm[0x3FAD] &= ~0x6000
                print(f'[v34-mailbox] synchronous data: TX {self.tx_width}, '
                      f'RX {self.rx_width} bits/datagram')
        bits = self.lapm.take(self.tx_width) if self.active else [1] * 16
        bits = bits + [1] * (16 - len(bits))
        dm[0x3F05] = sum(bit << (15 - i) for i, bit in enumerate(bits))
        dm[0x3F06] = dm[0x3F07] = 0
        self.pending = True
        self.tx_requests += 1

    def after_sample(self):
        if self.card.resident != 0x0261:
            self.pending = False
            return
        dm = self.card.dm
        if self.pending and not dm[0x3FAD] & 0x8000:
            self.pending = False
            self.tx_accepted += 1
        if not self.active:
            return
        for mask, address in ((0x2000, 0x3FAE), (0x4000, 0x3FAF)):
            if dm[0x3FAD] & mask:
                word = dm[address]
                self.lapm.feed([(word >> (15 - bit)) & 1
                                for bit in range(self.rx_width)])
                dm[0x3FAD] &= ~mask
                self.rx_datagrams += 1
