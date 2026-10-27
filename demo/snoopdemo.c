/* ATWSNOOP: atw-snoop (atwsnoop.h) at work. The ST's own screen (ST low) shows a moving bar, as a game would; the
 * ATW800/2's HDMI output shows the debug screen: the frame, the 200 Hz clock, the cost of the debug calls
 * themselves (measured with MFP timer C, shown as a bar and a sweep graph), the ST palette and MFP
 * registers live, the last key, and a log line a second. The background of the ST screen turns red for as long
 * as the debug calls take, so their cost shows on the ST's picture too.
 *
 * Command line: a number = seconds, then it ends by itself (unattended runs); "n" = without the T425 (the 68000
 * draws); "p<ticks>" = the T425's pause between passes; "f" = Mega STE at 16 MHz, "c" = 16 MHz with the
 * cache (both put back at the end; other machines ignore them); "s" = the figures as slots (the T425 formats);
 * a path (K:\X\ATWSNOOP.LOG) = where the log goes, else ATWSNOOP.LOG in the current folder: what snoop_init
 * found and the costs measured. Ctrl+Q ends it. */

#include "atwsnoop.h"

long gemdos(short fn, ...);
long xbios(short fn, ...);
long bios(short fn, ...);
#define Cconws(s) gemdos(0x09, (long)(s))
#define Fcreate(n, a) gemdos(0x3c, (long)(n), (short)(a))
#define Fclose(h) gemdos(0x3e, (short)(h))
#define Fwrite(h, c, b) gemdos(0x40, (short)(h), (long)(c), (long)(b))
#define Supexec(f) xbios(38, (long)(f))
/* BIOS calls from inside Supexec: not through crt0's wrappers, which keep the return address in one static
   place, so a call inside Supexec overwrote Supexec's own (the rig found it: run() returned to address 0) */
static long bios2(short fn, short arg)
{
  register long r __asm__("d0");
  __asm__ volatile("move.w %2,-(%%sp)\n\tmove.w %1,-(%%sp)\n\ttrap #13\n\taddq.l #4,%%sp"
                   : "=r"(r) : "g"(fn), "g"(arg) : "d1", "d2", "a0", "a1", "a2", "memory", "cc");
  return r;
}
#define Bconstat(d) bios2(1, (short)(d))
#define Bconin(d) bios2(2, (short)(d))
#define Kbshift(m) bios2(11, (short)(m))

extern char *basepage;
#define HZ200 (*(volatile unsigned long *)0x4BA)
#define FRCLOCK (*(volatile unsigned long *)0x466)
#define PAL ((volatile unsigned short *)0xFF8240)
#define B(a) (*(volatile unsigned char *)(a))

static short seconds, no_t425, use_slots;
static short sl[32];                               /* "s": the figures as slots */
static char logpath[100] = "ATWSNOOP.LOG";
static unsigned char screen_mem[32000 + 256];
static unsigned short old_pal[16];
static unsigned char old_hi, old_mid, old_lo, old_rez, old_speed, speed, mste;
static long frames, cost_us, cost_max, cost_sum, cost_n, init_ticks, hb_first, hb_last, hb_ticks;
static long sec_sum, sec_n, sec_max, sec_frames, frame_us = 20000;
static unsigned long sec_clock;   /* the last second's calls; the frame, measured */
static char speed_txt[40], human[80];
static short last_ascii, last_scan, mode_seen;
static char logbuf[3000];
static short loglen;

static void out(const char *s) { while (*s && loglen < (short)sizeof logbuf - 1) logbuf[loglen++] = *s++; }
static void out_num(long v)
{
  char b[12];
  short i = 11;
  unsigned long u = v < 0 ? -(unsigned long)v : (unsigned long)v;
  b[i] = 0;
  do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
  if (v < 0) b[--i] = '-';
  out(b + i);
}

static unsigned long vcount(void)                 /* the shifter's video address counter */
{
  return (unsigned long)B(0xFF8205) << 16 | (unsigned long)B(0xFF8207) << 8 | B(0xFF8209);
}

static void st_screen(unsigned char *s)
{
  short i;
  unsigned long a = (unsigned long)s;
  for (i = 0; i < 32000 / 4; i++) ((long *)s)[i] = 0;
  B(0xFF8201) = (unsigned char)(a >> 16);
  B(0xFF8203) = (unsigned char)(a >> 8);
  B(0xFF820D) = (unsigned char)a;                 /* STE: the low byte (0, the buffer is aligned) */
  B(0xFF8260) = 0;
  for (i = 0; i < 16; i++) PAL[i] = (unsigned short)(i == 0 ? 0x000 : i == 1 ? 0x777 : 0x070 + i);
  PAL[0] = 0x002;
}

static void bar(unsigned char *s, short x, short on)  /* 16 pixels wide, plane 0, rows 40..159 */
{
  short y;
  unsigned short *p = (unsigned short *)(s + 40L * 160 + (x >> 4) * 8);
  for (y = 0; y < 120; y++, p += 80) *p = on ? 0xFFFF : 0;
}

static char *cat(char *d, const char *s) { while (*s) *d++ = *s++; *d = 0; return d; }

static char *ms(char *d, long us)                 /* 2750 -> "2.75 ms" */
{
  long c = (us + 5) / 10;
  d = snoop_sdec(d, c / 100);
  *d++ = '.'; *d++ = (char)('0' + c / 10 % 10); *d++ = (char)('0' + c % 10);
  return cat(d, " ms");
}

static char *pct(char *d, long us)                /* the share of a frame, "14%" */
{
  return cat(snoop_sdec(d, (us * 100 + frame_us / 2) / frame_us), "%");
}

static char *share(char *d, long avg, long most)  /* "2.75 ms = 14% of a 20.00 ms frame, worst 3.39 ms = 17%" */
{
  d = pct(cat(ms(d, avg), " = "), avg);
  d = ms(cat(d, " of a "), frame_us);
  return pct(cat(ms(cat(d, " frame, worst "), most), " = "), most);
}

static void header(void)
{
  snoop_cls(SNOOP_NORMAL);
  snoop_fill(0, 0, snoop_cols, SNOOP_ATTR(SNOOP_BLACK, SNOOP_CYAN), ' ');
  snoop_puts(1, 0, SNOOP_ATTR(SNOOP_BLACK, SNOOP_CYAN), "atw-snoop demo " VERSION);
  snoop_puts(snoop_cols - 12, 0, SNOOP_ATTR(SNOOP_BLACK, SNOOP_CYAN), snoop_mode == SNOOP_T425 ? "T425 draws" : "68000 draws");
  snoop_puts(1, 1, SNOOP_ATTR(SNOOP_GREY, SNOOP_NAVY), snoop_why);
  snoop_puts(1, 2, SNOOP_ATTR(SNOOP_GREY, SNOOP_NAVY), speed_txt);
  snoop_puts(1, 3, SNOOP_NORMAL, "frame");
  snoop_puts(20, 3, SNOOP_NORMAL, "200 Hz");
  snoop_puts(40, 3, SNOOP_NORMAL, "$466");
  snoop_puts(1, 4, SNOOP_NORMAL, "debug calls this frame          us, most        us");
  snoop_puts(1, 5, SNOOP_NORMAL, "share of frame");
  snoop_puts(63, 5, SNOOP_NORMAL, "%");
  snoop_puts(1, 6, SNOOP_ATTR(SNOOP_YELLOW, SNOOP_NAVY), "last second: (measuring)");
  snoop_puts(1, 7, SNOOP_NORMAL, "ST palette");
  {
    static const char *const reg[12] = { "GPIP", "AER", "DDR", "IERA", "IERB", "IPRA", "IPRB", "ISRA", "ISRB",
                                         "IMRA", "IMRB", "VR" };
    short i;
    snoop_puts(1, 10, SNOOP_NORMAL, "MFP");
    for (i = 0; i < 12; i++) snoop_puts(5 + 5 * i, 10, SNOOP_NORMAL, reg[i]);
  }
  snoop_puts(1, 12, SNOOP_NORMAL, "last key: ascii    scan    shift    bar at x");
  snoop_puts(1, 14, SNOOP_NORMAL, "share of frame, a column a frame (full = 50%):");
  snoop_puts(1, 17, SNOOP_ATTR(SNOOP_YELLOW, SNOOP_NAVY), "log:");
  snoop_log_area(18, snoop_rows);
}

static unsigned long mch(void)                    /* the _MCH cookie, 0 without one */
{
  unsigned long *c = *(unsigned long **)0x5A0;
  for (; c && c[0]; c += 2) if (c[0] == 0x5F4D4348UL) return c[1];
  return 0;
}

static void run(void)
{
  static snoop_sweep_t sw;
  unsigned char *scr = (unsigned char *)(((unsigned long)screen_mem + 255) & ~255UL);
  unsigned long t_end, last_fc, start = HZ200;
  short x = 0, dx = 4, i, quit = 0, level, tick = 49, log_now;
  unsigned char shift;
  char line[80];
  for (i = 0; i < 16; i++) old_pal[i] = PAL[i];
  old_hi = B(0xFF8201); old_mid = B(0xFF8203); old_lo = B(0xFF820D); old_rez = B(0xFF8260);
  mste = mch() == 0x00010010UL;
  if (mste) {                                     /* $FF8E21: bit 0 cache, bit 1 16 MHz (HANDBOOK) */
    old_speed = B(0xFF8E21);
    if (speed) B(0xFF8E21) = speed;
    out(B(0xFF8E21) & 2 ? "Mega STE 16 MHz, cache " : "Mega STE 8 MHz, cache ");
    out(B(0xFF8E21) & 1 ? "on\r\n" : "off\r\n");
    cat(cat(speed_txt, B(0xFF8E21) & 2 ? "Mega STE at 16 MHz, cache " : "Mega STE at 8 MHz, cache "),
        B(0xFF8E21) & 1 ? "on" : "off");
  }

  init_ticks = HZ200;
  snoop_init(no_t425 ? SNOOP_NO_T425 : 0);
  init_ticks = HZ200 - init_ticks;
  mode_seen = snoop_mode;
  out("snoop_init: mode "); out_num(snoop_mode); out(" ("); out(snoop_why); out("), grid ");
  out_num(snoop_cols); out("x"); out_num(snoop_rows); out(", desktop "); out_num(snoop_desktop);
  out(", "); out_num(init_ticks * 5); out(" ms\r\n");
  if (snoop_mode) {
    long ns[3];
    snoop_speed(ns);
    out("a word: ST RAM write "); out_num(ns[0]); out(" ns, grid write "); out_num(ns[1]);
    out(" ns, grid read "); out_num(ns[2]); out(" ns\r\n");
  }
  header();
  if (use_slots) {
    const unsigned char w = SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), c = SNOOP_ATTR(SNOOP_CYAN, SNOOP_NAVY);
    sl[0] = snoop_slot(SNOOP_DEC, 7, 3, w, 8, 0);
    sl[1] = snoop_slot(SNOOP_DEC, 27, 3, w, 8, 0);
    sl[2] = snoop_slot(SNOOP_HEX, 45, 3, w, 8, 0);
    sl[3] = snoop_slot(SNOOP_DEC, 25, 4, w, 6, 0);
    sl[4] = snoop_slot(SNOOP_DEC, 42, 4, w, 6, 0);
    sl[5] = snoop_slot(SNOOP_BAR, 18, 5, SNOOP_ATTR(SNOOP_GREEN, SNOOP_NAVY), 40, frame_us);
    sl[6] = snoop_slot(SNOOP_DEC, 59, 5, w, 4, 0);
    sl[7] = snoop_slot(SNOOP_BYTES, 1, 8, c, 16, 0);
    sl[8] = snoop_slot(SNOOP_BYTES, 1, 9, c, 16, 0);
    for (i = 0; i < 12; i++) sl[9 + i] = snoop_slot(SNOOP_HEX, 6 + 5 * i, 11, c, 2, 0);
    sl[21] = snoop_slot(SNOOP_HEX, 17, 12, w, 2, 0);
    sl[22] = snoop_slot(SNOOP_HEX, 25, 12, w, 2, 0);
    sl[23] = snoop_slot(SNOOP_HEX, 34, 12, w, 2, 0);
    sl[24] = snoop_slot(SNOOP_DEC, 46, 12, w, 3, 0);
    out("slots: "); out_num(sl[24] >= 0 ? 25 : 0); out(" figures handed over raw\r\n");
  }
  sw.col = 1; sw.row = 15; sw.width = snoop_cols - 2 > 64 ? 64 : snoop_cols - 2; sw.pos = 0;
  hb_first = snoop_heartbeat(); hb_ticks = HZ200;

  st_screen(scr);
  t_end = HZ200 + seconds * 200L;
  last_fc = FRCLOCK;
  while (!quit && (!seconds || (long)(HZ200 - t_end) < 0)) {
    unsigned long v0, v1, line50 = (unsigned long)scr + 50L * 160;
    while (FRCLOCK == last_fc) ;
    last_fc = FRCLOCK;
    sec_frames++;
    bar(scr, x, 0);
    x += dx;
    if (x <= 0 || x >= 304) dx = -dx;
    bar(scr, x, 1);
    /* everything the screen shows is worked out first; the red stretch holds the debug calls only */
    shift = (unsigned char)Kbshift(-1);
    level = (short)(cost_us * 16 / frame_us);     /* eighths of half a frame */
    log_now = ++tick == 50;
    if (log_now) {
      char *p = line;
      tick = 0;
      {                                           /* the frame's length, measured: 50 or 60 Hz */
        unsigned long now = snoop_clock();
        if (sec_frames > 1) frame_us = (long)((now - sec_clock) * 625 / 24) / (sec_frames - 1);
        sec_clock = now; sec_frames = 1;
      }
      p = snoop_sdec(cat(p, "second "), frames / 50);
      if (sec_n) {
        p = share(cat(p, ": "), sec_sum / sec_n, sec_max);
        cat(share(cat(human, "last second: "), sec_sum / sec_n, sec_max), "   ");
      }
      sec_sum = sec_n = sec_max = 0;
    }
    while (vcount() < line50) ;                   /* measure inside the picture: lines of 64 us */
    PAL[0] = 0x700;
    v0 = snoop_clock();
    if (use_slots) {
      snoop_set(sl[0], frames);
      snoop_set(sl[1], HZ200 - start);
      snoop_set(sl[2], FRCLOCK);
      snoop_set(sl[3], cost_us);
      snoop_set(sl[4], cost_max);
      snoop_set(sl[5], cost_us);
      snoop_set(sl[6], cost_us * 100 / frame_us);
      if (log_now && *human) snoop_puts(1, 6, SNOOP_ATTR(SNOOP_YELLOW, SNOOP_NAVY), human);
      snoop_setmem(sl[7], (const volatile void *)0xFF8240);
      snoop_setmem(sl[8], (const volatile void *)0xFF8250);
      for (i = 0; i < 12; i++) snoop_set(sl[9 + i], B(0xFFFA01 + 2 * i));
      snoop_set(sl[21], last_ascii);
      snoop_set(sl[22], last_scan);
      snoop_set(sl[23], shift);
      snoop_set(sl[24], x);
    } else {
    snoop_dec(7, 3, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), frames, 8);
    snoop_dec(27, 3, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), (long)(HZ200 - start), 8);
    snoop_hex(45, 3, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), FRCLOCK, 8);
    snoop_dec(25, 4, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), cost_us, 6);
    snoop_dec(42, 4, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), cost_max, 6);
    snoop_bar(18, 5, 40, SNOOP_ATTR(SNOOP_GREEN, SNOOP_NAVY), cost_us, frame_us);
    snoop_dec(59, 5, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), cost_us * 100 / frame_us, 4);
    if (log_now && *human) snoop_puts(1, 6, SNOOP_ATTR(SNOOP_YELLOW, SNOOP_NAVY), human);
    snoop_mem(1, 8, SNOOP_ATTR(SNOOP_CYAN, SNOOP_NAVY), (const volatile void *)0xFF8240, 16);
    snoop_mem(1, 9, SNOOP_ATTR(SNOOP_CYAN, SNOOP_NAVY), (const volatile void *)0xFF8250, 16);
    for (i = 0; i < 12; i++) snoop_hex(6 + 5 * i, 11, SNOOP_ATTR(SNOOP_CYAN, SNOOP_NAVY), B(0xFFFA01 + 2 * i), 2);
    snoop_hex(17, 12, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), (unsigned long)last_ascii, 2);
    snoop_hex(25, 12, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), (unsigned long)last_scan, 2);
    snoop_hex(34, 12, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), shift, 2);
    snoop_dec(46, 12, SNOOP_ATTR(SNOOP_WHITE, SNOOP_NAVY), x, 3);
    }
    snoop_sweep(&sw, level > 6 ? SNOOP_ATTR(SNOOP_RED, SNOOP_NAVY) : SNOOP_ATTR(SNOOP_GREEN, SNOOP_NAVY), level);
    if (log_now && *human) snoop_log(SNOOP_NORMAL, line);
    snoop_frame();
    v1 = snoop_clock();
    PAL[0] = 0x002;
    cost_us = (long)((v1 - v0) * 625 / 24);         /* 26.04 us a count; the video counter stops at the screen's end */
    if (cost_us > cost_max) cost_max = cost_us;
    cost_sum += cost_us;
    cost_n++;
    sec_sum += cost_us; sec_n++;
    if (cost_us > sec_max) sec_max = cost_us;
    frames++;
    if (Bconstat(2)) {
      long k = Bconin(2);
      last_ascii = (short)(k & 0xFF);
      last_scan = (short)((k >> 16) & 0xFF);
      if (last_ascii == 0x11 || (last_scan == 0x10 && (Kbshift(-1) & 4))) quit = 1;
    }
  }
  hb_last = snoop_heartbeat();
  hb_ticks = HZ200 - hb_ticks;
  snoop_exit();
  for (i = 0; i < 16; i++) PAL[i] = old_pal[i];
  B(0xFF8201) = old_hi; B(0xFF8203) = old_mid; B(0xFF820D) = old_lo; B(0xFF8260) = old_rez;
  if (mste) B(0xFF8E21) = old_speed;
}

void atw_main(void)
{
  unsigned char *cl = (unsigned char *)basepage + 0x80;
  short n = cl[0], i;
  long h;
  for (i = 1; i <= n && i < 127; i++) {
    if (cl[i] == ' ') continue;
    if (cl[i + 1] == ':' || cl[i] == '\\') {      /* a path: the log goes there */
      short k = 0;
      while (i <= n && cl[i] != ' ' && k < (short)sizeof logpath - 1) logpath[k++] = (char)cl[i++];
      logpath[k] = 0;
    } else if (cl[i] == 'p' || cl[i] == 'P') {    /* p<ticks>: the T425's pause between passes */
      while (i + 1 <= n && cl[i + 1] >= '0' && cl[i + 1] <= '9') snoop_pass_ticks = snoop_pass_ticks * 10 + cl[++i] - '0';
    } else if (cl[i] >= '0' && cl[i] <= '9') seconds = (short)(seconds * 10 + cl[i] - '0');
    else if (cl[i] == 'n' || cl[i] == 'N') no_t425 = 1;
    else if (cl[i] == 'f' || cl[i] == 'F') speed = 2;
    else if (cl[i] == 's' || cl[i] == 'S') use_slots = 1;
    else if (cl[i] == 'c' || cl[i] == 'C') speed = 3;
  }
  Cconws("ATWSNOOP " VERSION ": the debug screen is on the ATW800/2's output. Ctrl+Q ends.\r\n");
  out("ATWSNOOP " VERSION "\r\n");
  if (snoop_pass_ticks) { out("T425 pass ticks "); out_num(snoop_pass_ticks); out("\r\n"); }
  Supexec(run);
  out("frames "); out_num(frames); out(", debug calls a frame: average ");
  out_num(cost_n ? cost_sum / cost_n : 0); out(" us, most "); out_num(cost_max); out(" us\r\n");
  if (cost_n) { share(cat(human, "debug calls a frame: "), cost_sum / cost_n, cost_max); out(human); out("\r\n"); }
  if (mode_seen == 1) {
    out("T425 pass counter: "); out_num(hb_first); out(" -> "); out_num(hb_last); out(" (low byte) in ");
    out_num(hb_ticks * 5); out(" ms\r\n");
  }
  out("mode at the end: "); out(snoop_why); out("\r\n");
  h = Fcreate(logpath, 0);
  if (h >= 0) { Fwrite((short)h, loglen, logbuf); Fclose((short)h); }
  logbuf[loglen] = 0;
  Cconws(logbuf);
}
