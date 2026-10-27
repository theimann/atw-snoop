/* atw-snoop (atwsnoop.h): the ATW800/2 as a second screen for debugging.
 *
 * The card: found at the screen's address when it is the xVDI desktop, else by its id at $A00000 or $C00000
 * (each read guarded against a bus error). As the xVDI desktop at 256 colours its page is borrowed (16 LUT entries
 * from 240 on, put back at the end; the desktop draws itself anew when the program has ended); without xVDI the
 * card is set to 640x480 at 8 bpp and switched off at the end (atw/atwtest.c's mode table).
 *
 * The grid of cells lives in the card's shared memory (card + $1F4000 + $400): a word a cell, character << 8 |
 * attribute. With the T425 (build_snoop.py) the 68000 only writes words there; the T425 compares the grid with its
 * copy and draws what changed. Without it (no answer, SNOOP_NO_T425, or the watchdog found it stopped) the 68000
 * draws from the same grid in snoop_frame(), snoop_budget cells a call, against a copy in ST RAM.
 *
 * The T425's boot and link are those of FujiTerm's ATW800/2 renderer. Shared memory the grid uses is copied
 * at the start and put back at the end. */

#include "atwsnoop.h"
#include "snoopprog.h"

#ifndef SNOOP_COLS_MAX                               /* the grid's limits (ST RAM: 4 bytes a cell + 1 KB) */
#define SNOOP_COLS_MAX 100
#endif
#ifndef SNOOP_ROWS_MAX
#define SNOOP_ROWS_MAX 40
#endif

#define OFF_SHARED 0x1F4000L
#define OFF_GRID 0x400L
#define OFF_INFO 0x1FDC00L
#define OFF_LUT 0x1FF000L
#define OFF_ID 0x1FF200L
#define OFF_VTG 0x1FF800L
#define OFF_LINK 0x1FFAC0L
#define CPM_ID 0x2063706dUL                       /* " cpm" at the id + 24 */
#define HZ200 (*(volatile unsigned long *)0x4BA)
#define PEN_DESKTOP 240
#define NCELL (SNOOP_COLS_MAX * SNOOP_ROWS_MAX)
#define SLOTS_MAX 128                              /* = SLOTS_MAX in build_snoop.py */
#define OFF_SLOTS ((OFF_GRID + 2L * NCELL + 15) & ~15L)  /* the slots (build_snoop.py): count, 16 bytes a slot */
#define OFF_SLOTDATA (OFF_SLOTS + 16 + 16L * SLOTS_MAX)  /* 32 bytes a slot (kind 4) */
#define SAVE_BYTES (OFF_SLOTDATA + 32L * SLOTS_MAX)

/* short snoop_probe(long addr): the long at addr into snoop_probe_val; 1 = read, 0 = bus error (nothing there).
   Supervisor mode. The bus error vector points at be_trap for the one read; the stack pointer is put back by hand
   (68000 and 68030 frames alike). Here as top-level assembler so the library is one C file (any build system). */
#define SNOOP_STR2(x) #x
#define SNOOP_STR(x) SNOOP_STR2(x)
#define SNOOP_SYM(n) SNOOP_STR(__USER_LABEL_PREFIX__) #n
short snoop_probe(long addr);
extern unsigned long snoop_probe_val;
__asm__(
  "\t.globl\t" SNOOP_SYM(snoop_probe) "\n"
  "\t.globl\t" SNOOP_SYM(snoop_probe_val) "\n"
  "\t.text\n"
  SNOOP_SYM(snoop_probe) ":\n"
  "\tmove.l\t4(%sp),%a0\n"
  "\tmove.w\t%sr,-(%sp)\n"
  "\tor.w\t#0x0700,%sr\n"
  "\tmove.l\t0x08,snoop_old_be\n"
  "\tmove.l\t%sp,snoop_probe_sp\n"
  "\tlea\tsnoop_be_trap(%pc),%a1\n"
  "\tmove.l\t%a1,0x08\n"
  "\tmove.l\t(%a0)," SNOOP_SYM(snoop_probe_val) "\n"
  "\tnop\n"
  "\tmoveq\t#1,%d0\n"
  "\tbra.s\tsnoop_probe_out\n"
  "snoop_be_trap:\n"
  "\tmove.l\tsnoop_probe_sp,%sp\n"
  "\tmoveq\t#0,%d0\n"
  "snoop_probe_out:\n"
  "\tmove.l\tsnoop_old_be,0x08\n"
  "\tmove.w\t(%sp)+,%sr\n"
  "\trts\n"
  "\t.bss\n"
  "\t.even\n"
  SNOOP_SYM(snoop_probe_val) ":\t.long\t0\n"
  "snoop_old_be:\t.long\t0\n"
  "snoop_probe_sp:\t.long\t0\n"
  "\t.text\n");

short snoop_mode, snoop_cols, snoop_rows, snoop_budget = 12, snoop_desktop;
long snoop_pass_ticks;
volatile long *snoop_slotv;                          /* T425 mode: the first slot's value; else 0 */
const char *snoop_why = "not started";

static unsigned long base;
static volatile unsigned char *L, *SH;
static volatile unsigned short *grid;
static volatile unsigned short *rowp[SNOOP_ROWS_MAX];  /* each row's first cell: no multiply a call */
static volatile unsigned char *page;              /* the grid's top left */
static long stride, page_bytes;
static short pen0, own_mode, data_cache, link_err, seq;
static unsigned short lut_saved[16];
static unsigned char saved[SAVE_BYTES];
static long saved_n;
static unsigned short sent[NCELL];                 /* 68000 mode: what is on the screen */
static short scan, wd_count;
static unsigned char wd_last;
static short log_top, log_bottom, log_line;
static struct {                                    /* the slots as defined, and the 68000 mode's last values */
  unsigned char kind, attr, n, gen;
  short col, row, valid;
  long max, last;
} slot[SLOTS_MAX];
static short nslots;

#define VW(o) (*(volatile unsigned short *)(base + (o)))
#define VTG(n) VW(OFF_VTG + 2 * (n))

/* ---- time-outs that end even with the 200 Hz clock stopped ---- */
static unsigned long t0, loops;
static void tmo_start(void) { t0 = HZ200; loops = 0; }
static short tmo(long ticks) { return HZ200 - t0 > (unsigned long)ticks || ++loops > (unsigned long)ticks * 4000; }

static long cookie(unsigned long id)
{
  long *jar = *(long **)0x5A0;
  if (jar)
    for (; jar[0]; jar += 2)
      if ((unsigned long)jar[0] == id) return jar[1];
  return -1;
}

static void cache_clear(void)                      /* a 68020/68030: what the data cache holds of the card is old */
{
  if (data_cache)
    __asm__ volatile(".long 0x4e7a0002\n\tor.w #0x800,%%d0\n\t.long 0x4e7b0002" ::: "d0");
}

/* ---- the link (as in FujiTerm's renderer) ---- */
static void t_reset(void)
{
  L[17] = 1;
  L[19] = 0;
  tmo_start(); while (!tmo(4)) ;
  L[17] = 0;
  L[5] = 0;
  L[7] = 0;
  tmo_start(); while (!tmo(4)) ;
}

static void putb(unsigned char b)
{
  if (link_err) return;
  tmo_start();
  while (!(L[7] & 1))
    if (tmo(200)) { link_err = 1; return; }
  L[3] = b;
}

static short getb(void)
{
  tmo_start();
  while (!(L[5] & 1))
    if (tmo(200)) return -1;
  return L[1];
}

static void putl(unsigned long v)
{
  short i;
  for (i = 0; i < 4; i++, v >>= 8) putb((unsigned char)v);
}

static const unsigned char ident_code[24] = {     /* TRPROC.H's identification: the answer is AA AA 00 00 */
  23, 0xB1, 0xD1, 0x24, 0xF2, 0x21, 0xFC, 0x24, 0xF2, 0x21, 0xF8, 0xF0, 0x60, 0x5C,
  0x2A, 0x2A, 0x2A, 0x4A, 0xFF, 0x21, 0x2F, 0xFF, 0x02, 0x00
};

static short t425_boot(unsigned long clear_bytes, unsigned char clear_pen)
{
  long i;
  link_err = 0;
  t_reset();
  for (i = 0; i < 24; i++) putb(ident_code[i]);
  if (link_err || getb() != 0xAA || getb() != 0xAA || getb() != 0 || getb() != 0) {
    snoop_why = "the T425 does not answer on the link";
    return 0;
  }
  for (i = 0; i < 0x110; i += 2) *(volatile unsigned short *)(SH + i) = 0;
  t_reset();
  putb((unsigned char)sizeof snoop_loader);
  for (i = 0; i < (long)sizeof snoop_loader; i++) putb(snoop_loader[i]);
  putl(sizeof snoop_payload);
  for (i = 0; i < (long)sizeof snoop_payload; i++) putb(snoop_payload[i]);
  putl((unsigned long)page - base);               /* P0 */
  putl(stride);                                   /* P1 */
  putl(snoop_cols);                                 /* P2 */
  putl(snoop_rows);                                 /* P3 */
  putl(OFF_SHARED - 0x1F4000L + OFF_GRID);        /* P4: the grid's offset in shared memory */
  putl(pen0);                                     /* P5 */
  putl(0);                                        /* P6: 4096 reads (64 KB) before each pass */
  putl(clear_bytes);                              /* P7, P8 */
  putl(clear_pen);
  putl(snoop_pass_ticks);                           /* P9: T425 timer ticks between passes (0: 312, ~20 ms) */
  putl(OFF_SLOTS);                                /* P10 */
  for (i = 11; i < 16; i++) putl(0);
  for (i = 0; i < 4096; i++) putb(snoop_font[i]);
  if (link_err) { snoop_why = "the link stopped taking bytes during the boot"; return 0; }
  tmo_start();
  while (cache_clear(), SH[0] != 0x5A)
    if (tmo(400)) { snoop_why = "the T425 program did not start"; return 0; }
  wd_last = SH[3];
  wd_count = 0;
  return 1;
}

static void t425_stop(void)
{
  seq = (seq + 1) & 0xFF;
  if (!seq) seq = 1;
  SH[0x101] = 6;
  SH[0x100] = (unsigned char)seq;
  tmo_start();
  while (cache_clear(), SH[1] != seq)
    if (tmo(100)) break;
  t_reset();
}

/* ---- the 68000 draws ---- */
static unsigned long nib[16];
static short nib_attr = -1;

static void draw(short i, unsigned short v)
{
  unsigned char attr = (unsigned char)v;
  const unsigned char *f = snoop_font + ((v >> 8) << 4);
  volatile unsigned long *d = (volatile unsigned long *)(page + (long)(i / snoop_cols) * 16 * stride + (i % snoop_cols) * 8);
  long s = stride >> 2;
  short r;
  if (attr != nib_attr) {
    unsigned long fg = (unsigned long)(pen0 + (attr & 15)) * 0x01010101UL;
    unsigned long bg = (unsigned long)(pen0 + (attr >> 4)) * 0x01010101UL;
    short n;
    for (n = 0; n < 16; n++)
      nib[n] = (bg & ~((n & 8 ? 0xFF000000UL : 0) | (n & 4 ? 0xFF0000UL : 0) | (n & 2 ? 0xFF00UL : 0) | (n & 1 ? 0xFFUL : 0)))
             | (fg & ((n & 8 ? 0xFF000000UL : 0) | (n & 4 ? 0xFF0000UL : 0) | (n & 2 ? 0xFF00UL : 0) | (n & 1 ? 0xFFUL : 0)));
    nib_attr = attr;
  }
  for (r = 0; r < 16; r++, d += s) {
    unsigned char b = f[r];
    d[0] = nib[b >> 4];
    d[1] = nib[b & 15];
  }
}

static void to_68000(const char *why)
{
  short i;
  for (i = 0; i < snoop_cols * snoop_rows; i++) sent[i] = 0xFFFF;
  scan = 0;
  snoop_slotv = 0;
  for (i = 0; i < nslots; i++) slot[i].valid = 0; /* the 68000 formats them from the next snoop_set */
  snoop_mode = SNOOP_68000;
  snoop_why = why;
}

void snoop_frame(void)
{
  short n, looked, ncell;
  if (snoop_mode == SNOOP_T425) {                     /* the watchdog: the pass counter must move within ~50 calls */
    if (++wd_count < 50) return;
    wd_count = 0;
    cache_clear();
    if (SH[3] != wd_last) { wd_last = SH[3]; return; }
    t_reset();
    to_68000("the T425 stopped (watchdog); the 68000 draws");
    return;
  }
  if (snoop_mode != SNOOP_68000) return;
  ncell = snoop_cols * snoop_rows;
  for (n = looked = 0; n < snoop_budget && looked < ncell; looked++) {
    unsigned short v = grid[scan];
    if (v != sent[scan]) {
      draw(scan, v);
      sent[scan] = v;
      n++;
    }
    if (++scan == ncell) scan = 0;
  }
}

/* ---- the card ---- */
static short card_at(unsigned long b)
{
  return snoop_probe((long)(b + OFF_ID + 24)) && snoop_probe_val == CPM_ID;
}

static void set_640x480(void)                     /* atwtest.c: VESA timings, the PLL at 5x the pixel clock */
{
  VTG(0) = 0;
  VTG(15) = 1;
  VTG(9) = 14 - 1;
  VTG(10) = 3 - 1;
  VTG(11) = 8 - 1;
  VTG(1) = 16; VTG(2) = 96; VTG(3) = 48; VTG(4) = 640;
  VTG(5) = 10; VTG(6) = 2; VTG(7) = 33; VTG(8) = 480;
  VTG(12) = 0;
  VTG(13) = 0;
  VTG(0) = 0x19;                                  /* on, 8 bpp through the LUT */
}

static unsigned short rgb565(short r, short g, short b) { return (unsigned short)((r >> 3) << 11 | (g >> 2) << 5 | b >> 3); }

static const unsigned char pal[16][3] = {
  { 0, 0, 0 }, { 255, 255, 255 }, { 255, 64, 64 }, { 64, 224, 64 }, { 80, 120, 255 }, { 255, 230, 64 },
  { 64, 230, 230 }, { 230, 80, 230 }, { 128, 128, 128 }, { 200, 200, 200 }, { 140, 0, 0 }, { 0, 110, 0 },
  { 0, 0, 90 }, { 255, 150, 0 }, { 0, 110, 120 }, { 64, 64, 64 }
};

short snoop_init(short flags)
{
  unsigned long lb = (unsigned long)*(void *volatile *)0x44E & 0xFFFFFFUL;   /* _v_bas_ad, 24 bits: $FEA00000 = $A00000 */
  static const unsigned long cand[2] = { 0xA00000UL, 0xC00000UL };
  volatile unsigned char *info;
  long i, clear;
  short k;
  snoop_mode = SNOOP_NONE;
  snoop_desktop = own_mode = 0;
  base = 0;
  if (lb >= 0xA00000UL && lb < 0xE00000UL && card_at(lb & ~0x1FFFFFUL))
    base = lb & ~0x1FFFFFUL;
  for (k = 0; !base && k < 2; k++)
    if (card_at(cand[k])) base = cand[k];
  if (!base) { snoop_why = "no ATW800/2 card"; return 0; }
  data_cache = cookie(0x5F435055UL) >= 20;        /* '_CPU' */
  info = (volatile unsigned char *)(base + OFF_INFO);
  L = (volatile unsigned char *)(base + OFF_LINK);
  SH = (volatile unsigned char *)(base + OFF_SHARED);
  grid = (volatile unsigned short *)(SH + OFF_GRID);
  cache_clear();
  if (*(volatile unsigned long *)info == 0x41545738UL && *(volatile unsigned short *)(info + 64) == 0x4711) {
    /* xVDI's ATWInfo: its page, at 256 colours only (the LUT) */
    if (*(volatile unsigned short *)(info + 26) != 8) { snoop_why = "xVDI's desktop is not at 256 colours"; return 0; }
    stride = *(volatile unsigned short *)(info + 24);
    page = (volatile unsigned char *)(base + (*(volatile unsigned long *)(info + 16) & 0x1FFFFFUL));
    snoop_cols = (short)(*(volatile unsigned short *)(info + 28) / 8);
    snoop_rows = (short)(*(volatile unsigned short *)(info + 30) / 16);
    page_bytes = stride * *(volatile unsigned short *)(info + 30);
    pen0 = PEN_DESKTOP;
    snoop_desktop = 1;
  } else {
    set_640x480();
    own_mode = 1;
    stride = 640;
    page = (volatile unsigned char *)base;
    snoop_cols = 80;
    snoop_rows = 30;
    page_bytes = 640L * 480;
    pen0 = 0;
  }
  if (snoop_cols > SNOOP_COLS_MAX) snoop_cols = SNOOP_COLS_MAX;
  if (snoop_rows > SNOOP_ROWS_MAX) snoop_rows = SNOOP_ROWS_MAX;
  snoop_cols &= ~1;
  for (k = 0; k < snoop_rows; k++) rowp[k] = grid + (long)k * snoop_cols;
  for (k = 0; k < 16; k++) {
    volatile unsigned short *lut = (volatile unsigned short *)(base + OFF_LUT) + pen0 + k;
    lut_saved[k] = *lut;
    *lut = rgb565(pal[k][0], pal[k][1], pal[k][2]);
  }
  saved_n = SAVE_BYTES;
  for (i = 0; i < saved_n; i++) saved[i] = SH[i];
  for (i = 0; i < (long)snoop_cols * snoop_rows; i++) grid[i] = (unsigned short)(' ' << 8 | SNOOP_NORMAL);
  for (i = OFF_SLOTS; i < SAVE_BYTES; i += 4) *(volatile long *)(SH + i) = 0;
  nslots = 0;
  clear = page_bytes;                             /* the whole page, in the grid's paper */
  if (!(flags & SNOOP_NO_T425) && t425_boot(clear, (unsigned char)(pen0 + (SNOOP_NORMAL >> 4)))) {
    snoop_mode = SNOOP_T425;
    snoop_slotv = (volatile long *)(SH + OFF_SLOTS + 16);
    snoop_why = snoop_desktop ? "T425 draws; the card is the xVDI desktop" : "T425 draws; the card set to 640x480";
  } else {
    volatile unsigned long *p = (volatile unsigned long *)page;
    unsigned long c = (unsigned long)(pen0 + (SNOOP_NORMAL >> 4)) * 0x01010101UL;
    const char *why = flags & SNOOP_NO_T425 ? "the 68000 draws (asked for)" : snoop_why;
    if (L) t_reset();
    for (i = page_bytes >> 2; i; i--) *p++ = c;
    to_68000(why);
    for (i = 0; i < (long)snoop_cols * snoop_rows; i++) sent[i] = (unsigned short)(' ' << 8 | SNOOP_NORMAL);
  }
  log_top = 0;
  log_bottom = snoop_rows;
  log_line = -1;
  return snoop_mode;
}

void snoop_exit(void)
{
  long i;
  short k;
  if (snoop_mode == SNOOP_NONE) return;
  if (snoop_mode == SNOOP_T425) t425_stop();
  snoop_slotv = 0;
  nslots = 0;
  for (i = 0; i + 1 < saved_n; i += 2) *(volatile unsigned short *)(SH + i) = (unsigned short)(saved[i] << 8 | saved[i + 1]);
  for (k = 0; k < 16; k++) ((volatile unsigned short *)(base + OFF_LUT))[pen0 + k] = lut_saved[k];
  if (own_mode) VTG(0) = 0;                       /* the card's output off, as before */
  snoop_mode = SNOOP_NONE;
  snoop_why = "ended";
}

/* 200 Hz ticks x 192 + timer C's count (192 down to 1). Timer C may have run out with its interrupt (which counts
   HZ200) not yet taken, e.g. while the caller holds interrupts off: the count has started again but HZ200 is one tick
   behind, and the clock went back by up to 5 ms (WoF on the Mega STE: sections of "176159750 us").
   The pending bit (IPRB bit 5) tells; a count read after the reload is high (> 96), one read before it low. */
unsigned long snoop_clock(void)
{
  unsigned long h;
  unsigned char t, p;
  do { h = HZ200; t = *(volatile unsigned char *)0xFFFA23; p = *(volatile unsigned char *)0xFFFA0D; } while (h != HZ200);
  if ((p & 0x20) && t > 96) h++;
  return h * 192 + 192 - t;
}

void snoop_speed(long ns[3])                        /* 2000 words each, the loop's own cost included */
{
  static volatile unsigned short ram[1];
  volatile unsigned short *g = grid, *t;
  unsigned short keep;
  unsigned long c;
  short i, k;
  ns[0] = ns[1] = ns[2] = 0;
  if (!snoop_mode) return;
  keep = g[0];
  for (k = 0; k < 3; k++) {
    t = k ? g : ram;
    c = snoop_clock();
    if (k < 2) for (i = 0; i < 2000; i++) *t = (unsigned short)i;
    else for (i = 0; i < 2000; i++) (void)*t;
    ns[k] = (long)((snoop_clock() - c) * 15625 / 1200);   /* 26042 ns a count, 2000 words */
  }
  g[0] = keep;
}

unsigned char snoop_heartbeat(void)
{
  if (snoop_mode != SNOOP_T425) return 0;
  cache_clear();
  return SH[3];
}

/* ---- writing ---- */
/* The writers below cost the program's time, not the card's (Mega STE 8 MHz: a grid word write is
   0.6 us over ST RAM, ATWSNOOP's ~140 calls took 12 ms a frame): row pointers instead of a multiply, hex digits
   written straight into the cells. Every call clips to the grid. */
static const unsigned short hexw[16] = {
  '0' << 8, '1' << 8, '2' << 8, '3' << 8, '4' << 8, '5' << 8, '6' << 8, '7' << 8,
  '8' << 8, '9' << 8, 'A' << 8, 'B' << 8, 'C' << 8, 'D' << 8, 'E' << 8, 'F' << 8 };

void snoop_putc(short col, short row, unsigned char attr, unsigned char ch)
{
  if (snoop_mode && col >= 0 && col < snoop_cols && row >= 0 && row < snoop_rows) rowp[row][col] = (unsigned short)(ch << 8 | attr);
}

void snoop_puts(short col, short row, unsigned char attr, const char *s)
{
  volatile unsigned short *g;
  short n;
  if (!snoop_mode || row < 0 || row >= snoop_rows || col < 0) return;
  g = rowp[row] + col;
  for (n = snoop_cols - col; *s && n > 0; n--) *g++ = (unsigned short)((unsigned char)*s++ << 8 | attr);
}

void snoop_fill(short col, short row, short n, unsigned char attr, unsigned char ch)
{
  volatile unsigned short *g;
  unsigned short v = (unsigned short)(ch << 8 | attr);
  if (!snoop_mode || row < 0 || row >= snoop_rows || col < 0) return;
  if (col + n > snoop_cols) n = snoop_cols - col;
  for (g = rowp[row] + col; n > 0; n--) *g++ = v;
}

static void gaps(short id)                        /* a SNOOP_BYTES slot: the spaces between its bytes, in its colour */
{
  short k;
  for (k = 1; k < slot[id].n; k++) rowp[slot[id].row][slot[id].col + 3 * k - 1] = (unsigned short)(' ' << 8 | slot[id].attr);
}

void snoop_cls(unsigned char attr)
{
  short r;
  for (r = 0; r < snoop_rows; r++) snoop_fill(0, r, snoop_cols, attr, ' ');
  for (r = 0; r < nslots; r++) {                  /* the slots' cells are gone: shown again */
    slot[r].valid = 0;
    if (slot[r].kind == SNOOP_BYTES) gaps(r);
    if (snoop_slotv) SH[OFF_SLOTS + 16 + 16L * r + 7] = ++slot[r].gen;
  }
}

/* ---- slots: raw values formatted by the T425 (build_snoop.py) ---- */

short snoop_slot(short kind, short col, short row, unsigned char attr, short n, long max)
{
  short id, room;
  volatile unsigned char *d;
  long off;
  if (!snoop_mode || nslots >= SLOTS_MAX || kind < SNOOP_HEX || kind > SNOOP_BYTES || col < 0 || col >= snoop_cols
      || row < 0 || row >= snoop_rows || n <= 0) return -1;
  room = snoop_cols - col;
  if (kind == SNOOP_BYTES) {                        /* "XX " each, the last without its space */
    if (n > 32) n = 32;
    if (3 * n - 1 > room) n = (room + 1) / 3;
  } else {
    if (kind == SNOOP_HEX && n > 8) n = 8;
    if (kind == SNOOP_DEC && n > 11) n = 11;
    if (n > room) n = room;
  }
  id = nslots;
  slot[id].kind = (unsigned char)kind; slot[id].attr = attr; slot[id].n = (unsigned char)n;
  slot[id].col = col; slot[id].row = row; slot[id].max = max; slot[id].valid = 0;
  slot[id].gen++;
  if (kind == SNOOP_BYTES) gaps(id);
  if (snoop_slotv) {
    d = SH + OFF_SLOTS + 16 + 16L * id;
    off = 2L * (rowp[row] - grid + col);
    *(volatile long *)d = 0;
    d[4] = (unsigned char)kind; d[5] = attr; d[6] = (unsigned char)n;
    d[8] = (unsigned char)(off >> 8); d[9] = (unsigned char)off;
    *(volatile long *)(d + 12) = max;
    d[7] = slot[id].gen;
    SH[OFF_SLOTS + 3] = (unsigned char)(id + 1); /* the T425 takes it from its next pass */
  }
  nslots = id + 1;
  return id;
}

void snoop_set_slow(short id, long v)               /* snoop_set without the T425: the 68000 formats, when it changed */
{
  if (!snoop_mode || id < 0 || id >= nslots) return;
  if (snoop_slotv) { snoop_slotv[(long)id << 2] = v; return; }
  if (slot[id].valid && slot[id].last == v) return;
  slot[id].last = v; slot[id].valid = 1;
  switch (slot[id].kind) {
  case SNOOP_HEX: snoop_hex(slot[id].col, slot[id].row, slot[id].attr, v, slot[id].n); break;
  case SNOOP_DEC: snoop_dec(slot[id].col, slot[id].row, slot[id].attr, slot[id].max > 0 ? v * slot[id].max / 1000 : v,
                       slot[id].n); break;
  case SNOOP_BAR: snoop_bar(slot[id].col, slot[id].row, slot[id].n, slot[id].attr, v, slot[id].max); break;
  }
}

void snoop_setmem(short id, const volatile void *addr)
{
  const volatile unsigned char *p = (const volatile unsigned char *)addr;
  short n;
  if (!snoop_mode || id < 0 || id >= nslots || slot[id].kind != SNOOP_BYTES) return;
  n = slot[id].n;
  if (snoop_slotv) {
    volatile unsigned char *d = SH + OFF_SLOTDATA + 32L * id;
    if (!((long)p & 1)) {                         /* words: half the bus cycles */
      const volatile unsigned short *w = (const volatile unsigned short *)p;
      volatile unsigned short *e = (volatile unsigned short *)d;
      for (; n > 1; n -= 2) *e++ = *w++;
      if (n) *(volatile unsigned char *)e = *(const volatile unsigned char *)w;
    } else
      for (; n > 0; n--) *d++ = *p++;
  } else
    snoop_mem(slot[id].col, slot[id].row, slot[id].attr, p, n);
}

void snoop_hex(short col, short row, unsigned char attr, unsigned long v, short digits)
{
  volatile unsigned short *g;
  short i, lim;
  if (!snoop_mode || row < 0 || row >= snoop_rows || col < 0) return;
  if (digits > 8) digits = 8;
  lim = snoop_cols - col;
  g = rowp[row] + col;
  if (digits <= 4) {                              /* word shifts: a long shift costs twice as much */
    unsigned short w = (unsigned short)v;
    for (i = digits - 1; i >= 0; i--, w >>= 4) if (i < lim) g[i] = hexw[w & 15] | attr;
  } else
    for (i = digits - 1; i >= 0; i--, v >>= 4) if (i < lim) g[i] = hexw[v & 15] | attr;
}

/* u / 10 with two 68000 divu (32 by 16 bits each, no overflow): the remainder comes back; libgcc's 32-bit
   division costs several times as much a digit */
static unsigned short div10(unsigned long *u)
{
  unsigned long q, r;
  __asm__("move.l %2,%0\n\tclr.w %0\n\tswap %0\n\tdivu.w #10,%0\n\tmove.l %0,%1\n\tmove.w %2,%1\n\t"
          "divu.w #10,%1\n\tswap %0\n\tmove.w %1,%0\n\tswap %1"
          : "=&d"(q), "=&d"(r) : "d"(*u) : "cc");
  *u = q;
  return (unsigned short)r;
}

char *snoop_sdec(char *d, long v)
{
  char b[12];
  short i = 11;
  unsigned long u = v < 0 ? -(unsigned long)v : (unsigned long)v;
  b[i] = 0;
  do { b[--i] = (char)('0' + div10(&u)); } while (u);
  if (v < 0) *d++ = '-';
  while (b[i]) *d++ = b[i++];
  *d = 0;
  return d;
}

void snoop_dec(short col, short row, unsigned char attr, long v, short width)
{
  char b[12];
  short i = 11, neg = v < 0;
  unsigned long u = neg ? -(unsigned long)v : (unsigned long)v;
  if (!snoop_mode) return;
  b[i] = 0;
  do { b[--i] = (char)('0' + div10(&u)); } while (u && i > 1);
  if (neg) b[--i] = '-';
  while (11 - i < width && i > 0) b[--i] = ' ';
  snoop_puts(col, row, attr, b + i);
}

void snoop_bar(short col, short row, short width, unsigned char attr, long value, long max)
{
  long e;
  short k;
  short f;
  volatile unsigned short *g;
  unsigned short full = (unsigned short)(SNOOP_BLOCK << 8 | attr), none = (unsigned short)(' ' << 8 | attr);
  if (!snoop_mode || row < 0 || row >= snoop_rows || col < 0) return;
  if (max <= 0) max = 1;
  if (value < 0) value = 0;
  if (value > max) value = max;
  e = value * width * 8 / max;                    /* eighths */
  if (col + width > snoop_cols) width = snoop_cols - col;
  g = rowp[row] + col;
  for (k = 0, f = (short)(e >> 3); k < width && k < f; k++) *g++ = full;
  if (k < width && (e & 7)) { *g++ = (unsigned short)(SNOOP_HBAR(e & 7) << 8 | attr); k++; }
  for (; k < width; k++) *g++ = none;
}

void snoop_mem(short col, short row, unsigned char attr, const volatile void *addr, short bytes)
{
  const volatile unsigned char *p = (const volatile unsigned char *)addr;
  volatile unsigned short *g;
  short lim;
  if (!snoop_mode || row < 0 || row >= snoop_rows || col < 0) return;
  g = rowp[row] + col;
  for (lim = snoop_cols - col; bytes > 0 && lim > 0; bytes--, lim -= 3, g += 3) {
    unsigned char b = *p++;
    g[0] = hexw[b >> 4] | attr;
    if (lim > 1) g[1] = hexw[b & 15] | attr;
  }
}

void snoop_sweep(snoop_sweep_t *s, unsigned char attr, short level)
{
  if (!snoop_mode) return;
  if (level < 0) level = 0;
  if (level > 8) level = 8;
  snoop_putc(s->col + s->pos, s->row, attr, level ? SNOOP_VBAR(level) : '_');
  if (++s->pos >= s->width) s->pos = 0;
  snoop_putc(s->col + s->pos, s->row, attr, SNOOP_MARK);
}

void snoop_log_area(short top, short bottom)
{
  log_top = top;
  log_bottom = bottom > snoop_rows ? snoop_rows : bottom;
  log_line = -1;
}

void snoop_log(unsigned char attr, const char *s)
{
  short n;
  if (!snoop_mode || log_bottom <= log_top) return;
  if (log_line >= 0) snoop_putc(0, log_line, attr, ' ');
  log_line = log_line < log_top || log_line + 1 >= log_bottom ? log_top : log_line + 1;
  snoop_putc(0, log_line, attr, SNOOP_MARK);
  for (n = 0; s[n] && n < snoop_cols - 1; n++) ;
  snoop_puts(1, log_line, attr, s);
  snoop_fill(1 + n, log_line, snoop_cols - 1 - n, attr, ' ');
}
