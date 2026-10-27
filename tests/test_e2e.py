#!/usr/bin/env python3
"""ATWSNOOP.TOS end to end without hardware: the 68000 program under unicorn, the card's T425 in temu.py.

A Mega STE as the program sees it: the ATW800/2 at $A00000; the shifter's registers, palette and video counter
and the MFP as plain memory (the counter moves with each read); the frame counter $466 and the 200 Hz clock move
with each read. GEMDOS/XBIOS/BIOS calls are served by hooks on the trap instructions. The T425 runs a slice at each
frame. Three cases, the T425 ones also with the figures as slots ("s"; the page must be the same):
  desktop  the card is the xVDI desktop, 1280x1024x8 (ATWInfo, page at 0): the T425 draws, the grid 100x40
  own      no xVDI: the program sets 640x480 itself and switches the output off at the end
  dead     no xVDI, the link never answers: the 68000 draws
Checks: the log's lines, the drawn page against the grid (T425 cases: every cell; 68000 case: the first rows,
it draws 12 cells a frame), the LUT and shared memory given back, the output off at the end (own, dead).
Pictures: build/e2e_<case>.png. Speeds mean nothing here. Needs unicorn and PIL (tools/venv)."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 't425'))
sys.path.insert(0, HERE)
import temu  # noqa: E402
import build_snoop as bd  # noqa: E402
from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE, UC_HOOK_MEM_READ  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_D0, UC_M68K_REG_SR, UC_CPU_M68K_M68000  # noqa: E402

PRG = os.path.join(HERE, '..', 'build', 'ATWSNOOP.TOS')
CARD = 0xA00000
VRAM_N = 0x1F4000
REGS = CARD + 0x1F4000
LOAD = 0x10000
SH = temu.SHARED - temu.MINT
VR = temu.VRAM - temu.MINT
GRID = 0x400


class Rig:
    def __init__(self, case):
        self.case = case
        self.uc = uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        uc.mem_map(0, 0x400000)
        uc.mem_map(CARD, VRAM_N)
        uc.mmio_map(REGS, 0xC000, self.rd, None, self.wr, None)
        uc.mem_map(0xFF8000, 0x8000)
        self.t = temu.T425()
        self.boot_buf = None
        self.running = False
        self.halted = False
        self.log = bytearray()
        self.out = bytearray()
        self.vtg = {}
        self.reads = 0
        self.vpos = 0
        self.fc = 0
        self.snap = None
        self.lut = [(i * 0x0841) & 0xFFFF for i in range(256)]
        self.lut0 = list(self.lut)
        self.info = bytearray(0x80)
        if case == 'desktop':
            self.W, self.H = 1280, 1024
            struct.pack_into('>4sIIIIIHHHH', self.info, 0, b'ATW8', 0x58564449, 0x30313030, 0, 0, 1280 * 1024, 1280, 8,
                             1280, 1024)
            struct.pack_into('>H', self.info, 32, 0x001F)
            struct.pack_into('>H', self.info, 64, 0x4711)
        else:
            self.W, self.H = 640, 480
        self.fpga = b'V0208 build 20251103 (rig)'.ljust(24)[:24] + b' cpm' + bytes(4)
        self.t.mem[SH:SH + 0x8000] = bytes((x * 11 + 1) & 0xFF for x in range(0x8000))
        self.shm = bytes(self.t.mem[SH:SH + 0x8000])

    # ---- the T425 ----
    def t_run(self, n):
        if not self.running or self.halted:
            return
        uc, t = self.uc, self.t
        t.mem[VR:VR + VRAM_N] = uc.mem_read(CARD, VRAM_N)
        try:
            t.run(n)
        except temu.Halt as e:
            if 'limite' not in str(e):
                self.halted = True
                self.running = False
                if 'stopp' not in str(e) and self.t.mem[SH] == 0x5A:
                    print('T425 halted:', e)
        uc.mem_write(CARD, bytes(t.mem[VR:VR + VRAM_N]))

    # ---- the card ----
    def rd(self, uc, off, size, _):
        o = REGS + off - CARD
        if o < 0x1FDC00:
            if o - 0x1F4000 < 4 and self.running:       # the 68000 looks at the state bytes: the T425 goes on
                self.t_run(200_000)
            return int.from_bytes(self.t.mem[SH + o - 0x1F4000:SH + o - 0x1F4000 + size], 'big')
        if o < 0x1FDC80:
            return int.from_bytes(self.info[o - 0x1FDC00:o - 0x1FDC00 + size], 'big')
        if 0x1FF000 <= o < 0x1FF200:
            i = (o - 0x1FF000) // 2
            return self.lut[i] if size == 2 else self.lut[i] << 16 | self.lut[i + 1]
        if 0x1FF200 <= o < 0x1FF220:
            return int.from_bytes(self.fpga[o - 0x1FF200:o - 0x1FF200 + size], 'big')
        if 0x1FFAC0 <= o < 0x1FFAE0:
            v = 0
            for k in range(size):
                v = v << 8 | self.link_rd(o - 0x1FFAC0 + k)
            return v
        return 0

    def link_rd(self, r):
        if self.case == 'dead':
            return 0
        t = self.t
        if r == 5:
            if not t.link_out and t.I and not self.halted and self.running:
                self.t_run(50_000)
            return 1 if t.link_out else 0
        if r == 7:
            return 1
        if r == 1:
            return t.link_out.pop(0) if t.link_out else 0
        return 0

    def wr(self, uc, off, size, value, _):
        o = REGS + off - CARD
        if o < 0x1FDC00:
            r = o - 0x1F4000
            if r == 0x101 and value == 6 and self.running:     # the stop: what it was given, drawn first
                self.t_run(3_000_000)
                self.take_snap()
            self.t.mem[SH + r:SH + r + size] = value.to_bytes(size, 'big')
            if r == 0x100 and self.running:
                self.t_run(3_000_000)
            return
        if 0x1FF000 <= o < 0x1FF200:
            if not hasattr(self, 'lut_used') and self.lut != self.lut0 and (o - 0x1FF000) // 2 == 0:
                self.lut_used = list(self.lut)              # (the colours as the program set them, before it ends)
            self.lut[(o - 0x1FF000) // 2] = value & 0xFFFF
            return
        if 0x1FF800 <= o < 0x1FF820:
            self.vtg[(o - 0x1FF800) // 2] = value
            return
        if 0x1FFAC0 <= o < 0x1FFAE0 and self.case != 'dead':
            for k in range(size):
                self.link_wr(o - 0x1FFAC0 + k, (value >> (8 * (size - 1 - k))) & 0xFF)

    def link_wr(self, r, v):
        if r == 17:
            if v:
                old = self.t
                self.t = temu.T425()
                self.t.mem = old.mem
                self.halted = False
                self.running = False
                self.boot_buf = None
            else:
                self.boot_buf = bytearray()
        elif r == 3:
            if self.boot_buf is not None:
                self.boot_buf.append(v)
                if len(self.boot_buf) == self.boot_buf[0] + 1:
                    self.t.link_in.extend(self.boot_buf)
                    self.boot_buf = None
                    self.t.boot()
                    self.running = True
            else:
                self.t.link_in.append(v)

    def take_snap(self):
        self.snap = (bytes(self.t.mem[SH + GRID:SH + GRID + 2 * 100 * 40]), bytes(self.uc.mem_read(CARD, self.W * self.H)),
                     list(self.lut))

    # ---- TOS ----
    def load(self, cmd):
        uc = self.uc
        prg = open(PRG, 'rb').read()
        tl, dl, bl = struct.unpack('>III', prg[2:14])
        sl = struct.unpack('>I', prg[14:18])[0]
        body = bytearray(prg[28:28 + tl + dl])
        text = LOAD + 0x100
        rel = prg[28 + tl + dl + sl:]
        first = struct.unpack('>I', rel[:4])[0]
        if first:
            p = first
            struct.pack_into('>I', body, p, struct.unpack('>I', body[p:p + 4])[0] + text)
            for b in rel[4:]:
                if b == 0:
                    break
                p += 254 if b == 1 else b
                if b != 1:
                    struct.pack_into('>I', body, p, struct.unpack('>I', body[p:p + 4])[0] + text)
        uc.mem_write(text, bytes(body))
        uc.mem_write(LOAD, struct.pack('>IIIIIIII', LOAD, LOAD + 0x200000, text, tl, text + tl, dl, text + tl + dl, bl))
        uc.mem_write(LOAD + 0x24, struct.pack('>I', 0x8000))
        uc.mem_write(LOAD + 0x80, bytes([len(cmd)]) + cmd.encode() + b'\0')
        uc.mem_write(0x5A0, struct.pack('>I', 0x2000))
        uc.mem_write(0x2000, b'_MCH' + struct.pack('>I', 0x00010010) + b'_CPU' + struct.pack('>I', 0) + bytes(8))
        uc.mem_write(0x44E, struct.pack('>I', 0xFEA00000 if self.case == 'desktop' else 0x300000))
        self.traps = {}
        code = bytes(uc.mem_read(text, tl))
        for i in range(0, tl - 1, 2):
            w = code[i:i + 2]
            if w in (b'\x4e\x41', b'\x4e\x4e', b'\x4e\x4d'):
                self.traps[text + i] = w[1] & 15
                uc.mem_write(text + i, b'\x4e\x71')
        for a in self.traps:
            uc.hook_add(UC_HOOK_CODE, lambda *x: self.trap(*x), begin=a, end=a)
        uc.hook_add(UC_HOOK_MEM_READ, self.clock, begin=0x466, end=0x469)
        uc.hook_add(UC_HOOK_MEM_READ, self.clock, begin=0x4BA, end=0x4BD)
        uc.hook_add(UC_HOOK_MEM_READ, self.video, begin=0xFF8205, end=0xFF8209)
        uc.reg_write(UC_M68K_REG_SR, 0x2700)
        uc.mem_write(0x7FF00, struct.pack('>II', 0, LOAD))
        uc.reg_write(UC_M68K_REG_A7, 0x7FF00)
        return text

    def clock(self, uc, access, addr, size, value, _):
        self.reads += 1
        if addr == 0x4BA:
            uc.mem_write(0x4BA, struct.pack('>I', 1000 + self.reads // 16 + self.t.icount // 50000))
            # (a step every 16 reads: snoop_clock reads it twice and repeats until both agree; every other read never would)
        elif addr == 0x466:
            if self.reads % 40 == 0:
                self.fc += 1
                self.grid_frame = bytes(self.t.mem[SH + GRID:SH + GRID + 2 * 80 * 30])
                self.t_run(150_000)                 # the T425 goes on while the 68000 waits for the frame
            uc.mem_write(0x466, struct.pack('>I', self.fc))

    def video(self, uc, access, addr, size, value, _):
        if addr == 0xFF8205:
            self.vpos = (self.vpos + 480) % 32000
            hi, mid = uc.mem_read(0xFF8201, 1)[0], uc.mem_read(0xFF8203, 1)[0]
            a = (hi << 16 | mid << 8) + self.vpos
            uc.mem_write(0xFF8204, bytes([0, a >> 16 & 0xFF, 0, a >> 8 & 0xFF, 0, a & 0xFF]))

    def trap(self, uc, addr, size, _):
        n = self.traps[addr]
        sp = uc.reg_read(UC_M68K_REG_A7)
        fn = struct.unpack('>H', uc.mem_read(sp, 2))[0]
        arg = lambda o, s=4: int.from_bytes(uc.mem_read(sp + 2 + o, s), 'big')  # noqa: E731
        d0 = 0
        if n == 1:
            if fn == 0x00:
                uc.emu_stop()
                self.done = True
            elif fn == 0x09:
                s = arg(0)
                b = bytearray()
                while uc.mem_read(s + len(b), 1)[0]:
                    b.append(uc.mem_read(s + len(b), 1)[0])
                self.out += b
            elif fn == 0x4A:
                d0 = 0
            elif fn == 0x3C:
                d0 = 6
            elif fn == 0x40:
                self.log += uc.mem_read(arg(6), arg(2))
            elif fn == 0x3E:
                d0 = 0
            else:
                raise RuntimeError('GEMDOS %#x' % fn)
        elif n == 14:
            if fn == 38:
                f = arg(0)
                sp -= 4
                uc.mem_write(sp, struct.pack('>I', addr + 2))
                uc.reg_write(UC_M68K_REG_A7, sp)
                uc.reg_write(UC_M68K_REG_PC, f)
                return
            raise RuntimeError('XBIOS %d' % fn)
        elif n == 13:
            if fn in (1, 2, 11):
                d0 = 0
            else:
                raise RuntimeError('BIOS %d' % fn)
        uc.reg_write(UC_M68K_REG_D0, d0 & 0xFFFFFFFF)


def render(font, grid, cols, rows, W, page, pen0, only_rows=None):
    """Cells whose drawing differs from the reference: [(col, row)]."""
    bad = []
    for r in range(rows if only_rows is None else only_rows):
        for c in range(cols):
            v = grid[2 * (r * cols + c)] << 8 | grid[2 * (r * cols + c) + 1]
            ch, attr = v >> 8, v & 0xFF
            fg, bg = pen0 + (attr & 15), pen0 + (attr >> 4)
            for y in range(16):
                bits = font[ch * 16 + y]
                o = (r * 16 + y) * W + c * 8
                if page[o:o + 8] != bytes(fg if bits & (0x80 >> k) else bg for k in range(8)):
                    bad.append((c, r))
                    break
    return bad


def save_png(name, page, W, H, lut):
    from PIL import Image
    pal = []
    for c in lut:
        pal += [((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31]
    im = Image.frombytes('P', (W, H), page)
    im.putpalette(pal)
    im.convert('RGB').save(os.path.join(HERE, 'build', name))


def run(case):
    base, slots = case.split('-')[0], case.endswith('-s')
    r = Rig(base)
    text = r.load(('2' if base != 'dead' else '3') + (' s' if slots else ''))
    r.done = False
    r.uc.emu_start(text, 0)
    log = r.log.decode('latin-1').replace('\r', '')
    print('--- %s ---\n%s' % (case, log.strip()))
    assert r.done, 'did not end'
    font = bd.font()
    if slots:
        assert 'slots: 25 ' in log, 'the slots were not made'
    case = base
    if case == 'desktop':
        cols, rows, pen0 = 100, 40, 240
        assert 'mode 1' in log and 'xVDI desktop' in log, 'not the T425 on the desktop'
    elif case == 'own':
        cols, rows, pen0 = 80, 30, 0
        assert 'mode 1' in log and '640x480' in log
    else:
        cols, rows, pen0 = 80, 30, 0
        assert 'mode 2' in log and 'does not answer' in log
    assert 'frames ' in log
    if case != 'dead':
        assert r.snap, 'no stop command seen'
        grid, page, lut = r.snap
        bad = render(font, grid, cols, rows, r.W, page, pen0)
        save_png('e2e_%s%s.png' % (case, '-s' if slots else ''), page, r.W, r.H, lut)
        r.page = page
        assert not bad, '%d cells differ, first %s' % (len(bad), bad[:5])
        assert bytes(r.t.mem[SH:SH + 0x8000])[:0x3C00] == r.shm[:0x3C00], 'shared memory not back'
    else:
        page = bytes(r.uc.mem_read(CARD, r.W * r.H))     # the 68000 draws 12 cells a frame: the first rows are done
        bad = render(font, r.grid_frame, cols, rows, r.W, page, pen0, only_rows=2)
        save_png('e2e_%s.png' % case, page, r.W, r.H, r.lut_used)
        assert not bad, '%d cells differ in the first rows, first %s' % (len(bad), bad[:5])
    assert r.lut == r.lut0, 'LUT not back'
    if case != 'desktop':
        assert r.vtg.get(0) == 0, 'output not off at the end'
    print('OK: %s' % case)
    return r


def main():
    os.makedirs(os.path.join(HERE, 'build'), exist_ok=True)
    cases = sys.argv[1:] or ['desktop', 'own', 'dead', 'own-s', 'desktop-s', 'dead-s']
    pages = {}
    for c in cases:
        pages[c] = getattr(run(c), 'page', None)
    for c in cases:                                 # the slots must show exactly what the calls show
        if c.endswith('-s') and c[:-2] in pages:
            assert pages[c] == pages[c[:-2]], '%s differs from %s' % (c, c[:-2])
            print('OK: %s draws the same page as %s' % (c, c[:-2]))


if __name__ == '__main__':
    main()
