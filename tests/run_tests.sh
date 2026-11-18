#!/usr/bin/env bash
# Offline end-to-end tests for ibmi_build.py using a fake IBM i (tests/fake-ibmi).
# Usage: bash tests/run_tests.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
expect_rc() { [ "$1" = "$2" ] && ok "$3 (rc=$1)" || bad "$3 (expected rc=$2, got $1)"; }
expect_in() { grep -qF -- "$1" "$2" && ok "$3" || { bad "$3 - '$1' not found"; sed 's/^/      /' "$2"; }; }

# --- set up a throwaway clone of the project
mkdir -p "$WORK/proj" "$WORK/ifs"
( cd "$REPO" && tar --exclude=.git --exclude=.bob/build --exclude=.bob/ibmi-build.conf -cf - . ) | ( cd "$WORK/proj" && tar -xf - )
cd "$WORK/proj"
git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm "initial"
cat > .bob/ibmi-build.conf <<CONF
IBMI_HOST=fakehost
IBMI_USER=TESTER
IBMI_REMOTE_DIR=$WORK/ifs/proj
IBMI_OBJLIB=TESTLIB
IBMI_SSH_CMD=$REPO/tests/fake-ibmi/ssh
IBMI_MAKEI=$REPO/tests/fake-ibmi/makei
IBMI_REMOTE_SHELL=bash
IBMI_SETCCSID=
IBMI_MAX_FIX_ATTEMPTS=2
CONF
B="python3 .bob/skills/ibmi-build-object/scripts/ibmi_build.py"
OUT="$WORK/out.txt"

echo "1. check"
$B check > "$OUT" 2>&1; rc=$?
expect_rc $rc 2 "check flags missing objlib as an environment problem (fake system has no CHKOBJ)"
expect_in "makei: found" "$OUT" "makei detected"

echo "2. compile everything cleanly"
$B compile qddssrc/custdsp.dspf qrpglesrc/custinq.pgm.rpgle qclsrc/custmenu.pgm.clle --new-run > "$OUT" 2>&1; rc=$?
expect_rc $rc 0 "clean compile"
expect_in "[OK]     qddssrc/custdsp.dspf -> CUSTDSP.FILE" "$OUT" "DSPF built first"
expect_in "Synced" "$OUT" "files synced to fake IFS"
[ -f "$WORK/ifs/proj/qrpglesrc/custinq.pgm.rpgle" ] && ok "source present on fake IFS" || bad "source missing on fake IFS"

echo "3. changed: nothing uncommitted"
$B changed > "$OUT" 2>&1; rc=$?
expect_rc $rc 4 "nothing to compile"

echo "4. dependents"
echo "     A* touched" >> qddssrc/custdsp.dspf
$B changed --dry-run > "$OUT" 2>&1
expect_in "qrpglesrc/custinq.pgm.rpgle -> CUSTINQ.PGM  [uses CUSTDSP.FILE" "$OUT" "DSPF change pulls in CUSTINQ"
git checkout -q qddssrc/custdsp.dspf
echo "// touched" >> qrpgleref/custconst.rpgleinc
$B changed --dry-run > "$OUT" 2>&1
expect_in "[includes changed copybook qrpgleref/custconst.rpgleinc]" "$OUT" "copybook change pulls in CUSTINQ"
git checkout -q qrpgleref/custconst.rpgleinc

echo "5. break the build (demo patch) and compile changed sources"
git apply demo/break-build.patch && ok "demo patch applies" || bad "demo patch failed"
$B changed --new-run > "$OUT" 2>&1; rc=$?
expect_rc $rc 1 "compile errors detected"
expect_in "qrpglesrc/custinq.pgm.rpgle:44:7 RNF7030 sev30" "$OUT" "RPG error mapped to file:line:col"
expect_in "qclsrc/custmenu.pgm.clle:19:27 CPD0043 sev30" "$OUT" "CL error mapped to file:line:col"
expect_in "Fix attempts remaining: 2 of 2" "$OUT" "attempt counter shown"
python3 -c "import json;d=json.load(open('.bob/build/last-build.json'));assert d['summary']['failed']==2" && ok "JSON report written" || bad "JSON report"

echo "6. commit mode"
git -c user.email=t@t -c user.name=t commit -qam "broken" 
$B changed --commit HEAD --dry-run > "$OUT" 2>&1
expect_in "qclsrc/custmenu.pgm.clle -> CUSTMENU.PGM" "$OUT" "commit mode finds changed CL"

echo "7. fix one error, retry only failed sources"
sed -i "s/CUSTNAM = /CUSTNAME = /" qrpglesrc/custinq.pgm.rpgle
$B compile qrpglesrc/custinq.pgm.rpgle qclsrc/custmenu.pgm.clle > "$OUT" 2>&1; rc=$?
expect_rc $rc 1 "still failing (CL not fixed yet)"
expect_in "[OK]     qrpglesrc/custinq.pgm.rpgle" "$OUT" "RPG fixed"
expect_in "fix attempt 1 of 2" "$OUT" "fix attempt 1 counted"

echo "8. attempt limit enforced"
$B compile qclsrc/custmenu.pgm.clle > "$OUT" 2>&1; rc=$?
expect_rc $rc 3 "last allowed attempt returns 3"
$B compile qclsrc/custmenu.pgm.clle > "$OUT" 2>&1; rc=$?
expect_rc $rc 3 "further compiles refused"
expect_in "MAX FIX ATTEMPTS REACHED" "$OUT" "refusal message"

echo "9. new run after human review"
sed -i "s/MSGTYP(\*ESCAPE)/MSGTYPE(*ESCAPE)/" qclsrc/custmenu.pgm.clle
$B compile qclsrc/custmenu.pgm.clle --new-run > "$OUT" 2>&1; rc=$?
expect_rc $rc 0 "fixed build compiles"

echo "10. environment error"
IBMI_REMOTE_DIR=/ $B compile qclsrc/custmenu.pgm.clle --new-run > "$OUT" 2>&1; rc=$?
expect_rc $rc 2 "bad config returns 2"
expect_in "Do not edit source" "$OUT" "env error says not a code problem"

echo
echo "Passed: $PASS  Failed: $FAIL"
[ $FAIL -eq 0 ]
