# Measurements

All on a Mega STE with an ATW800/2, timed with `snoop_clock()` (MFP timer C, 26 µs).

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
| on the xVDI desktop, grid 100×40 | 16 MHz, cache off | 4.95 ms |

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
