# TOBi conventions used by this project

TOBi (The Object Builder for i, formerly "Better Object Builder") is IBM's open-source, make-based build tool for native QSYS objects. Docs: https://ibm.github.io/ibmi-tobi/

## Project files

- `iproj.json` (project root): object library (`"objlib": "&OBJLIB"`), library list and include path. `&OBJLIB` is resolved from an environment variable, which `ibmi_build.py` exports from `IBMI_OBJLIB`.
- `Rules.mk` (root): `SUBDIRS = qddssrc qrpglesrc qclsrc`, the folders TOBi descends into.
- `Rules.mk` (each folder): one rule per object, `TARGET.TYPE: source-file [prerequisite objects...]`.
- `.ibmi.json` (optional, per folder): per-folder build settings such as `tgtCcsid`.

## Source naming

| File | Object | Created with |
|---|---|---|
| `name.pgm.rpgle` | `NAME.PGM` | CRTBNDRPG |
| `name.pgm.sqlrpgle` | `NAME.PGM` | CRTSQLRPGI |
| `name.rpgle` | `NAME.MODULE` | CRTRPGMOD |
| `name.pgm.clle` | `NAME.PGM` | CRTBNDCL |
| `name.dspf` | `NAME.FILE` | CRTDSPF |
| `name.rpgleinc` | (copybook, no object) | — |

## Rule examples (this repo)

```make
# qddssrc/Rules.mk
CUSTDSP.FILE: custdsp.dspf

# qrpglesrc/Rules.mk  - the DSPF is a prerequisite, so it's built first
CUSTINQ.PGM: custinq.pgm.rpgle CUSTDSP.FILE

# qclsrc/Rules.mk
CUSTMENU.PGM: custmenu.pgm.clle CUSTINQ.PGM
```

## What `ibmi_build.py` does on the IBM i

1. Uploads only changed files (SHA-1 manifest) to `IBMI_REMOTE_DIR` with `tar` over SSH, and tags them with `setccsid` (`IBMI_SETCCSID`, default 1208).
2. Runs `/QOpenSys/pkgs/bin/bash -s` with a generated script that:
   - exports `OBJLIB` / `CURLIB`
   - runs `makei compile -f <source>` once per target
   - after each target, copies `.logs/joblog.json` and `.logs/output.log`, and moves `.evfevent/*` into `.bob-remote/logs/<n>.*`
   - streams `.bob-remote/logs` back as a tar file.
3. Parses the EVFEVENT `FILEID` and `ERROR` records into `file:line:col MSGID sev text`. If there are no event files, it falls back to job-log messages with severity 30 or higher and the tail of the `makei` output.

## Local outputs (git-ignored)

- `.bob/build/last-build.json` / `last-build.md`: the latest results.
- `.bob/build/state.json`: run id and attempt counter.
- `.bob/build/fix-log.md`: Bob's record of each fix.
- `.bob/build/remote/`: raw logs and event files from the last compile.
- `.bob/build/sync-manifest.json`: hashes of the files already on the IBM i.
