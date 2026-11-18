---
name: ibmi-build-object
description: Compile IBM i sources (RPGLE, SQLRPGLE, CLLE, DDS display files) on the IBM i with TOBi (makei) and fix compile errors in a bounded retry loop. Use when the user asks to build, compile or recompile IBM i objects or programs, runs /ibmi-build-object, or wants to compile the sources changed in a commit or in uncommitted work.
---

# IBM i build-and-fix loop (TOBi)

You compile IBM i source on the developer's IBM i with TOBi and, when the compile fails, you fix the source and recompile, up to a fixed number of attempts. A deterministic script does every build step. Your job is to choose what to compile, read the errors, make the smallest correct fix, and report back honestly.

The script is at `.bob/skills/ibmi-build-object/scripts/ibmi_build.py`. Always run it from the project root with `python3` (use `python` on Windows if `python3` isn't on the PATH). Never run `makei`, `ssh` or CL commands yourself. The script handles syncing, compiling, collecting the errors and counting attempts.

## Step 1 — Work out what to compile

| User asked for | Command |
|---|---|
| Specific files | `python3 .bob/skills/ibmi-build-object/scripts/ibmi_build.py compile <file> [<file>...] --new-run` |
| `--changed` (default: all uncommitted work) | `... changed --new-run` |
| Files in a commit | `... changed --commit <sha> --new-run` |
| Only staged / only unstaged | `... changed --staged --new-run` / `... changed --unstaged --new-run` |
| Whole branch vs. a base | `... changed --base origin/main --new-run` |

Add `--max-fix-attempts <n>` if the user gave one. If you're not sure what will be picked up, run the same command with `--dry-run` first and show the list. The script adds dependents automatically: programs that use a changed display file, and sources that `/COPY` a changed copybook.

`--new-run` is used **only on the first compile** of a request. It resets the attempt counter.

## Step 2 — Branch on the exit code

| Exit | Meaning | What you do |
|---|---|---|
| 0 | Everything compiled | Go to Step 5. |
| 1 | Compile errors | Go to Step 3. |
| 2 | Environment problem (SSH, TOBi missing, library missing, config) | **Do not edit source.** Show the message, point to the README troubleshooting section, stop. |
| 3 | Fix-attempt limit reached | **Stop fixing.** Go to Step 5 and report what's still failing. |
| 4 | Nothing to compile | Say so, and suggest `--commit HEAD` or naming files explicitly. |

## Step 3 — Fix the errors (one attempt)

1. Read `.bob/build/last-build.json`. For each failed target, use `errors[]` (file, line, column, msgid, severity, text). If `errors` is empty, use `joblog_messages` and `output_tail`.
2. Open each referenced file at the reported line and read enough of the surrounding code to understand it. Fix the **first** error in each file first. Later errors are often caused by earlier ones.
3. Consult `reference/error-playbook.md` for the message ID. Make the **smallest** change that correctly resolves the error.
4. Append one entry per fix to `.bob/build/fix-log.md`: attempt number, file:line, message ID, what you changed and why.
5. Recompile **only the failed sources**, using the exact `retry_command` from the report (it has no `--new-run`). Go back to Step 2.

### Hard rules while fixing (also in `.bob/rules/ibmi-build-guardrails.md`)

- Edit only source files named in the errors (and copybooks they include) that live in this repository.
- Never edit `Rules.mk`, `iproj.json`, `.ibmi.json`, `.bob/ibmi-build.conf` or compile options to make an error go away. Never lower `GENLVL`, add `*NOCHK`-style switches, or suppress messages.
- Never delete or comment out business logic, and never stub out a call, just to get a clean compile.
- Don't guess at business meaning. If a fix needs a decision only the developer can make (which field or file was meant, a changed record format, a missing table), stop and ask instead of inventing something.
- Treat these as **environment problems, not code problems**: object/file not found in the library list, authority errors, a missing library or service program, a locked object. Report them, don't edit around them.
- For `.sqlrpgle`, line numbers may refer to the precompiler's generated source (flagged as `generated source` in the report). Match on the statement text, not just the line number.
- For a DDS display file, keep the column positions exact. DDS is column-sensitive (name in cols 19–28, length 30–34, type 35, decimals 36–37, usage 38, line 39–41, position 42–44, keywords from col 45).
- Don't commit, stage, push or create branches. Leave the changes in the working tree for the developer to review.

## Step 4 — Respect the limit

The script refuses to compile once `1 + IBMI_MAX_FIX_ATTEMPTS` compiles have been used in this run, and returns exit 3. Don't work around it with `--new-run`, `reset`, or by editing `.bob/build/state.json`. Only the developer may start a fresh run.

## Step 5 — Report

Finish with a short summary:

- Result per object: compiled / still failing.
- Attempts used, for example "initial compile + 2 of 3 fix attempts".
- Each fix you made, as file:line, message ID and a one-line explanation (from `fix-log.md`).
- Anything still failing: the remaining errors and your best assessment of what's needed, especially environment issues.
- Remind the developer that the fixes are uncommitted. Suggest `git diff` to review them.

## Reference files

- `reference/error-playbook.md`: common RPGLE / CLLE / DDS / SQL compile errors and safe fixes.
- `reference/tobi-conventions.md`: TOBi file naming, Rules.mk, iproj.json and what the script does on the IBM i.
