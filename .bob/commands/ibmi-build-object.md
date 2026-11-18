---
description: Compile IBM i source (RPGLE, CLLE, DDS DSPF) with TOBi and auto-fix compile errors a set number of times
argument-hint: <source-file...> | --changed [--commit <sha> | --staged | --unstaged | --base <ref>] [--max-fix-attempts <n>]
---
Build IBM i objects for this project using the `ibmi-build-object` skill.

Arguments typed after the command: $1 $2 $3 $4 $5 $6 $7 $8
(Ignore any placeholder above that still reads literally as a dollar sign and a number.)

How to interpret the arguments:

- One or more source paths (for example `qrpglesrc/custinq.pgm.rpgle`) → compile exactly those files.
- `--changed` with no other option → compile every compilable source with uncommitted changes (staged + unstaged + untracked).
- `--changed --commit <sha>` → compile the sources changed in that commit (`HEAD` for the last commit).
- `--changed --staged` / `--changed --unstaged` → only staged, or only unstaged + untracked, changes.
- `--changed --base <ref>` → everything changed on this branch since `<ref>` (for example `origin/main`), plus uncommitted work.
- `--max-fix-attempts <n>` → override the configured number of fix attempts for this run.
- No arguments → if a file is open in the editor and it is a compilable IBM i source, compile it; otherwise behave like `--changed`.

Then follow the `ibmi-build-object` skill exactly: start a new run, compile, and if there are compile errors fix them and recompile only until the attempt limit is reached. Never commit, push, or edit build configuration. Finish with the summary the skill describes.
