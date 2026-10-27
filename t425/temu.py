# Copied from github.com/stephane-perez/atw800-matrix-demo (MIT, LICENSE.matrix); additions: wsub, move, a timer that advances by instruction.
"""Simulateur de T425 (sous-ensemble entier) + carte ATW800/2 (VRAM, LUT, VTG).

Plan mémoire vu du Transputer (d'après newatw.c et VGA.OCC de l'archive) :
  0x80000000-0x8000001F  canaux des liens (sortie 0..3, entrée 4..7)
  0x80000020             canal Événement (VBL)
  0x80000070-0x80600000  RAM (interne puis externe)
  0x80600000-0x807FFFFF  VRAM 2 Mo (partagée 0x807F4000, ATWInfo 0x807FDC00)
  0x40000000             LUT RGB565 (256 mots)
  0x40001000             VTG (16 mots), 0x40002300 registre de contrôle
"""
from collections import deque

MINT = 0x80000000
LINK0_OUT = 0x80000000
LINK0_IN = 0x80000010
EVENT = 0x80000020
MEM_SIZE = 0x00808000          # octets à partir de MINT
VRAM = 0x80600000
SHARED = 0x807F4000
ATWINFO = 0x807FDC00
LUT = 0x40000000
VTG = 0x40001000
CTRL = 0x40002300

M32 = 0xFFFFFFFF


def s32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


class Halt(Exception):
    pass


class T425:
    def __init__(self):
        self.mem = bytearray(MEM_SIZE)
        self.lut = [0] * 256
        self.vtg = [0] * 16
        self.ctrl = 0
        self.A = self.B = self.C = 0
        self.W = 0
        self.I = 0
        self.O = 0
        self.link_in = deque()
        self.link_out = bytearray()
        self.vbl_hook = None
        self.vbl_count = 0
        self.icount = 0
        self.timer = 0
        self.unknown_op = None
        self.vbl_stop_after = None

    # ---- mémoire ----
    def _reg(self, addr):
        a = addr & M32
        if LUT <= a < LUT + 0x400:
            return ('lut', (a - LUT) >> 2)
        if VTG <= a < VTG + 0x40:
            return ('vtg', (a - VTG) >> 2)
        if a == CTRL:
            return ('ctrl', 0)
        return None

    def rd8(self, addr):
        o = (addr & M32) - MINT
        if not 0 <= o < MEM_SIZE:
            raise Halt('lecture hors RAM %08x' % (addr & M32))
        return self.mem[o]

    def wr8(self, addr, v):
        o = (addr & M32) - MINT
        if not 0 <= o < MEM_SIZE:
            raise Halt('écriture hors RAM %08x (I=%08x)' % (addr & M32, self.I))
        self.mem[o] = v & 0xFF

    def rd32(self, addr):
        a = addr & M32
        r = self._reg(a)
        if r:
            if r[0] == 'lut':
                return self.lut[r[1]]
            if r[0] == 'vtg':
                return self.vtg[r[1]]
            return self.ctrl
        if a & 3:
            raise Halt('lecture mot non alignée %08x' % a)
        o = a - MINT
        if not 0 <= o <= MEM_SIZE - 4:
            raise Halt('lecture hors RAM %08x' % a)
        return int.from_bytes(self.mem[o:o + 4], 'little')

    def wr32(self, addr, v):
        a = addr & M32
        r = self._reg(a)
        if r:
            if r[0] == 'lut':
                self.lut[r[1]] = v & M32
            elif r[0] == 'vtg':
                self.vtg[r[1]] = v & M32
            else:
                self.ctrl = v & M32
            return
        if a & 3:
            raise Halt('écriture mot non alignée %08x (I=%08x)' % (a, self.I))
        o = a - MINT
        if not 0 <= o <= MEM_SIZE - 4:
            raise Halt('écriture hors RAM %08x (I=%08x)' % (a, self.I))
        self.mem[o:o + 4] = (v & M32).to_bytes(4, 'little')

    # ---- pile ----
    def push(self, v):
        self.C, self.B, self.A = self.B, self.A, v & M32

    def pop(self):
        v = self.A
        self.A, self.B = self.B, self.C
        return v

    # ---- démarrage par lien (convention INMOS) ----
    def boot(self):
        """Lit [n][n octets] sur le lien 0 et les place à MemStart."""
        n = self.link_in.popleft()
        assert n > 1
        code = bytes(self.link_in.popleft() for _ in range(n))
        start = 0x80000070
        for i, b in enumerate(code):
            self.wr8(start + i, b)
        self.I = start
        self.W = (start + n + 3) & ~3          # mot suivant le code
        self.A = start
        self.B = self.W
        self.C = LINK0_IN                      # canal d'entrée du boot
        self.O = 0

    # ---- exécution ----
    def run(self, max_instr=50_000_000):
        mem = self.mem
        for _ in range(max_instr):
            o = self.I - MINT
            b = mem[o] if 0 <= o < MEM_SIZE else self._bad_fetch()
            self.I = (self.I + 1) & M32
            self.icount += 1
            self.timer += 1
            fn = b >> 4
            self.O |= b & 15
            if fn == 2:
                self.O = (self.O << 4) & M32
                continue
            if fn == 6:
                self.O = ((~self.O) << 4) & M32
                continue
            n = s32(self.O)
            self.O = 0
            if fn == 0:
                self.I = (self.I + n) & M32
            elif fn == 1:
                self.push(self.W + 4 * n)
            elif fn == 3:
                self.A = self.rd32(self.A + 4 * n)
            elif fn == 4:
                self.push(n)
            elif fn == 5:
                self.A = (self.A + 4 * n) & M32
            elif fn == 7:
                self.push(self.rd32(self.W + 4 * n))
            elif fn == 8:
                self.A = (self.A + n) & M32
            elif fn == 9:
                self.W = (self.W - 16) & M32
                self.wr32(self.W, self.I)
                self.wr32(self.W + 4, self.A)
                self.wr32(self.W + 8, self.B)
                self.wr32(self.W + 12, self.C)
                self.A = self.I
                self.I = (self.I + n) & M32
            elif fn == 10:
                if self.A == 0:
                    self.I = (self.I + n) & M32
                else:
                    self.pop()
            elif fn == 11:
                self.W = (self.W + 4 * n) & M32
            elif fn == 12:
                self.A = 1 if s32(self.A) == n else 0
            elif fn == 13:
                self.wr32(self.W + 4 * n, self.pop())
            elif fn == 14:
                addr = self.pop()
                val = self.pop()
                self.wr32(addr + 4 * n, val)
            elif fn == 15:
                self.operate(n)
        else:
            raise Halt('limite d\'instructions atteinte')

    def _bad_fetch(self):
        raise Halt('fetch hors RAM I=%08x' % self.I)

    def operate(self, code):
        if code == 0x00:      # rev
            self.A, self.B = self.B, self.A
        elif code == 0x01:    # lb
            self.A = self.rd8(self.A)
        elif code == 0x02:    # bsub
            self.A = (self.A + self.B) & M32
            self.B = self.C
        elif code == 0x0A:    # wsub : A := A + 4*B
            self.A = (self.A + 4 * self.B) & M32
            self.B = self.C
        elif code == 0x4A:    # move : A=count B=dest C=source
            count, dst, src = self.A, self.B, self.C
            self.A = self.B = self.C = 0
            for i in range(count):
                self.wr8(dst + i, self.rd8(src + i))
            self.timer += count // 4
        elif code == 0x06:    # gcall
            self.A, self.I = self.I, self.A
        elif code == 0x07:    # in
            count, chan, ptr = self.A, self.B, self.C
            self.A = self.B = self.C = 0
            self._do_in(count, chan, ptr)
        elif code == 0x08:    # prod
            self.A = (self.A * self.B) & M32
            self.B = self.C
        elif code == 0x09:    # gt : A := B > A
            self.A = 1 if s32(self.B) > s32(self.A) else 0
            self.B = self.C
        elif code == 0x0B:    # out : A=count B=chan C=ptr
            count, chan, ptr = self.A, self.B, self.C
            self.A = self.B = self.C = 0
            if (chan & M32) != LINK0_OUT:
                raise Halt('out sur canal inconnu %08x' % chan)
            for i in range(count):
                self.link_out.append(self.rd8(ptr + i))
        elif code == 0x0E:    # outbyte : A=octet B=canal
            val, chan = self.A, self.B
            self.A = self.B = self.C = 0
            if (chan & M32) != LINK0_OUT:
                raise Halt('outbyte sur canal inconnu')
            self.link_out.append(val & 0xFF)
        elif code == 0x0F:    # outword : A=mot B=canal
            val, chan = self.A, self.B
            self.A = self.B = self.C = 0
            if (chan & M32) != LINK0_OUT:
                raise Halt('outword sur canal inconnu')
            self.link_out += (val & M32).to_bytes(4, 'little')
        elif code == 0x0C:    # sub : A := B - A
            self.A = (self.B - self.A) & M32
            self.B = self.C
        elif code == 0x1B:    # ldpi
            self.A = (self.A + self.I) & M32
        elif code in (0x18, 0x1C):   # sthf, stlf : on dépile seulement
            self.pop()
        elif code == 0x15:    # stopp
            raise Halt('stopp')
        elif code == 0x1F:    # rem : A := B rem A
            a, b = s32(self.A), s32(self.B)
            r = abs(b) % abs(a) if a else 0
            self.A = (-r if b < 0 else r) & M32
            self.B = self.C
        elif code == 0x2C:    # div : A := B / A
            a, b = s32(self.A), s32(self.B)
            q = abs(b) // abs(a) if a else 0
            self.A = (-q if (a < 0) != (b < 0) else q) & M32
            self.B = self.C
        elif code == 0x20:    # ret
            self.I = self.rd32(self.W)
            self.W = (self.W + 16) & M32
        elif code == 0x22:    # ldtimer
            self.push(self.timer // 64)
        elif code == 0x29:    # testerr : empile « pas d'erreur »
            self.push(1)
        elif code == 0x54:    # sttimer (timers stopped after reset until this)
            self.timer = (self.pop() & M32) * 64
        elif code == 0x2B:    # tin
            target = self.pop()
            now = self.timer // 64
            if s32(target - now) > 0:
                self.timer = (target & M32) * 64
        elif code == 0x32:    # not
            self.A = (~self.A) & M32
        elif code == 0x33:    # xor
            self.A = (self.A ^ self.B) & M32
            self.B = self.C
        elif code == 0x3B:    # sb : A=adresse B=valeur
            addr = self.pop()
            val = self.pop()
            self.wr8(addr, val)
        elif code == 0x40:    # shr : A := B >> A (logique)
            n = self.A
            self.A = (self.B >> n) & M32 if n < 32 else 0
            self.B = self.C
        elif code == 0x41:    # shl
            n = self.A
            self.A = (self.B << n) & M32 if n < 32 else 0
            self.B = self.C
        elif code == 0x42:    # mint
            self.push(MINT)
        elif code == 0x46:    # and
            self.A = self.A & self.B
            self.B = self.C
        elif code == 0x4B:    # or
            self.A = self.A | self.B
            self.B = self.C
        elif code == 0x52:    # sum
            self.A = (self.A + self.B) & M32
            self.B = self.C
        elif code == 0x53:    # mul (vérifié, ici comme prod)
            self.A = (self.A * self.B) & M32
            self.B = self.C
        elif code == 0x57:    # clrhalterr
            pass
        elif code == 0x5A:    # dup
            self.push(self.A)
        else:
            raise Halt('opération inconnue %#x (I=%08x)' % (code, self.I))

    def _do_in(self, count, chan, ptr):
        chan &= M32
        if chan == LINK0_IN:
            if len(self.link_in) < count:
                raise Halt('lien vide : %d octets demandés, %d disponibles'
                           % (count, len(self.link_in)))
            for i in range(count):
                self.wr8(ptr + i, self.link_in.popleft())
        elif chan == EVENT:
            self.vbl_count += 1
            self.timer += 16667
            if self.vbl_hook:
                self.vbl_hook(self)
        else:
            raise Halt('in sur canal inconnu %08x' % chan)
