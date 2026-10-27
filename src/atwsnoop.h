/* atw-snoop: the ATW800/2 in the Mega STE or TT (tested on the Mega STE, TOS 2.06) as a second screen for debugging. The program keeps the ST's own
 * screen; text written here appears on the card's HDMI output. With the card's T425 running the cost is one word
 * write into the card per character written (the T425 draws); without it the 68000 draws, at most
 * snoop_budget cells a snoop_frame() call. No card: every call does nothing (snoop_mode 0).
 *
 * All calls in supervisor mode. snoop_init() once, before the program changes vectors or the MFP (it uses the
 * 200 Hz clock for its time-outs); snoop_frame() once a frame; snoop_exit() before the program ends. Source for any
 * project: compile atwsnoop.c with the program, one C file (int size does not matter: shorts and longs only). */
#ifndef ATWSNOOP_H
#define ATWSNOOP_H
#ifdef __cplusplus
extern "C" {
#endif

/* colours: ink | paper << 4 */
enum { SNOOP_BLACK, SNOOP_WHITE, SNOOP_RED, SNOOP_GREEN, SNOOP_BLUE, SNOOP_YELLOW, SNOOP_CYAN, SNOOP_MAGENTA,
       SNOOP_GREY, SNOOP_LGREY, SNOOP_DRED, SNOOP_DGREEN, SNOOP_NAVY, SNOOP_ORANGE, SNOOP_DCYAN, SNOOP_DGREY };
#define SNOOP_ATTR(ink, paper) ((unsigned char)((ink) | (paper) << 4))
#define SNOOP_NORMAL SNOOP_ATTR(SNOOP_LGREY, SNOOP_NAVY)

/* glyphs besides ASCII 32-126: 1-8 a bar from the bottom (eighths), 9-16 a bar from the left, 17 a triangle,
   127 a full block */
#define SNOOP_VBAR(n) ((unsigned char)(n))           /* n 1..8 */
#define SNOOP_HBAR(n) ((unsigned char)(8 + (n)))     /* n 1..8 */
#define SNOOP_MARK 17
#define SNOOP_BLOCK 127

enum { SNOOP_NONE, SNOOP_T425, SNOOP_68000 };
extern short snoop_mode;                             /* SNOOP_NONE, SNOOP_T425 or SNOOP_68000 */
extern short snoop_cols, snoop_rows;                   /* the grid */
extern short snoop_budget;                           /* 68000 mode: cells drawn per snoop_frame() (default 12) */
extern long snoop_pass_ticks;                        /* T425 mode: its timer ticks (15600 a s) between passes, set
                                                      before snoop_init (0: 312, ~20 ms; 1: no pause) */
extern const char *snoop_why;                        /* how snoop_init() decided, for a log */
extern short snoop_desktop;                          /* 1: the card was the xVDI desktop (redrawn after the program) */

/* finds the card and starts the screen; flags: SNOOP_NO_T425 (the 68000 draws). Returns snoop_mode. */
#define SNOOP_NO_T425 1
short snoop_init(short flags);
void snoop_exit(void);                               /* T425 stopped, LUT and shared memory back, own mode off */
void snoop_frame(void);                              /* once a frame: the 68000 draws; the T425's watchdog */
unsigned long snoop_clock(void);                     /* MFP timer C: 38400 a second (26 us), any mode, supervisor */
void snoop_speed(long ns[3]);                        /* ns a word: write ST RAM, write the grid, read the grid */
unsigned char snoop_heartbeat(void);                /* the T425's pass counter (low byte), 0 in other modes */

void snoop_cls(unsigned char attr);
void snoop_putc(short col, short row, unsigned char attr, unsigned char ch);
void snoop_puts(short col, short row, unsigned char attr, const char *s);   /* clipped at the right edge */
void snoop_fill(short col, short row, short n, unsigned char attr, unsigned char ch);
void snoop_hex(short col, short row, unsigned char attr, unsigned long v, short digits);
void snoop_dec(short col, short row, unsigned char attr, long v, short width);   /* right-aligned in width */
char *snoop_sdec(char *d, long v);                  /* v in decimal at d (0-ended); returns the end. Any mode */
void snoop_bar(short col, short row, short width, unsigned char attr, long value, long max);   /* in eighths */
void snoop_mem(short col, short row, unsigned char attr, const volatile void *addr, short bytes);  /* hex dump */

/* Slots: the cheap way to show values every frame. Define a field once; then snoop_set() hands over the raw value
   (with the T425: one long write, ~5 us at 8 MHz) and the card formats it. Without the T425 the 68000 formats it,
   only when it changed. Fixed fields: a decimal longer than its width loses its top digits. snoop_cls() shows
   every slot again at its next value. At most 128 slots; snoop_slot returns -1 when none is left or the field is
   off the grid (snoop_set and snoop_setmem then do nothing). */
enum { SNOOP_HEX = 1, SNOOP_DEC, SNOOP_BAR, SNOOP_BYTES };
short snoop_slot(short kind, short col, short row, unsigned char attr, short n, long max);
                                                   /* n: hex digits (8 at most), decimal width (11), bar width in
                                                      cells (max = the full bar), bytes shown as hex (32); a decimal
                                                      with max > 0 shows value x max / 1000 (e.g. snoop_clock counts as
                                                      us: 26042), the product under 2^31 */
extern volatile long *snoop_slotv;
void snoop_set_slow(short id, long v);
#define snoop_set(id, v) do { if (snoop_slotv && (id) >= 0) snoop_slotv[(long)(id) << 2] = (long)(v); \
                            else snoop_set_slow((id), (long)(v)); } while (0)
void snoop_setmem(short id, const volatile void *addr);   /* a SNOOP_BYTES slot: its n bytes from addr */

/* a sweep graph (oscilloscope): one column a call, the cursor one ahead; level 0..8 */
typedef struct { short col, row, width, pos; } snoop_sweep_t;
void snoop_sweep(snoop_sweep_t *s, unsigned char attr, short level);

/* a log in rows top..bottom-1: each line overwrites the oldest, a triangle marks the newest */
void snoop_log_area(short top, short bottom);
void snoop_log(unsigned char attr, const char *s);

#ifdef __cplusplus
}
#endif
#endif
