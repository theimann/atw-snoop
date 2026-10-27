# Case study: Wings of Fury on the Mega STE

The reference build for atw-snoop: how the method is used in a real game. A showcase, not a release of
the game. Tested on a Mega STE with TOS 2.06 (8 MHz, the card in its own 640×480 mode); not on a TT.

[Wings of Fury](https://github.com/theimann/wings-of-fury-atari-ste) (Amiga, 1990) runs on the STE
and Mega STE at the original's steady 16.7 frames a second: every frame gets 3 VBLs (60 ms). A frame
that needs more is late and shows as a stutter.

## The debug build

`make atw` builds the game with atw-snoop. 18 profile markers split each frame into sections (input,
logic, sound mix, effects, the drawing steps of the engine, the panel, the pacing wait). Each marker
stores `snoop_clock()`; once a frame the differences go to the card as raw counts.

The card shows:

- the frame number, its length in VBLs, frames per second, late frames in total and this second;
- the busy time (everything but the pacing wait) as a bar, in µs and in % of the 60 ms;
- per section a live bar (full = 10 ms), the current, average and worst time of the last second, and
  "blame": how often that section was the one furthest over its average in a late frame;
- sound (the mixer's lead, voices, resyncs), entities in use, free memory, the plane's state;
- a log line for every late frame: its VBLs, the busy time, the section that overran most.

## Scripted runs

A replay script plays the same flight every time, so builds can be compared. The scripted build
writes a run log, and with the card also:

- `LATE`: every section of every late frame;
- `PROF`: each second's section averages;
- `LPC`: where the 68000 was at the last six VBLs of a late frame (a stub ahead of the VBL
  interrupt keeps the interrupted PC; `atw_late.py` names the functions from the build's map).

## What the screen showed

- **Measuring itself.** The first hardware run showed sections of 176 million µs now and then: the
  clock went back by up to 5 ms when timer C had run out and its interrupt was still pending. Fixed
  in the library (`snoop_clock` adds the pending tick).
- **The cost of measuring.** At first the profile cost 2.2 ms a frame. With slot addresses and
  marker pairs worked out once, raw counts handed over and the card scaling them to µs, it costs
  about 0.8 ms.
- **The late frames come from the sound mixer.** Mixing costs 0.5 ms a frame in quiet flight but
  8–20 ms while guns fire, shells hit and the plane crashes: the game mixes four voices in
  software at 12.5 kHz, about 6 ms per VBL of sound with all four playing. A late frame then needs
  a fourth VBL of sound, which makes the next frame later still. The VBL samples agree: in late
  frames the mixer holds 15 % of the samples outside the pacing wait, against 2 % of the time on
  average.

Run 1 on the Mega STE at 8 MHz: 80 late frames of 3713 without the debug screen, 122 of 3698 with it.

## The reference build

The reference build ships as one folder (`WOFSNOOP`): `WOF.PRG` (play), `SCRIPTED.PRG` (all scripted runs one
after another, no keys), `WOF.DAT` (all data), `RUNS\` (the scripts, the logs, the run build).
Without the card it is the normal game.
