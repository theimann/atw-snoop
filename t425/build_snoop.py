#!/usr/bin/env python3
"""The T425 program of atw-snoop (atwsnoop.c): the ATW800/2 as a text screen. Writes snoopprog.h.

The 68000 writes cells (a word: character << 8 | attribute) into a text grid in the card's shared memory and
nothing else: no commands, no waiting. The T425 compares the grid with its own copy, over and over, and draws
each cell that changed (8x16 font, 256 colours: pen = pen base + the attribute's low nibble for the ink, high
nibble for the paper). So the program that is being debugged pays only for its word writes.

Shared memory (T425 $807F4000, the 68000 at the card's base + $1F4000; the T425 writes bytes only there):
  +0 $5A once running, +1 the seq of the last command done, +3 a pass counter (the 68000's watchdog)
  +$100 byte seq (a new value = a new command), +$101 byte cmd: 6 stop
  +P4 the grid, P2 x P3 cells, row by row, two bytes a cell (character, attribute)
Parameters (16 longs over the link after the payload), then the font (256 glyphs x 16 bytes, top row first,
leftmost pixel in the top bit):
  P0 the grid's top left in VRAM (offset), P1 the stride, P2 columns (even), P3 rows, P4 the grid's offset in
  shared memory, P5 the pen base, P6 reads before each pass (0: 4096), P7 bytes to clear from P0 at the start
  (0: none), P8 their colour, P9 timer ticks from one pass's start to the next (0: 312, about 20 ms; 1: no wait),
  P10 the slots' offset in shared memory (0: none).
Slots (atwsnoop.c snoop_slot/snoop_set): the 68000 hands over raw values, the T425 formats them into the grid's cells,
so a value costs the 68000 one long write instead of its formatting (Mega STE 8 MHz: ATWSNOOP's ~140
formatting calls took 5 ms a frame; a grid word costs only 0.6 us more than ST RAM). At P10, big-endian as the
68000 writes them: +3 the number of slots (byte); from +16, SLOTS_MAX slots of 16 bytes: +0 the value (long),
+4 kind (1 hex, 2 decimal, 3 bar, 4 bytes as hex), +5 attribute, +6 n (digits, field width, bar width in cells,
bytes), +7 a generation (changed by the 68000 when the slot is defined or its cells were overwritten), +8 the
first cell's byte offset in the grid (word), +12 the bar's maximum (long); from +16 + 16 x SLOTS_MAX, 32 bytes of
data per slot (kind 4). Each pass, a slot whose value, generation or data changed is formatted into its cells;
the grid scan then draws them like any other cell. Fixed fields: a decimal too long for its width loses its top
digits.
The passes are paced: without a pause the T425 read the card's memory without end, and the 68000's word writes
over VME waited for it (Mega STE: ATWSNOOP's calls 9.4 ms a frame against 3.2 ms with the 68000 drawing).
The wait spins on ldtimer in the T425's cache (tin never wakes on the card, build_rain.py).
The card's T425 caches its own RAM (measured on the card): it reads 64 KB elsewhere before each pass, or it
would go on seeing the grid as it was. Tools: tasm.py (assembler), t425base.py (memory map, boot loader), temu.py (emulator for the tests).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
T425 = HERE
sys.path.insert(0, T425)
from tasm import Asm  # noqa: E402
from t425base import build_loader, mask, SHARED_WORDS, LOAD_WORDS, VRAM_WORDS, FONT_WORDS, EVICT_WORDS  # noqa: E402

OUT = os.path.join(HERE, '..', 'src', 'snoopprog.h')
FONT_BDF = os.path.join(HERE, '..', 'fonts', 'ter-u16n.bdf')
COPY_WORDS = 0x80000         # $80200000: the T425's copy of the grid
CACHE_WORDS = 0x90000        # $80240000: what each slot showed last (12 words a slot)
SLOTS_MAX = 128
assert LOAD_WORDS < FONT_WORDS < COPY_WORDS < CACHE_WORDS < EVICT_WORDS

NAMES = ['P%d' % i for i in range(16)] + [
    'sh', 'vram', 'font', 'copy', 'grid', 'lastseq', 'seq', 'cmd', 'w', 'ep', 'en', 'es', 'ea', 'pass',
    'r', 'c', 'gp', 'cp', 'rowdst', 'rb', 'v', 'old', 'cell', 'half', 'attr', 'lastc', 'bw', 'fx', 'fp', 'dst',
    'tmp', 'n'] + ['ct%d' % i for i in range(16)] + ['next', 'period'] + [
    'slp', 'sdp', 'scp', 'sn', 'kind', 'gen', 'val', 'chg', 'sd', 'sat', 'sk', 'k', 'e', 'neg', 'ch2', 'smax',
    'cache']
G = {n: i for i, n in enumerate(NAMES)}


class Prog(Asm):
    def L(self, n): self.d('ldl', G[n])
    def S(self, n): self.d('stl', G[n])
    def C(self, v): self.d('ldc', v)
    def o(self, *ops):
        for x in ops:
            self.op(x)

    def default(self, p, name, value):          # name := p, or value when p is 0
        self.L(p); self.S(name)
        self.L(p); self.jr('cj', name + '_0')
        self.jr('j', name + '_1')
        self.label(name + '_0')
        if isinstance(value, int):
            self.C(value)
        else:
            value()
        self.S(name)
        self.label(name + '_1')


def be32(p, base, off, name):                   # local := the 68000's big-endian long at local base + off
    for k in range(4):
        p.L(base)
        if off + k:
            p.d('adc', off + k)
        p.o('lb')
        if k < 3:
            p.C(24 - 8 * k); p.o('shl')
        if k:
            p.o('or')
    p.S(name)


def hexch(p):                                   # ch2 := the hex digit e (0..15)
    p.L('e'); p.C(9); p.o('gt'); p.C(7); p.o('prod'); p.L('e'); p.o('sum'); p.d('adc', 48); p.S('ch2')


def put(p, cells):                              # ch2 and sat to the cell sd + `cells` (pushes the cell index)
    for a, name in ((0, 'ch2'), (1, 'sat')):
        p.L(name)
        cells(); p.C(1); p.o('shl'); p.L('sd'); p.o('sum')
        if a:
            p.d('adc', 1)
        p.o('sb')


def slots(p):
    """Every slot whose value, generation or data changed: formatted into its cells in the grid."""
    p.label('slots')
    p.L('P10'); p.jr('cj', 'scan')
    p.L('sh'); p.L('P10'); p.o('sum'); p.S('slp')
    p.L('slp'); p.d('adc', 3); p.o('lb'); p.S('sn')
    p.L('slp'); p.d('adc', 16); p.S('slp')
    p.L('slp'); p.C(16 * SLOTS_MAX); p.o('sum'); p.S('sdp')
    p.L('cache'); p.S('scp')
    p.label('sl')
    p.L('sn'); p.jr('cj', 'scan')
    p.L('slp'); p.d('adc', 4); p.o('lb'); p.S('kind')
    p.L('kind'); p.jr('cj', 'snext')
    p.L('slp'); p.d('ldnl', 0); p.S('val')
    p.L('slp'); p.d('adc', 7); p.o('lb'); p.S('gen')
    p.C(0); p.S('chg')
    for i, (a, b) in enumerate([('val', 0), ('gen', 1)] + [(None, 2 + k) for k in range(8)]):
        if a:
            p.L(a)
        else:
            p.L('sdp'); p.d('ldnl', b - 2)
        p.L('scp'); p.d('ldnl', b); p.o('xor'); p.jr('cj', 'same%d' % i)
        p.C(1); p.S('chg')
        p.label('same%d' % i)
    p.L('chg'); p.jr('cj', 'snext')
    p.L('val'); p.L('scp'); p.d('stnl', 0)
    p.L('gen'); p.L('scp'); p.d('stnl', 1)
    for k in range(8):
        p.L('sdp'); p.d('ldnl', k); p.L('scp'); p.d('stnl', 2 + k)
    p.L('slp'); p.d('adc', 8); p.o('lb'); p.C(8); p.o('shl')
    p.L('slp'); p.d('adc', 9); p.o('lb'); p.o('or'); p.L('grid'); p.o('sum'); p.S('sd')
    p.L('slp'); p.d('adc', 5); p.o('lb'); p.S('sat')
    p.L('slp'); p.d('adc', 6); p.o('lb'); p.S('sk')
    be32(p, 'slp', 0, 'val')
    for kind, lab in ((1, 'khex'), (2, 'kdec'), (3, 'kbar')):
        p.L('kind'); p.d('eqc', kind); p.jr('cj', 'not%d' % kind); p.jr('j', lab)
        p.label('not%d' % kind)
    p.jr('j', 'kmem')

    def at_k():
        p.L('k')

    # hex: n digits, the lowest at the right
    p.label('khex')
    p.L('sk'); p.S('k')
    p.label('hx')
    p.L('k'); p.jr('cj', 'snext')
    p.L('k'); p.d('adc', -1); p.S('k')
    p.L('val'); p.C(15); p.o('and'); p.S('e'); hexch(p)
    put(p, at_k)
    p.L('val'); p.C(4); p.o('shr'); p.S('val')
    p.jr('j', 'hx')

    # decimal, signed, right-aligned in a field of n
    p.label('kdec')
    be32(p, 'slp', 12, 'smax')                                  # a scale in thousandths (max > 0): val x max / 1000
    p.L('smax'); p.C(0); p.o('gt'); p.jr('cj', 'dnosc')
    p.L('val'); p.L('smax'); p.o('prod'); p.C(1000); p.o('div'); p.S('val')
    p.label('dnosc')
    p.C(0); p.L('val'); p.o('gt'); p.S('neg')                   # 0 > val
    p.L('neg'); p.jr('cj', 'dpos')
    p.C(0); p.L('val'); p.o('sub'); p.S('val')
    p.label('dpos')
    p.L('sk'); p.S('k')
    p.label('dd')
    p.L('k'); p.jr('cj', 'dsign')
    p.L('k'); p.d('adc', -1); p.S('k')
    p.L('val'); p.C(10); p.o('rem'); p.d('adc', 48); p.S('ch2')
    put(p, at_k)
    p.L('val'); p.C(10); p.o('div'); p.S('val')
    p.L('val'); p.jr('cj', 'dsign')
    p.jr('j', 'dd')
    p.label('dsign')
    p.L('neg'); p.jr('cj', 'dfill')
    p.L('k'); p.jr('cj', 'dfill')
    p.L('k'); p.d('adc', -1); p.S('k')
    p.C(ord('-')); p.S('ch2')
    put(p, at_k)
    p.label('dfill')
    p.L('k'); p.jr('cj', 'snext')
    p.L('k'); p.d('adc', -1); p.S('k')
    p.C(32); p.S('ch2')
    put(p, at_k)
    p.jr('j', 'dfill')

    # bar: n cells in eighths of value / max (font: 127 full, 9..15 partial from the left), as snoop_bar
    p.label('kbar')
    be32(p, 'slp', 12, 'smax')
    p.C(0); p.L('val'); p.o('gt'); p.jr('cj', 'b1')               # value < 0: 0
    p.C(0); p.S('val')
    p.label('b1')
    p.L('smax'); p.C(0); p.o('gt'); p.jr('cj', 'bm1')             # max <= 0: 1
    p.jr('j', 'b2')
    p.label('bm1')
    p.C(1); p.S('smax')
    p.label('b2')
    p.L('val'); p.L('smax'); p.o('gt'); p.jr('cj', 'b3')          # value > max: max
    p.L('smax'); p.S('val')
    p.label('b3')
    p.L('smax'); p.C(0xFFFFF); p.o('gt'); p.jr('cj', 'b4')        # max under 2^20: value x 8n stays in 31 bits
    p.L('smax'); p.C(1); p.o('shr'); p.S('smax')
    p.L('val'); p.C(1); p.o('shr'); p.S('val')
    p.jr('j', 'b3')
    p.label('b4')
    p.L('val'); p.L('sk'); p.C(3); p.o('shl'); p.o('prod'); p.L('smax'); p.o('div'); p.S('e')
    p.C(0); p.S('k')
    p.label('bb')
    p.L('k'); p.L('sk'); p.o('xor'); p.jr('cj', 'snext')
    p.L('e'); p.C(7); p.o('gt'); p.jr('cj', 'bpart')
    p.C(127); p.S('ch2'); p.jr('j', 'bput')
    p.label('bpart')
    p.L('e'); p.C(0); p.o('gt'); p.jr('cj', 'bsp')
    p.L('e'); p.d('adc', 8); p.S('ch2'); p.jr('j', 'bput')
    p.label('bsp')
    p.C(32); p.S('ch2')
    p.label('bput')
    put(p, at_k)
    p.L('e'); p.d('adc', -8); p.S('e')
    p.L('k'); p.d('adc', 1); p.S('k')
    p.jr('j', 'bb')

    # bytes: n bytes from the slot's data, "XX " each (the spaces are the 68000's)
    p.label('kmem')
    p.C(0); p.S('k')
    p.label('mm')
    p.L('k'); p.L('sk'); p.o('xor'); p.jr('cj', 'snext')
    p.L('k'); p.L('sdp'); p.o('bsub'); p.o('lb'); p.S('val')

    def at_3k(lo):
        def f():
            p.L('k'); p.C(3); p.o('prod')
            if lo:
                p.d('adc', 1)
        return f
    p.L('val'); p.C(4); p.o('shr'); p.S('e'); hexch(p)
    put(p, at_3k(0))
    p.L('val'); p.C(15); p.o('and'); p.S('e'); hexch(p)
    put(p, at_3k(1))
    p.L('k'); p.d('adc', 1); p.S('k')
    p.jr('j', 'mm')

    p.label('snext')
    p.L('slp'); p.d('adc', 16); p.S('slp')
    p.L('sdp'); p.d('adc', 32); p.S('sdp')
    p.L('scp'); p.d('adc', 48); p.S('scp')
    p.L('sn'); p.d('adc', -1); p.S('sn')
    p.jr('j', 'sl')


def build_main():
    p = Prog()
    p.o('mint'); p.d('ldnlp', SHARED_WORDS); p.S('sh')
    p.d('ldlp', G['P0']); p.o('mint'); p.d('ldnlp', 4); p.C(64); p.o('in')          # the parameters
    p.o('mint'); p.d('ldnlp', FONT_WORDS); p.S('font')
    p.L('font'); p.o('mint'); p.d('ldnlp', 4); p.C(4096); p.o('in')                # the font
    p.o('mint'); p.d('ldnlp', VRAM_WORDS); p.S('vram')
    p.o('mint'); p.d('ldnlp', COPY_WORDS); p.S('copy')
    p.o('mint'); p.d('ldnlp', CACHE_WORDS); p.S('cache')
    p.C(SLOTS_MAX * 12); p.S('n'); p.L('cache'); p.S('scp')        # no slot has shown anything (generation 0)
    p.label('zc')
    p.L('n'); p.jr('cj', 'zcd')
    p.C(0); p.L('scp'); p.d('stnl', 0)
    p.L('scp'); p.d('adc', 4); p.S('scp')
    p.L('n'); p.d('adc', -1); p.S('n')
    p.jr('j', 'zc')
    p.label('zcd')
    p.L('sh'); p.L('P4'); p.o('sum'); p.S('grid')
    p.L('P6'); p.d('adc', -1); p.S('en')
    p.default('P6', 'en', 4096)
    p.default('P9', 'period', 312)                                                  # 15600 ticks a second
    p.C(0); p.o('sttimer')                                                         # the timers stand still after a reset
    p.o('ldtimer'); p.S('next')
    p.C(16); p.S('es')
    p.o('mint'); p.d('ldnlp', EVICT_WORDS); p.S('ea')
    p.L('P1'); p.C(4); p.o('shl'); p.S('rb')                                        # a cell row: 16 lines
    p.C(0); p.S('lastseq'); p.C(0); p.S('pass')
    p.C(-1); p.S('lastc')
    # the copy: all $FFFF (no attribute is $FF with character $FF in practice), so every cell is drawn once
    p.L('P2'); p.L('P3'); p.o('prod'); p.C(1); p.o('shr'); p.S('n')
    p.L('copy'); p.S('cp')
    p.label('cinit')
    p.L('n'); p.jr('cj', 'clear')
    p.C(-1); p.L('cp'); p.d('stnl', 0)
    p.L('cp'); p.d('adc', 4); p.S('cp')
    p.L('n'); p.d('adc', -1); p.S('n')
    p.jr('j', 'cinit')
    # the area to clear: longs of colour P8
    p.label('clear')
    p.L('P8'); p.C(0x01010101); p.o('prod'); p.S('v')
    p.L('P7'); p.C(2); p.o('shr'); p.S('n')
    p.L('vram'); p.L('P0'); p.o('sum'); p.S('dst')
    p.label('cl')
    p.L('n'); p.jr('cj', 'running')
    p.L('v'); p.L('dst'); p.d('stnl', 0)
    p.L('dst'); p.d('adc', 4); p.S('dst')
    p.L('n'); p.d('adc', -1); p.S('n')
    p.jr('j', 'cl')
    p.label('running')
    p.C(0x5A); p.L('sh'); p.o('sb')

    p.label('loop')
    p.label('wait')
    p.o('ldtimer'); p.L('next'); p.o('sub'); p.C(-1); p.o('gt'); p.jr('cj', 'wait')
    p.o('ldtimer'); p.L('period'); p.o('sum'); p.S('next')
    p.L('en'); p.S('w'); p.L('ea'); p.S('ep')
    p.label('evict')
    p.L('w'); p.jr('cj', 'poll')
    p.L('ep'); p.d('ldnl', 0); p.L('ep'); p.L('es'); p.o('sum'); p.S('ep')
    p.L('w'); p.d('adc', -1); p.S('w')
    p.jr('j', 'evict')
    p.label('poll')
    p.L('pass'); p.d('adc', 1); p.S('pass')
    p.L('pass'); p.L('sh'); p.d('adc', 3); p.o('sb')
    p.L('sh'); p.d('adc', 0x100); p.o('lb'); p.S('seq')
    p.L('seq'); p.L('lastseq'); p.o('xor'); p.jr('cj', 'slots')
    p.L('seq'); p.S('lastseq')
    p.L('sh'); p.d('adc', 0x101); p.o('lb'); p.S('cmd')
    p.L('seq'); p.L('sh'); p.d('adc', 1); p.o('sb')
    p.L('cmd'); p.d('eqc', 6); p.jr('cj', 'slots')
    p.o('stopp')

    # the grid, two cells (a long) at a time against the copy
    slots(p)
    p.label('scan')
    p.L('grid'); p.S('gp')
    p.L('copy'); p.S('cp')
    p.L('vram'); p.L('P0'); p.o('sum'); p.S('rowdst')
    p.L('P3'); p.S('r')
    p.label('row')
    p.L('r'); p.jr('cj', 'loop')
    p.C(0); p.S('c')
    p.label('pair')
    p.L('c'); p.L('P2'); p.o('xor'); p.jr('cj', 'rowend')
    p.L('gp'); p.d('ldnl', 0); p.S('v')
    p.L('cp'); p.d('ldnl', 0); p.S('old')
    p.L('v'); p.L('old'); p.o('xor'); p.jr('cj', 'pairnext')
    p.L('v'); p.L('cp'); p.d('stnl', 0)
    p.C(0); p.S('half')
    p.label('half')
    p.L('v'); p.L('half'); p.o('shr'); p.C(0xFFFF); p.o('and'); p.S('cell')
    p.L('old'); p.L('half'); p.o('shr'); p.C(0xFFFF); p.o('and'); p.L('cell'); p.o('xor'); p.jr('cj', 'halfnext')
    # draw: the character is the low byte (the 68000 wrote it first), the attribute the high one
    p.L('cell'); p.C(8); p.o('shr'); p.S('attr')
    p.L('attr'); p.L('lastc'); p.o('xor'); p.jr('cj', 'same')
    p.L('attr'); p.S('lastc')
    p.L('attr'); p.C(4); p.o('shr'); p.L('P5'); p.o('sum'); p.C(0x01010101); p.o('prod'); p.S('bw')
    p.L('attr'); p.C(15); p.o('and'); p.L('P5'); p.o('sum'); p.C(0x01010101); p.o('prod'); p.L('bw'); p.o('xor'); p.S('fx')
    for n in range(16):
        p.C(mask(n)); p.L('fx'); p.o('and'); p.L('bw'); p.o('xor'); p.S('ct%d' % n)
    p.label('same')
    p.L('cell'); p.C(0xFF); p.o('and'); p.C(4); p.o('shl'); p.L('font'); p.o('sum'); p.S('fp')
    # x = (c + half/16) * 8 pixels
    p.L('c'); p.L('half'); p.C(4); p.o('shr'); p.o('sum'); p.C(3); p.o('shl'); p.L('rowdst'); p.o('sum'); p.S('dst')
    for r in range(16):
        for k, low in ((0, False), (1, True)):
            p.L('fp')
            if r:
                p.d('adc', r)
            p.o('lb')
            if low:
                p.C(15); p.o('and')
            else:
                p.C(4); p.o('shr')
            p.d('ldlp', G['ct0']); p.o('wsub'); p.d('ldnl', 0); p.L('dst'); p.d('stnl', k)
        p.L('dst'); p.L('P1'); p.o('sum'); p.S('dst')
    p.label('halfnext')
    p.L('half'); p.d('eqc', 16); p.jr('cj', 'half16')
    p.jr('j', 'pairnext')
    p.label('half16')
    p.C(16); p.S('half')
    p.jr('j', 'half')
    p.label('pairnext')
    p.L('gp'); p.d('adc', 4); p.S('gp')
    p.L('cp'); p.d('adc', 4); p.S('cp')
    p.L('c'); p.d('adc', 2); p.S('c')
    p.jr('j', 'pair')
    p.label('rowend')
    p.L('rowdst'); p.L('rb'); p.o('sum'); p.S('rowdst')
    p.L('r'); p.d('adc', -1); p.S('r')
    p.jr('j', 'row')
    return p.assemble()


def bdf(path):
    """{code: 16 bytes} from an 8x16 BDF."""
    lines = open(path, encoding='latin-1').read().split('\n')
    asc = 12
    out = {}
    i = 0
    while i < len(lines):
        q = lines[i].split()
        if q and q[0] == 'FONT_ASCENT':
            asc = int(q[1])
        if q and q[0] == 'STARTCHAR':
            enc, bbx, rows = None, None, []
            while not lines[i].startswith('BITMAP'):
                q = lines[i].split()
                if q and q[0] == 'ENCODING':
                    enc = int(q[1])
                if q and q[0] == 'BBX':
                    bbx = [int(x) for x in q[1:]]
                i += 1
            i += 1
            while not lines[i].startswith('ENDCHAR'):
                rows.append(int(lines[i].strip()[:2], 16) >> bbx[2] if bbx[2] >= 0 else int(lines[i].strip()[:2], 16))
                i += 1
            g = [0] * 16
            top = asc - (bbx[1] + bbx[3])
            for k, v in enumerate(rows):
                if 0 <= top + k < 16:
                    g[top + k] = v
            out[enc] = bytes(g)
        i += 1
    return out


def font():
    """256 glyphs: ASCII 32-126 as Terminus 8x16; 1-8 bars from the bottom (2 lines each), 9-16 bars from the
    left (1 pixel each), 17 a triangle to the right (the log's current line), 127 a full block, the rest blank."""
    src = bdf(FONT_BDF)
    g = [bytes(16)] * 256
    for c in range(32, 127):
        g[c] = src.get(c, src[ord('?')])
    for k in range(1, 9):
        g[k] = bytes(0xFF if r >= 16 - 2 * k else 0 for r in range(16))
        g[8 + k] = bytes([(0xFF << (8 - k)) & 0xFF] * 16)
    g[17] = bytes([0, 0, 0, 0x40, 0x60, 0x70, 0x78, 0x7C, 0x78, 0x70, 0x60, 0x40, 0, 0, 0, 0])
    g[127] = bytes([0xFF] * 16)
    return b''.join(g)


def carray(name, data):
    lines = ['static const unsigned char %s[%d] = {' % (name, len(data))]
    for i in range(0, len(data), 16):
        lines.append('  ' + ', '.join('0x%02X' % b for b in data[i:i + 16]) + ',')
    lines.append('};')
    return '\n'.join(lines)


def main():
    loader, payload, f = build_loader(), build_main(), font()
    with open(OUT, 'w') as o:
        o.write('/* generated by build_snoop.py: the T425 side of atw-snoop (loader %d bytes, program %d bytes) and the '
                '8x16 font */\n' % (len(loader), len(payload)))
        o.write(carray('snoop_loader', loader) + '\n')
        o.write(carray('snoop_payload', payload) + '\n')
        o.write(carray('snoop_font', f) + '\n')
    return loader, payload, f


if __name__ == '__main__':
    lo, pa, _ = main()
    print('T425 loader %d bytes, program %d bytes' % (len(lo), len(pa)))
