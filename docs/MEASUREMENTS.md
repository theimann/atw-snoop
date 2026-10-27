# Measurements

All on a Mega STE (TOS 2.06) with an ATW800/2, timed with `snoop_clock()` (MFP timer C, 26 µs), unless
stated otherwise. Not measured on a TT.

## The bus

| | 8 MHz |
|---|---|
| a word written to ST RAM | 3.8 µs |
| a word written to the card's grid over VME | 4.4 µs |

The bus is not the cost: formatting numbers on the 68000 is.

## The demo, about 140 values a frame, formatted by the 68000

| Version | Speed | Cost a frame |
|---|---|---|
| first writers | 8 MHz | 12.2 ms |
| tuned writers (row table, hex table, direct writes) | 8 MHz | 5.1 ms |
| tuned writers | 16 MHz, cache off | 4.6 ms |
| tuned writers | 16 MHz, cache on | 2.8 ms |
| on the xVDI desktop, grid 100×40 (an earlier version) | 16 MHz, cache off | 4.95 ms |

Without pacing, the T425 read the card's memory without pause and the 68000's VME writes waited:
9.4 ms a frame against 3.2 ms with the 68000 drawing itself. The paced T425 program fixed that.

## Slots, in a game

Wings of Fury's frame profile hands over about 25 raw values a frame (18 bars every frame, the
numbers every fourth frame), the card formats them:

| Version of the per-frame code | Cost a frame (8 MHz) |
|---|---|
| a function call, conversion and checks per section | 2.2 ms |
| slot addresses and marker pairs set once; raw counts, scaled by the card | ~0.8 ms |

Each profile marker itself (`snoop_clock()` plus a store) costs about 20 µs.

## Without a card

Every call returns at once when `snoop_mode` is 0, but the calls still pass their arguments: about 45
calls a frame cost about 1 ms at 8 MHz. Guarded by `if (snoop_mode) { ... }` the block costs one test.

## Memory

- ST RAM for the grid: 4 bytes a cell (a copy of the shared memory the grid borrows, and the 68000
  mode's copy of the screen): 17 KB for the default 100×40.
- In the program file: the T425 program (2.3 KB) and the font (4 KB).
- Wings of Fury's debug build has 52 KB less free memory than the normal build: the library, the font,
  the T425 program, the buffers, and the profile's own tables.

## The effect on a game

Wings of Fury, scripted run 1, Mega STE at 8 MHz, frames that took more than the 3-VBL pacing:

| Build | Late frames |
|---|---|
| without the debug screen | 80 of 3713 |
| with the debug screen | 122 of 3698 |

The difference is the profile's 0.8 ms plus 18 markers of about 20 µs each in a frame that is often
close to its 60 ms. Compare builds with the debug screen against each other, and confirm a result with
the normal build.
