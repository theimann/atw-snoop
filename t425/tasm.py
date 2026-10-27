# Copied from github.com/stephane-perez/atw800-matrix-demo (MIT, LICENSE.matrix); addition: move.
"""Mini-assembleur Transputer (T414/T425, entiers seulement).

Chaque instruction directe est encodée avec les préfixes pfix/nfix nécessaires.
Les sauts relatifs (j, cj, call) et le motif « ldc (cible - suivant); ldpi »
sont résolus par relaxation : les tailles ne font que croître jusqu'à stabilité.
"""

DIRECT = {
    'j': 0x0, 'ldlp': 0x1, 'pfix': 0x2, 'ldnl': 0x3, 'ldc': 0x4, 'ldnlp': 0x5,
    'nfix': 0x6, 'ldl': 0x7, 'adc': 0x8, 'call': 0x9, 'cj': 0xA, 'ajw': 0xB,
    'eqc': 0xC, 'stl': 0xD, 'stnl': 0xE, 'opr': 0xF,
}

# Opérations secondaires (codes INMOS). Seules celles utilisées sont listées.
OPS = {
    'rev': 0x00, 'lb': 0x01, 'bsub': 0x02, 'gcall': 0x06, 'in': 0x07,
    'prod': 0x08, 'gt': 0x09, 'wsub': 0x0A, 'out': 0x0B, 'sub': 0x0C,
    'outbyte': 0x0E, 'outword': 0x0F, 'rem': 0x1F, 'ret': 0x20,
    'ldtimer': 0x22, 'sttimer': 0x54, 'testerr': 0x29, 'tin': 0x2B, 'div': 0x2C,
    'ldpi': 0x1B, 'stlf': 0x1C, 'sthf': 0x18, 'stopp': 0x15, 'not': 0x32,
    'xor': 0x33, 'sb': 0x3B, 'shr': 0x40, 'shl': 0x41, 'mint': 0x42,
    'and': 0x46, 'or': 0x4B, 'sum': 0x52, 'mul': 0x53, 'clrhalterr': 0x57,
    'dup': 0x5A, 'move': 0x4A,
}


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def prefix(fn, n):
    """Octets d'une fonction directe `fn` avec l'opérande signé `n`."""
    n = s32(n)
    out = []

    def rec(f, v):
        if 0 <= v < 16:
            out.append((f << 4) | v)
        elif v >= 16:
            rec(DIRECT['pfix'], v >> 4)
            out.append((f << 4) | (v & 15))
        else:
            rec(DIRECT['nfix'], (~v) >> 4)
            out.append((f << 4) | (v & 15))
    rec(fn, n)
    return out


class Asm:
    def __init__(self):
        self.items = []     # (kind, a, b)
        self.labels = {}

    # ---- émission ----
    def label(self, name):
        self.items.append(('label', name, None))

    def d(self, fn, n):
        """Fonction directe avec opérande entier."""
        self.items.append(('d', DIRECT[fn], n))

    def op(self, name):
        self.items.append(('d', DIRECT['opr'], OPS[name]))

    def jr(self, fn, label):
        """j / cj / call vers une étiquette."""
        self.items.append(('jr', DIRECT[fn], label))

    def ldaddr(self, label):
        """A := adresse absolue de `label` (ldc (label - suivant); ldpi)."""
        self.items.append(('pi', label, None))

    def raw(self, data):
        self.items.append(('raw', bytes(data), None))

    def align(self, n=4):
        self.items.append(('align', n, None))

    # ---- assemblage ----
    def _layout(self, sizes):
        addr = 0
        labels = {}
        starts = []
        for i, (kind, a, b) in enumerate(self.items):
            starts.append(addr)
            if kind == 'label':
                labels[a] = addr
            elif kind == 'd':
                addr += len(prefix(a, b))
            elif kind in ('jr', 'pi'):
                addr += sizes[i]
            elif kind == 'raw':
                addr += len(a)
            elif kind == 'align':
                addr = (addr + a - 1) // a * a
        return labels, starts, addr

    def assemble(self):
        sizes = {i: 1 for i, it in enumerate(self.items) if it[0] in ('jr', 'pi')}
        for _ in range(100):
            labels, starts, _end = self._layout(sizes)
            changed = False
            for i, (kind, a, b) in enumerate(self.items):
                if kind == 'jr':
                    nxt = starts[i] + sizes[i]
                    need = len(prefix(a, labels[b] - nxt))
                elif kind == 'pi':
                    nxt = starts[i] + sizes[i]
                    need = len(prefix(DIRECT['ldc'], labels[a] - nxt)) + 2
                else:
                    continue
                if need > sizes[i]:
                    sizes[i] = need
                    changed = True
            if not changed:
                break
        else:
            raise RuntimeError('relaxation non convergente')
        labels, starts, end = self._layout(sizes)
        out = bytearray()
        for i, (kind, a, b) in enumerate(self.items):
            if kind == 'd':
                out += bytes(prefix(a, b))
            elif kind == 'jr':
                nxt = starts[i] + sizes[i]
                enc = prefix(a, labels[b] - nxt)
                assert len(enc) <= sizes[i]
                # bourrage impossible : on exige taille exacte
                enc = prefix(a, labels[b] - nxt)
                if len(enc) != sizes[i]:
                    # une taille plus grande que nécessaire : rembourrer avec pfix 0
                    enc = [0x20] * (sizes[i] - len(enc)) + enc
                out += bytes(enc)
            elif kind == 'pi':
                nxt = starts[i] + sizes[i]
                disp = labels[a] - nxt
                ldc = prefix(DIRECT['ldc'], disp)
                pad = sizes[i] - 2 - len(ldc)
                out += bytes([0x20] * pad + ldc + prefix(DIRECT['opr'], OPS['ldpi']))
            elif kind == 'raw':
                out += a
            elif kind == 'align':
                while len(out) % a:
                    out.append(0)
        assert len(out) == end, (len(out), end)
        self.labels = labels
        return bytes(out)


def disasm(code, base=0):
    """Désassembleur minimal pour les tests."""
    names = {v: k for k, v in DIRECT.items()}
    opn = {v: k for k, v in OPS.items()}
    out = []
    o = 0
    i = 0
    start = 0
    while i < len(code):
        b = code[i]
        if o == 0:
            start = i
        fn, n = b >> 4, b & 15
        o |= n
        i += 1
        if fn == 2:
            o = (o << 4) & 0xFFFFFFFF
            continue
        if fn == 6:
            o = ((~o) << 4) & 0xFFFFFFFF
            continue
        val = s32(o)
        if fn == 15:
            text = 'opr %s' % opn.get(val, hex(val))
        else:
            text = '%s %d' % (names[fn], val)
        out.append('%04x: %s' % (base + start, text))
        o = 0
    return out
