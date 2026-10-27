| Start-up for the demo: shrink the TPA to what we use, set a stack, call atw_main().
|
| Started from the SidecarTridge's runner (md-devops 1.1.0) a program must also keep the runner's way back.
| The runner calls Pexec in supervisor mode, so GEMDOS saves its return frame (the parent basepage's saved
| stack pointer, $7C) on the supervisor stack, and the program's own supervisor stack starts just above it.
| GEMDRIVE handles every file call on that stack (registers plus a nested GEMDOS call) and overwrites the
| frame: at the end the program then returned into garbage (white screen and a hang, or bombs). Seen on the
| STE with the debug channel. So the frame and what lies above it are copied at the start, before
| the first system call, and written back just before Pterm. Only when the parent ran in supervisor mode,
| which is the runner; programs started from the desktop are not affected.
	.globl	_start, _gemdos, _xbios, _bios
	.text
_start:
	move.l	4(sp),a5		| basepage
	move.l	0x24(a5),a0		| parent basepage
	move.l	0x7c(a0),a0		| its saved stack pointer (GEMDOS P_A7SAVE): the return frame
	move.l	a0,d0
	btst	#0,d0
	bne.s	1f
	cmp.l	#0x800,d0		| plausible: RAM below us, readable from user mode
	bcs.s	1f
	cmp.l	a5,d0
	bcc.s	1f
	btst	#5,4(a0)		| saved status register: parent in supervisor mode?
	beq.s	1f
	move.l	a0,frame_at
	lea	frame_copy,a1
	moveq	#FRAME_LONGS-1,d1
2:	move.l	(a0)+,(a1)+
	dbf	d1,2b
1:
	move.l	a5,_basepage
	move.l	12(a5),d0		| text
	add.l	20(a5),d0		| data
	add.l	28(a5),d0		| bss
	add.l	#0x100+0x2000,d0
	move.l	a5,d1
	add.l	d0,d1
	and.w	#0xfffe,d1
	move.l	d1,sp			| stack on top of our block
	move.l	d0,-(sp)
	move.l	a5,-(sp)
	clr.w	-(sp)
	move.w	#0x4a,-(sp)		| Mshrink
	trap	#1
	lea	12(sp),sp
	jsr	_atw_main
	move.l	frame_at,d0		| the runner's return frame back (see above)
	beq.s	3f
	move.l	d0,a1
	lea	frame_copy,a0
	moveq	#FRAME_LONGS-1,d1
4:	move.l	(a0)+,(a1)+
	dbf	d1,4b
3:	clr.w	-(sp)			| Pterm0
	trap	#1
FRAME_LONGS = 32			| 128 bytes: the 50-byte frame and the runner's stack above it

| long gemdos(short fn, ...), xbios, bios: the arguments as the system expects them on the stack.
| The system may change d0-d2/a0-a2; the compiler expects d2/a2 kept. The return address, d2 and a2 go onto a small
| stack of their own (8 calls deep), not into one static place: a call made inside a Supexec function through
| these wrappers used to overwrite Supexec's own return address (seen in a test rig: return to 0).
_gemdos:
	bsr.s	trap_save
	trap	#1
	bra.s	trap_done
_xbios:
	bsr.s	trap_save
	trap	#14
	bra.s	trap_done
_bios:
	bsr.s	trap_save
	trap	#13
trap_done:
	move.l	trap_top,a0
	move.l	-(a0),a2
	move.l	-(a0),d2
	move.l	-(a0),a1
	move.l	a0,trap_top
	move.l	a1,-(sp)
	rts
| the caller's return address (above ours), d2 and a2 onto the save stack; the caller's frame left as the trap wants it
trap_save:
	move.l	trap_top,a0
	move.l	(sp)+,a1		| our own return (into the wrapper)
	move.l	(sp)+,(a0)+		| the caller's return address
	move.l	d2,(a0)+
	move.l	a2,(a0)+
	move.l	a0,trap_top
	jmp	(a1)

	.data
	.even
trap_top:	.long	trap_stack
	.globl	_basepage
	.bss
frame_at:	.long	0
frame_copy:	.space	FRAME_LONGS*4
_basepage:	.long	0
trap_stack:	.space	8*12
