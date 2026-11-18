# IBM i build guardrails

These rules always apply in this repository, whether or not a skill is active.

1. **Build through the script only.** Compile IBM i objects with `python3 .bob/skills/ibmi-build-object/scripts/ibmi_build.py` (or `/ibmi-build-object`). Don't run `makei`, `ssh`, `system` or CL commands against the IBM i directly.
2. **Bounded retries.** When fixing compile errors, stop when the script reports the fix-attempt limit (exit code 3). Never use `--new-run`, `reset`, or edit `.bob/build/state.json` to get more attempts. Only the developer may start a new run.
3. **Fix code, not configuration.** Never change `Rules.mk`, `iproj.json`, `.ibmi.json`, `.bob/ibmi-build.conf`, control options or compile options to suppress or hide errors.
4. **No destructive "fixes".** Never delete, comment out or stub business logic to get a clean compile.
5. **Environment problems aren't code problems.** For exit code 2, or for missing objects, libraries, authority or locks, report the issue and stop.
6. **Ask before guessing.** If a fix needs business knowledge, such as which field, file or default value was intended, ask the developer.
7. **Leave changes for review.** Never commit, stage, push or create branches as part of a build. Log every fix in `.bob/build/fix-log.md`.
8. **DDS is column-sensitive.** When editing `.dspf`, `.prtf`, `.pf` or `.lf` source, keep every entry in its exact column positions.
