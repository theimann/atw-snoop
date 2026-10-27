"""t425base.py: what every T425 program for the ATW800/2 shares: the memory map seen from the T425 (the 68000's
shared window, the program, the frame buffer, the font), the bit masks of the link protocol and the boot loader the
68000 sends over the link. From the ATW800/2 renderer of FujiTerm (same authors); the program atw-snoop runs is
built on it by build_snoop.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tasm import Asm  # noqa: E402


SHARED_WORDS = 0x1FD000      # $807F4000
LOAD_WORDS = 0x4000          # $80010000: the program
VRAM_WORDS = 0x180000        # $80600000
FONT_WORDS = 0x40000         # $80100000: the font
EVICT_WORDS = 0xC0000        # $80300000: 64 KB read before each poll

NAMES = ['P%d' % i for i in range(16)] + [
    'sh', 'vram', 'font', 'lastseq', 'seq', 'cmd', 'a0', 'a1', 'a2', 'w', 'ep', 'n', 'i', 'cp',
    'x', 'y', 'g', 'fg', 'bg', 'c', 'lastc', 'bw', 'fx', 'dst', 'src', 'fp', 'r', 'v', 'fw', 'gb', 'bpr', 'boldoff',
    'under', 'tmp', 'px', 'fh', 'en', 'es', 'ea'] + ['ct%d' % i for i in range(16)]
G = {n: i for i, n in enumerate(NAMES)}


def mask(n):
    """The 4 pixels of nibble n as byte masks, the leftmost pixel at the lowest address (the T425 is little-endian)."""
    return sum(0xFF << (8 * i) for i in range(4) if n & (8 >> i))


class Prog(Asm):
    def L(self, n):
        self.d('ldl', G[n])

    def S(self, n):
        self.d('stl', G[n])

    def C(self, v):
        self.d('ldc', v)

    def o(self, *ops):
        for x in ops:
            self.op(x)

    def sh(self, off):                      # A := shared + off
        self.L('sh')
        if off:
            self.d('adc', off)

    def mark(self, n):
        self.C(n); self.sh(2); self.o('sb')
        self.C(1); self.sh(0x40 + n); self.o('sb')

    def be16(self, base, off, name):        # local := big-endian word at local base + off
        self.L(base)
        if off:
            self.d('adc', off)
        self.o('lb'); self.C(8); self.o('shl')
        self.L(base); self.d('adc', off + 1); self.o('lb'); self.o('or'); self.S(name)

    def dec_loop_end(self, var, top, done):
        self.L(var); self.d('adc', -1); self.S(var)
        self.L(var); self.jr('cj', done); self.jr('j', top)


def build_loader():
    """The matrix demo's loader: 4-byte length, then the payload to $80010000, then gcall."""
    a = Asm()
    a.d('ajw', 5)
    a.d('ldc', 0xA1); a.op('mint'); a.d('ldnlp', SHARED_WORDS); a.d('adc', 2); a.op('sb')
    a.op('mint'); a.op('sthf'); a.op('mint'); a.op('stlf')
    a.op('clrhalterr'); a.op('testerr')
    a.d('ldlp', 0); a.op('mint'); a.d('ldnlp', 4); a.d('ldc', 4); a.op('in')
    a.op('mint'); a.d('ldnlp', LOAD_WORDS)
    a.op('mint'); a.d('ldnlp', 4)
    a.d('ldl', 0)
    a.op('in')
    a.d('ldc', 0xA2); a.op('mint'); a.d('ldnlp', SHARED_WORDS); a.d('adc', 2); a.op('sb')
    a.op('mint'); a.d('ldnlp', LOAD_WORDS); a.op('gcall')
    code = a.assemble()
    assert len(code) < 255
    return code
