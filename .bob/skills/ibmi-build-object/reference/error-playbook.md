# Compile error playbook

Use this when fixing errors reported by `ibmi_build.py`. Always confirm the cause by reading the code. The message ID is a starting point, not an answer.

Severity guide (ILE compilers): `00` informational, `10` warning, `20` error, `30` severe error, `40`+ unrecoverable. The object is not created when any message is above the compiler's `GENLVL` (default 10). The script reports `>= 20` as errors.

## RPGLE / SQLRPGLE (RNF messages)

| Symptom | Typical cause | Safe fix |
|---|---|---|
| **RNF7030**: name or indicator not defined | Typo in a variable, field or procedure name, or a missing declaration / `/COPY`. | Look for a near-identical defined name (for example `CUSTNAM` vs `CUSTNAME`) and fix the spelling. If it really is new, declare it with the type implied by its use. If it should come from a file or copybook, check the `dcl-f` or `/COPY`, but don't invent file fields. |
| **RNF7031**: name not referenced (sev 00) | Informational only. | Ignore. Don't delete declarations because of it. |
| Type mismatch in an expression or assignment | Assigning numeric to character (or the reverse), or comparing incompatible types. | Use `%char`, `%dec`, `%int` or `%editc` as the intent requires. Don't change a field's declared type if other code relies on it. |
| Missing `;`, unmatched `if/endif`, `dou/enddo`, `select/endsl`, `dcl-proc/end-proc` | Syntax error. These usually cascade into many later errors. | Fix the first one, then recompile before chasing the rest. |
| Prototype / parameter mismatch | Wrong number or type of parameters, or `const` vs. by-reference. | Match the call to the prototype (`dcl-pr` / `dcl-pi`). Change the prototype only if the program owns it and the change is clearly intended. |
| File or record format not found at compile time | The file object doesn't exist in the build library list, or it was never built. | **Cascade first:** if that file's own source (for example its `.dspf`) failed in the same build, this isn't an environment issue. Fix the file's source and recompile both. Otherwise it's an **environment issue.** Check that the `Rules.mk` prerequisite builds the file first. Don't remove the `dcl-f`. |
| `/COPY` member not found | Wrong path, or `includePath` doesn't cover the copybook folder. | Fix the path in the `/COPY` statement if it's clearly a typo. Otherwise report it. Don't edit `iproj.json` on your own. |

**SQL precompiler (SQLRPGLE):** messages such as **SQL0206** (column or variable not found) or **SQL0312** (host variable not defined or not usable). Check the spelling of the column or host variable, and that the host variable is declared before it's used. For a missing table or column that's spelled correctly, report it as an environment or database issue.

## CLLE (CPD / CPF messages)

| Symptom | Typical cause | Safe fix |
|---|---|---|
| **CPD0043**: keyword not valid for this command | A misspelled parameter keyword (for example `MSGTYP` instead of `MSGTYPE`). | Correct the keyword using the command's real parameter names. |
| **CPD0030**: command not found | A misspelled command, or a user command whose library isn't in the build library list. | Fix the spelling. If it's a real custom command, report it as an environment issue. |
| Variable not declared | `&VAR` used without `DCL`. | Add a `DCL` with the type and length implied by its use, or fix a typo in the variable name. |
| `MONMSG` in the wrong position | A global `MONMSG` must come right after the `DCL`s. | Move it. Don't delete it. |
| Continuation errors | A missing `+` at the end of a continued line. | Add the continuation character. |

## DDS display files (CPD7xxx / CPF7302)

- **CPF7302** (file not created) is a summary message. The real causes are the CPD7xxx messages before it in the report or job log.
- Most DDS errors are **column errors**. Check the field name (cols 19–28), length (30–34, right-aligned), data type (35), decimals (36–37), usage (38: B/I/O/H), line (39–41), position (42–44) and keywords (from 45).
- Fields that overlap other fields or constants on the same line are a common error. Move the field or shorten the constant. Don't drop fields that programs use.
- After a DSPF changes, every program using it must be recompiled. The script does this automatically from `Rules.mk`.

## When to stop and ask instead of fixing

- The fix needs a business decision (which field, which file, what default value).
- The error points at generated source or a member outside the repository.
- The same error is still there after your fix. Don't try a third variation of the same idea. Explain what you tried.
- Authority, lock, missing-object or missing-library messages (`CPF9801`, `CPF9802`, `CPF9810`, `CPF1002` and similar). These are environment problems.
