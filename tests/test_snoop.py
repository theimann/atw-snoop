#!/usr/bin/env python3
"""atw-snoop's T425 program (build_snoop.py) in the emulator (temu.py): boot, parameters, font, then cells written into
the grid as the 68000 writes them (big-endian words). After each pass the page must equal a reference drawn here
from the same font, pixel for pixel; the area cleared at the start must hold the clear colour; a stop command must
stop it. Then the slots: values handed over raw, formatted by the T425 into the grid (each kind, edge values),
checked cell by cell and pixel by pixel. Says nothing about the real card's speed or its cache (the evict pass is shortened here: P6 = 2)."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 't425'))
sys.path.insert(0, HERE)
import temu  # noqa: E402
import build_snoop as bd  # noqa: E402

SH = temu.SHARED - temu.MINT
VR = temu.VRAM - temu.MINT
COLS, ROWS = 40, 12
STRIDE = 1280
PAGE_OFF = 0x1000 + 3 * STRIDE           # a panel not at the page's start
GRID = 0x400
PEN = 240
CLEAR = (COLS * 8 + 64) * 0 + STRIDE * (ROWS * 16 + 4)


def le(v):
    return (v & 0xFFFFFFFF).to_bytes(4, 'little')


class Card:
    def __init__(self):
        self.t = temu.T425()
        self.font = bd.font()

    def slice(self, n=3_000_000):
        try:
            self.t.run(n)
            return True
        except temu.Halt as e:
            if 'limite' in str(e):
                return True
            if 'stopp' in str(e):
                return False
            raise

    def boot(self, cells, slots=0):
        t = self.t
        for i, v in enumerate(cells):                       # the 68000 fills the grid before the boot
            self.put(i, v)
        loader, payload = bd.build_loader(), bd.build_main()
        t.link_in.extend([len(loader)] + list(loader))
        t.boot()
        t.link_in.extend(le(len(payload)) + payload)
        t.link_in.extend(b''.join(le(v) for v in [PAGE_OFF, STRIDE, COLS, ROWS, GRID, PEN, 2, CLEAR, PEN + 3, 0, slots] + [0] * 5))
        t.link_in.extend(self.font)
        self.slice()
        assert t.mem[SH] == 0x5A, 'not running'

    def put(self, i, v):                                    # a big-endian word, as the 68000 writes it
        self.t.mem[SH + GRID + 2 * i] = v >> 8
        self.t.mem[SH + GRID + 2 * i + 1] = v & 0xFF

    def page(self):
        return self.t.mem[VR:VR + 0x200000]


def reference(card, cells, base):
    ref = bytearray(base)
    for i, v in enumerate(cells):
        ch, attr = v >> 8, v & 0xFF
        fg, bg = PEN + (attr & 15), PEN + (attr >> 4)
        x, y = (i % COLS) * 8, (i // COLS) * 16
        for r in range(16):
            bits = card.font[ch * 16 + r]
            o = PAGE_OFF + (y + r) * STRIDE + x
            ref[o:o + 8] = bytes(fg if bits & (0x80 >> k) else bg for k in range(8))
    return ref


SLOTS = 0x1000                          # the slots' offset in shared memory (P10)


def be(v, n=4):
    return (v & ((1 << 8 * n) - 1)).to_bytes(n, 'big')


def fmt(kind, v, n, mx, data):
    """The cells a slot must show (character codes), as snoop_hex/snoop_dec/snoop_bar/snoop_mem write them."""
    if kind == 1:
        return [ord(c) for c in '%0*X' % (n, v & ((1 << 4 * n) - 1))][-n:]
    if kind == 2:
        if mx > 0:                                            # scaled: v x mx / 1000, truncated as the T425 divides
            v = int(v * mx / 1000) if abs(v * mx) < 1 << 31 else (v * mx & 0xFFFFFFFF)
        s = str(abs(v))[-n:]
        if v < 0 and len(s) < n:
            s = '-' + s
        return [ord(c) for c in s.rjust(n)]
    if kind == 3:
        mx = mx if mx > 0 else 1
        v = min(max(v, 0), mx)
        while mx > 0xFFFFF:
            mx >>= 1
            v >>= 1
        e = v * n * 8 // mx
        out = []
        for _ in range(n):
            out.append(127 if e >= 8 else 8 + e if e > 0 else 32)
            e -= 8
        return out
    out = []
    for k in range(n):
        out += [ord(c) for c in '%02X' % data[k]] + ([32] if k < n - 1 else [])
    return out


def slot_test(rnd):
    c = Card()
    cells = [(ord(' ') << 8) | 0xC1] * (COLS * ROWS)
    c.boot(cells, SLOTS)
    defs = [(1, 0, 0, 0x05, 8, 0), (1, 10, 0, 0x2A, 2, 0), (2, 0, 1, 0x05, 6, 0), (2, 8, 1, 0x05, 3, 0),
            (3, 0, 2, 0x0B, 20, 1000), (3, 22, 2, 0x0B, 5, 3_000_000_000 // 3), (4, 0, 3, 0x0E, 12, 0),
            (2, 30, 4, 0x0E, 11, 0), (2, 50, 4, 0x0E, 7, 26042)]
    data = [bytes(32)] * len(defs)
    gen = [0] * len(defs)
    sh = c.t.mem

    def define(i):
        kind, col, row, attr, n, mx = defs[i]
        d = SH + SLOTS + 16 + 16 * i
        gen[i] = (gen[i] + 1) & 255
        sh[d + 4:d + 16] = bytes([kind, attr, n, gen[i]]) + be(2 * (row * COLS + col), 2) + bytes(2) + be(mx)
        if kind == 4:                                         # the spaces between bytes are the 68000's
            for k in range(1, n):
                cells[row * COLS + col + 3 * k - 1] = 32 << 8 | attr
                c.put(row * COLS + col + 3 * k - 1, cells[row * COLS + col + 3 * k - 1])
        sh[SH + SLOTS + 3] = i + 1

    def setv(i, v):
        d = SH + SLOTS + 16 + 16 * i
        sh[d:d + 4] = be(v)

    def setdata(i, b):
        data[i] = b
        sh[SH + SLOTS + 16 + 16 * 128 + 32 * i:SH + SLOTS + 16 + 16 * 128 + 32 * i + 32] = b

    def check(vals, what):
        for i, (kind, col, row, attr, n, mx) in enumerate(defs):
            want = fmt(kind, vals[i], n, mx, data[i])
            for k, ch in enumerate(want):
                cells[row * COLS + col + k] = ch << 8 | attr
        for i in range(COLS * ROWS):
            got = sh[SH + GRID + 2 * i] << 8 | sh[SH + GRID + 2 * i + 1]
            assert got == cells[i], '%s: cell %d,%d is %04X, not %04X' % (what, i % COLS, i // COLS, got, cells[i])
        base = bytearray(0x200000)
        base[PAGE_OFF:PAGE_OFF + CLEAR] = bytes([PEN + 3]) * CLEAR
        assert c.page()[:PAGE_OFF + CLEAR + 64] == reference(c, cells, base)[:PAGE_OFF + CLEAR + 64], what + ': picture'

    for i in range(len(defs)):
        define(i)
    rounds = [[0x1234ABCD, 0x7F, 0, 7, 0, 0, 0, 0, 0],
              [-1, 0x100, -42, 12345, 500, 2_000_000_000, 0, -2147483647, 1],
              [0xDEAD, 0xFE, 999999, -5, 1000, 1, 0, 123, 384],
              [0, 0, 1234567, -99, 1500, -7, 0, 0, 26880],
              [7, 9, -100000, 0, 999, 999_999_999, 0, 2147483647, 77]]
    for r, vals in enumerate(rounds):
        for i, v in enumerate(vals):
            setv(i, v)
        setdata(6, bytes(rnd.randrange(256) for _ in range(32)))
        c.slice()
        check(vals, 'round %d' % r)
    cells[:] = [(ord('#') << 8) | 0xC1] * (COLS * ROWS)      # the cells overwritten (snoop_cls): a new generation
    for i in range(COLS * ROWS):
        c.put(i, cells[i])
    for i in range(len(defs)):                               # snoop_cls: the byte slots' spaces again, new generations
        d = SH + SLOTS + 16 + 16 * i
        gen[i] += 1
        sh[d + 7] = gen[i]
        kind, col, row, attr, n, mx = defs[i]
        for k in range(1, n if kind == 4 else 0):
            cells[row * COLS + col + 3 * k - 1] = 32 << 8 | attr
            c.put(row * COLS + col + 3 * k - 1, cells[row * COLS + col + 3 * k - 1])
    c.slice()
    check(rounds[-1], 'after a new generation')
    print('slots: %d slots, %d rounds, every kind formatted and drawn exactly' % (len(defs), len(rounds) + 1))


def main():
    rnd = random.Random(7)
    c = Card()
    cells = [(ord(' ') << 8) | 0xC1] * (COLS * ROWS)
    for i, ch in enumerate(b'atw-snoop test: Hello, Mega STE!'):
        cells[i] = ch << 8 | 0x05
    cells[COLS * 2:COLS * 2 + 8] = [(k + 1) << 8 | 0x0A for k in range(8)]   # the bar glyphs
    c.boot(cells)
    base = bytearray(0x200000)
    base[PAGE_OFF:PAGE_OFF + CLEAR] = bytes([PEN + 3]) * CLEAR
    want = reference(c, cells, base)
    got = c.page()
    assert got[:PAGE_OFF + CLEAR + 64] == want[:PAGE_OFF + CLEAR + 64], 'first pass differs'
    print('pass 1: %d cells, %d instructions' % (len(cells), c.t.icount))
    for step in range(3):                                   # random changes, also single halves of a pair
        for _ in range(25):
            i = rnd.randrange(len(cells))
            cells[i] = rnd.randrange(32, 127) << 8 | rnd.randrange(256)
            c.put(i, cells[i])
        n0 = c.t.icount
        c.slice()
        want = reference(c, cells, base)
        assert c.page()[:PAGE_OFF + CLEAR + 64] == want[:PAGE_OFF + CLEAR + 64], 'step %d differs' % step
        print('step %d: 25 changes drawn (%d instructions in the slice)' % (step + 1, c.t.icount - n0))
    assert c.t.mem[SH + 3] != 0, 'no pass counter'
    c.t.mem[SH + 0x101] = 6
    c.t.mem[SH + 0x100] = 9
    assert not c.slice(), 'did not stop'
    assert c.t.mem[SH + 1] == 9
    try:
        from PIL import Image
        pal = []
        for k in range(256):
            v = (k - PEN) if k >= PEN else 0
            pal += [(v * 97) & 255, (v * 53) & 255, (v * 151) & 255]
        im = Image.frombytes('P', (STRIDE, ROWS * 16 + 8), bytes(c.page()[PAGE_OFF:PAGE_OFF + STRIDE * (ROWS * 16 + 8)]))
        im.putpalette(pal)
        im.crop((0, 0, COLS * 8 + 16, ROWS * 16 + 8)).convert('RGB').save(os.path.join(HERE, 'build', 'test_snoop.png'))
    except ImportError:
        pass
    slot_test(rnd)
    print('OK: every pass pixel-exact, stop answered, slots exact')


if __name__ == '__main__':
    os.makedirs(os.path.join(HERE, 'build'), exist_ok=True)
    main()
