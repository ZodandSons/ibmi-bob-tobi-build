# Fix-loop demo: ORDDSP + ORDINQ

`qddssrc/orddsp.dspf` and `qrpglesrc/ordinq.pgm.rpgle` are an order inquiry (a display file and the RPGLE program that uses it). Both were written **with deliberate compile errors** so you can watch `/ibmi-build-object` find and fix them.

## Run it

In Bob:

```text
/ibmi-build-object qddssrc/orddsp.dspf qrpglesrc/ordinq.pgm.rpgle
```

or from a terminal (initial compile only, with no AI fixes):

```bash
python .bob/skills/ibmi-build-object/scripts/ibmi_build.py compile qddssrc/orddsp.dspf qrpglesrc/ordinq.pgm.rpgle --new-run
```

## What you should see (2 fix rounds)

**Initial compile.** Both targets fail. ORDINQ can't compile because ORDDSP.FILE wasn't created.

| File | Error | Fix |
|---|---|---|
| `qddssrc/orddsp.dspf:11` | `DSPATR(HJ)`: `HJ` isn't a valid display attribute | `DSPATR(HI)` |
| `qddssrc/orddsp.dspf:22` | `COLR`: not a DDS keyword | `COLOR(BLU)` |
| `qrpglesrc/ordinq.pgm.rpgle` | ORDDSP not found (knock-on from the DSPF failure) | Nothing in the RPG yet. Fix the DSPF first. |

**Fix attempt 1.** ORDDSP compiles. ORDINQ now shows its own errors:

| File | Error | Fix |
|---|---|---|
| `qrpglesrc/ordinq.pgm.rpgle:46` | RNF7030 `ORDCUS` not defined | `ORDCUST` (the display file field) |
| `qrpglesrc/ordinq.pgm.rpgle:53` | Numeric `orderNumber` in a character expression | `%char(orderNumber)` |

**Fix attempt 2.** Everything compiles. Bob reports "initial compile + 2 of 3 fix attempts" and lists each fix from `.bob/build/fix-log.md`.

The exact message IDs and texts for the DDS errors (CPD7xxx) depend on your IBM i release.

## Reset and run again

Bob fixes the files in place. To put the errors back:

```bash
cp demo/fix-loop/orddsp.dspf.broken    qddssrc/orddsp.dspf
cp demo/fix-loop/ordinq.pgm.rpgle.broken qrpglesrc/ordinq.pgm.rpgle
```

Start the next run with `--new-run` (Bob does this automatically on a new `/ibmi-build-object` request).

## Variation: hit the attempt limit

```bash
python .bob/skills/ibmi-build-object/scripts/ibmi_build.py compile qddssrc/orddsp.dspf qrpglesrc/ordinq.pgm.rpgle --new-run --max-fix-attempts 1
```

Bob fixes the DSPF and the retry exposes the RPG errors. The script then refuses another compile (exit 3, fix-attempt limit reached), so Bob stops and reports ORDINQ as still failing.
