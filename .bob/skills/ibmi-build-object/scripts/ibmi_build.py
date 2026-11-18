#!/usr/bin/env python3
"""
ibmi_build.py - deterministic compile driver for IBM Bob + TOBi (The Object Builder for i)

This script is the "hands" of the /ibmi-build-object slash command. It does the
parts that must be repeatable and auditable, so that Bob (the AI) only does the
part that needs judgement: reading compiler errors and fixing source.

What it does on every run:
  1. Works out WHICH sources to compile (explicit files, or files changed in a
     commit / range / branch / staged / unstaged work).
  2. Adds dependents that must be rebuilt (programs that use a changed display
     file; sources that /COPY a changed copybook).
  3. Syncs only changed files to the IBM i IFS build directory over SSH.
  4. Runs TOBi `makei compile -f <file>` for each target on the IBM i.
  5. Pulls back the compiler event files (.evfevent), job logs and output, and
     turns them into a compact error list:  file:line:col MSGID sev text
  6. Enforces the pre-set number of fix attempts (IBMI_MAX_FIX_ATTEMPTS).

Exit codes (Bob's skill branches on these):
  0  all targets compiled
  1  one or more targets failed to compile  -> Bob may fix and retry
  2  configuration / connection / environment problem -> do NOT edit source
  3  maximum fix attempts reached -> stop and report
  4  nothing to compile

Only the Python 3 standard library is used. Works on macOS, Linux, WSL and
Windows (with the built-in OpenSSH client).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import uuid

EXIT_OK = 0
EXIT_COMPILE_FAILED = 1
EXIT_ENV = 2
EXIT_MAX_ATTEMPTS = 3
EXIT_NOTHING = 4

CONF_REL = os.path.join(".bob", "ibmi-build.conf")
BUILD_DIR_REL = os.path.join(".bob", "build")
REMOTE_WORK = ".bob-remote"  # scratch folder inside the IFS project dir

DEFAULTS = {
    "IBMI_HOST": "",
    "IBMI_USER": "",
    "IBMI_SSH_PORT": "",
    "IBMI_SSH_KEY": "",
    "IBMI_SSH_CMD": "ssh",
    "IBMI_SSH_OPTS": "-o BatchMode=yes -o ConnectTimeout=20",
    "IBMI_REMOTE_DIR": "",
    "IBMI_REMOTE_SHELL": "/QOpenSys/pkgs/bin/bash",
    "IBMI_MAKEI": "/QOpenSys/pkgs/bin/makei",
    "IBMI_OBJLIB": "",
    "IBMI_SETCCSID": "1208",
    "IBMI_MAX_FIX_ATTEMPTS": "3",
    "IBMI_SOURCE_EXTS": ".rpgle,.sqlrpgle,.clle,.dspf",
    "IBMI_INCLUDE_EXTS": ".rpgleinc,.sqlrpgleinc",
    "IBMI_FILE_OBJECT_EXTS": ".dspf,.prtf,.pf,.lf",
    "IBMI_WARNING_SEVERITY": "10",
    "IBMI_ERROR_SEVERITY": "20",
    "IBMI_SSH_TIMEOUT": "1800",
}

# Build order: file objects first, then RPG, then CL.
ORDER = {".dspf": 0, ".prtf": 0, ".pf": 0, ".lf": 0, ".rpgle": 1, ".sqlrpgle": 1, ".clle": 2}


# --------------------------------------------------------------------------- utils

class EnvError(Exception):
    """Configuration, connection or environment problem (exit 2)."""


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd, cwd=None, input_bytes=None, timeout=None, check=False):
    return subprocess.run(cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=check)


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def split_list(value: str):
    return [v.strip().lower() for v in value.split(",") if v.strip()]


def find_root(start: str) -> str:
    try:
        out = run(["git", "rev-parse", "--show-toplevel"], cwd=start)
        if out.returncode == 0:
            return out.stdout.decode().strip()
    except FileNotFoundError:
        pass
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "iproj.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise EnvError("Could not find the project root (no git repo and no iproj.json found).")
        d = parent


def load_config(root: str) -> dict:
    cfg = dict(DEFAULTS)
    path = os.path.join(root, CONF_REL)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                cfg[k] = v
    for k in list(cfg):  # environment wins (CI pipelines, one-off overrides)
        if k in os.environ:
            cfg[k] = os.environ[k]
    cfg["_conf_path"] = path
    cfg["_conf_exists"] = os.path.exists(path)
    return cfg


def validate_config(cfg: dict, need_remote=True):
    problems = []
    if need_remote:
        if not cfg["_conf_exists"] and not os.environ.get("IBMI_HOST"):
            problems.append(f"Missing {CONF_REL}. Copy .bob/ibmi-build.conf.example to {CONF_REL} and edit it.")
        for key in ("IBMI_HOST", "IBMI_USER", "IBMI_REMOTE_DIR", "IBMI_OBJLIB"):
            if not cfg.get(key):
                problems.append(f"{key} is not set.")
        rd = cfg.get("IBMI_REMOTE_DIR", "")
        if rd and (not rd.startswith("/") or rd.rstrip("/") in ("", "/home", "/QOpenSys", "/tmp")
                   or rd.upper().startswith("/QSYS.LIB")):
            problems.append("IBMI_REMOTE_DIR must be a dedicated absolute IFS folder such as /home/DEVUSER/builds/myproject.")
        lib = cfg.get("IBMI_OBJLIB", "")
        if lib and not re.fullmatch(r"[A-Za-z$#@][A-Za-z0-9_$#@.]{0,9}", lib):
            problems.append("IBMI_OBJLIB must be a valid library name (max 10 characters).")
    try:
        int(cfg["IBMI_MAX_FIX_ATTEMPTS"])
    except ValueError:
        problems.append("IBMI_MAX_FIX_ATTEMPTS must be a number.")
    if problems:
        raise EnvError("Configuration problem:\n  - " + "\n  - ".join(problems))


# --------------------------------------------------------------------------- ssh

def ssh_base(cfg: dict):
    cmd = shlex.split(cfg["IBMI_SSH_CMD"]) + shlex.split(cfg.get("IBMI_SSH_OPTS", ""))
    if cfg.get("IBMI_SSH_PORT"):
        cmd += ["-p", cfg["IBMI_SSH_PORT"]]
    if cfg.get("IBMI_SSH_KEY"):
        cmd += ["-i", os.path.expanduser(cfg["IBMI_SSH_KEY"])]
    cmd.append(f'{cfg["IBMI_USER"]}@{cfg["IBMI_HOST"]}')
    return cmd


def ssh(cfg, remote_cmd: str, input_bytes=None, timeout=None):
    timeout = timeout or int(cfg["IBMI_SSH_TIMEOUT"])
    try:
        return run(ssh_base(cfg) + [remote_cmd], input_bytes=input_bytes, timeout=timeout)
    except FileNotFoundError:
        raise EnvError(f'SSH client not found: {cfg["IBMI_SSH_CMD"]}')
    except subprocess.TimeoutExpired:
        raise EnvError(f"SSH command timed out after {timeout}s.")


def ssh_failed(res) -> bool:
    # OpenSSH returns 255 for connection/auth failures.
    return res.returncode == 255


# --------------------------------------------------------------------------- git / project scan

def git_lines(root, args):
    res = run(["git"] + args, cwd=root)
    if res.returncode != 0:
        raise EnvError(f"git {' '.join(args)} failed: {res.stderr.decode(errors='replace').strip()}")
    return [l for l in res.stdout.decode(errors="replace").splitlines() if l.strip()]


def project_files(root):
    """All files git would track (tracked + untracked-but-not-ignored)."""
    try:
        files = git_lines(root, ["ls-files", "--cached", "--others", "--exclude-standard"])
    except EnvError:
        files = []
        for d, dirs, names in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for n in names:
                files.append(os.path.relpath(os.path.join(d, n), root))
    out = []
    for f in sorted(set(files)):
        f = f.replace("\\", "/")
        if f.startswith(".bob/") or f.startswith(".git/") or f.startswith(REMOTE_WORK + "/"):
            continue
        if os.path.isfile(os.path.join(root, f)):
            out.append(f)
    return out


def changed_files(root, args):
    filt = "--diff-filter=ACMR"
    if args.commit:
        sha = args.commit
        parents = git_lines(root, ["rev-list", "--parents", "-n", "1", sha])[0].split()
        if len(parents) == 1:  # root commit
            files = git_lines(root, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", filt, sha])
        else:
            files = git_lines(root, ["diff-tree", "--no-commit-id", "--name-only", "-r", filt, sha])
        head = git_lines(root, ["rev-parse", "HEAD"])[0]
        full = git_lines(root, ["rev-parse", sha])[0]
        note = None if head == full else (
            f"Note: {sha} is not HEAD. The files changed in that commit are compiled from your "
            "CURRENT working tree. Check the commit out first if you need its exact content.")
        return files, f"commit {sha}", note
    if args.range:
        if ".." not in args.range:
            raise EnvError("--range expects A..B")
        a, b = args.range.split("..", 1)
        return git_lines(root, ["diff", "--name-only", filt, a, b or "HEAD"]), f"range {args.range}", None
    if args.base:
        return git_lines(root, ["diff", "--name-only", filt, f"{args.base}...HEAD"]) + \
            _working(root), f"branch changes since {args.base} (plus uncommitted work)", None
    if args.staged:
        return git_lines(root, ["diff", "--cached", "--name-only", filt]), "staged changes", None
    if args.unstaged:
        return git_lines(root, ["diff", "--name-only", filt]) + \
            git_lines(root, ["ls-files", "--others", "--exclude-standard"]), "unstaged + untracked changes", None
    return _working(root), "uncommitted changes (staged + unstaged + untracked)", None


def _working(root):
    filt = "--diff-filter=ACMR"
    return (git_lines(root, ["diff", "--cached", "--name-only", filt])
            + git_lines(root, ["diff", "--name-only", filt])
            + git_lines(root, ["ls-files", "--others", "--exclude-standard"]))


RULE_RE = re.compile(r"^([A-Za-z0-9_$#@][A-Za-z0-9_$#@.]*)\s*:(?!=)\s*(.*)$")


def rules_index(root, files):
    """Parse every Rules.mk into {TARGET: {dir, prereqs, source}} and {source: TARGET}."""
    targets, by_source = {}, {}
    for rel in files:
        if os.path.basename(rel) != "Rules.mk":
            continue
        d = os.path.dirname(rel)
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\\\n", " ")
        for line in text.splitlines():
            if line.startswith("\t") or line.lstrip().startswith("#"):
                continue
            m = RULE_RE.match(line.strip())
            if not m:
                continue
            target, rest = m.group(1), m.group(2)
            if "=" in rest:  # target-specific variable, e.g. CUSTINQ.PGM: TEXT = ...
                continue
            prereqs = rest.split()
            source = None
            for p in prereqs:
                cand = f"{d}/{p}" if d else p
                if os.path.isfile(os.path.join(root, cand)):
                    source = cand.replace("\\", "/")
                    break
            entry = targets.setdefault(target.upper(), {"dir": d, "prereqs": [], "source": None})
            entry["prereqs"] += [p.upper() for p in prereqs]
            if source:
                entry["source"] = source
                by_source[source] = target.upper()
    return targets, by_source


def find_copy_dependents(root, files, include_rel, source_exts):
    """Sources that /COPY or /INCLUDE the given copybook (by file name or member name)."""
    base = os.path.basename(include_rel).lower()
    stem = base.split(".")[0]
    directive = re.compile(r"/(copy|include)\b(.*)", re.I)
    deps = []
    for rel in files:
        if ext_of(rel) not in (".rpgle", ".sqlrpgle") or ext_of(rel) not in source_exts:
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = directive.search(line)
                    if not m:
                        continue
                    arg = m.group(2).lower()
                    if base in arg or re.search(rf"[,/'\s]{re.escape(stem)}\b", " " + arg):
                        deps.append(rel)
                        break
        except OSError:
            pass
    return deps


def resolve_targets(root, cfg, requested, reason_default):
    """Normalise requested files, filter to compilable, add dependents, order them."""
    files = project_files(root)
    source_exts = split_list(cfg["IBMI_SOURCE_EXTS"])
    include_exts = split_list(cfg["IBMI_INCLUDE_EXTS"])
    file_exts = split_list(cfg["IBMI_FILE_OBJECT_EXTS"])
    targets, by_source = rules_index(root, files)

    chosen, notes, skipped = {}, [], []

    def add(rel, why):
        if rel not in chosen:
            chosen[rel] = why

    for raw in requested:
        if os.path.exists(os.path.join(root, raw)):
            rel = os.path.normpath(raw)
        else:  # path given relative to the current directory
            rel = os.path.relpath(os.path.abspath(raw), root)
        rel = rel.replace("\\", "/")
        if not os.path.isfile(os.path.join(root, rel)):
            skipped.append(f"{raw} (not found / deleted)")
            continue
        e = ext_of(rel)
        if e in include_exts:
            deps = find_copy_dependents(root, files, rel, source_exts)
            if deps:
                for d in deps:
                    add(d, f"includes changed copybook {rel}")
            else:
                notes.append(f"Copybook {rel} changed but no source includes it.")
            continue
        if e not in source_exts:
            skipped.append(f"{rel} (not a compilable source type)")
            continue
        add(rel, reason_default)
        if e in file_exts and rel in by_source:
            obj = by_source[rel]
            for t, info in targets.items():
                if obj in info["prereqs"] and info["source"] and info["source"] != rel:
                    add(info["source"], f"uses {obj} (recompile to avoid level checks)")

    ordered = sorted(chosen, key=lambda r: (ORDER.get(ext_of(r), 5), r))
    result = []
    for rel in ordered:
        obj = by_source.get(rel)
        if not obj:
            notes.append(f"{rel} is not referenced by any Rules.mk - TOBi may not know how to build it.")
        result.append({"source": rel, "object": obj, "reason": chosen[rel]})
    return result, notes, skipped


# --------------------------------------------------------------------------- state (attempt limit)

def build_dir(root):
    p = os.path.join(root, BUILD_DIR_REL)
    os.makedirs(p, exist_ok=True)
    return p


def load_state(root):
    p = os.path.join(build_dir(root), "state.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return None


def save_state(root, state):
    with open(os.path.join(build_dir(root), "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def new_state(max_fix):
    return {"run_id": uuid.uuid4().hex[:8], "started": now_iso(), "compiles": 0,
            "max_fix_attempts": max_fix, "complete": False, "history": []}


def begin_attempt(root, cfg, args):
    max_fix = args.max_fix_attempts if args.max_fix_attempts is not None else int(cfg["IBMI_MAX_FIX_ATTEMPTS"])
    state = load_state(root)
    stale = False
    if state:
        try:
            started = _dt.datetime.fromisoformat(state["started"])
            stale = (_dt.datetime.now().astimezone() - started).total_seconds() > 12 * 3600
        except (KeyError, ValueError):
            stale = True
    if args.new_run or not state or state.get("complete") or stale:
        state = new_state(max_fix)
    elif args.max_fix_attempts is not None:
        state["max_fix_attempts"] = max_fix
    allowed = 1 + int(state["max_fix_attempts"])
    if state["compiles"] >= allowed:
        return state, False
    state["compiles"] += 1
    save_state(root, state)
    return state, True


def attempt_label(state):
    n = state["compiles"]
    m = state["max_fix_attempts"]
    return "initial compile" if n == 1 else f"fix attempt {n - 1} of {m}"


# --------------------------------------------------------------------------- sync

def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sync(root, cfg, full=False):
    files = project_files(root)
    man_path = os.path.join(build_dir(root), "sync-manifest.json")
    key = f'{cfg["IBMI_USER"]}@{cfg["IBMI_HOST"]}:{cfg["IBMI_REMOTE_DIR"]}'
    manifest = {}
    if os.path.exists(man_path) and not full:
        try:
            with open(man_path, encoding="utf-8") as fh:
                manifest = json.load(fh).get(key, {})
        except (OSError, ValueError):
            manifest = {}
    hashes = {f: sha1(os.path.join(root, f)) for f in files}
    to_send = [f for f in files if manifest.get(f) != hashes[f]]
    if not to_send:
        return 0

    buf = io.BytesIO()
    now = int(_dt.datetime.now().timestamp())
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for f in to_send:
            ti = tar.gettarinfo(os.path.join(root, f), arcname=f)
            ti.mtime = now  # make sure make/TOBi sees the file as newer than the object
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode = 0o644
            with open(os.path.join(root, f), "rb") as fh:
                tar.addfile(ti, fh)
        listing = ("\n".join(to_send) + "\n").encode()
        ti = tarfile.TarInfo(f"{REMOTE_WORK}/synced-files.txt")
        ti.size, ti.mtime, ti.mode = len(listing), now, 0o644
        tar.addfile(ti, io.BytesIO(listing))

    rd = shlex.quote(cfg["IBMI_REMOTE_DIR"])
    remote = f"mkdir -p {rd} && cd {rd} && tar -xf -"
    if cfg.get("IBMI_SETCCSID"):
        ccsid = shlex.quote(cfg["IBMI_SETCCSID"])
        remote += (f" && while IFS= read -r f; do setccsid {ccsid} \"$f\" 2>/dev/null; done "
                   f"< {REMOTE_WORK}/synced-files.txt; true")
    res = ssh(cfg, remote, input_bytes=buf.getvalue())
    if res.returncode != 0:
        raise EnvError("Upload to IBM i failed (rc={}):\n{}".format(
            res.returncode, res.stderr.decode(errors="replace").strip()))

    allm = {}
    if os.path.exists(man_path):
        try:
            with open(man_path, encoding="utf-8") as fh:
                allm = json.load(fh)
        except (OSError, ValueError):
            allm = {}
    allm[key] = hashes if full else {**manifest, **{f: hashes[f] for f in to_send}}
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(allm, fh, indent=1)
    return len(to_send)


# --------------------------------------------------------------------------- remote compile

def remote_script(cfg, targets):
    q = shlex.quote
    lib = cfg["IBMI_OBJLIB"].upper()
    lines = [
        "set -u",
        "export PATH=/QOpenSys/pkgs/bin:$PATH",
        f"cd {q(cfg['IBMI_REMOTE_DIR'])} || {{ echo '@@BOB_FATAL cannot cd to remote dir' >&2; exit 90; }}",
        f"[ -x {q(cfg['IBMI_MAKEI'])} ] || {{ echo '@@BOB_FATAL makei not found at {cfg['IBMI_MAKEI']}' >&2; exit 91; }}",
        # TOBi resolves &OBJLIB (etc.) in iproj.json from environment variables.
        f"export OBJLIB={q(lib)} objlib={q(lib)} CURLIB={q(lib)} curlib={q(lib)}",
        f"rm -rf .evfevent {REMOTE_WORK}/logs",
        f"mkdir -p {REMOTE_WORK}/logs",
    ]
    for i, t in enumerate(targets, 1):
        src = q(t["source"])
        d = f"{REMOTE_WORK}/logs/{i}"
        lines += [
            f"echo '@@BOB_BEGIN {i}' >&2",
            f"{q(cfg['IBMI_MAKEI'])} compile -f {src} > {d}.out 2>&1",
            "rc=$?",
            f"[ -f .logs/joblog.json ] && cp .logs/joblog.json {d}.joblog.json",
            f"[ -f .logs/output.log ] && cp .logs/output.log {d}.output.log",
            f"if [ -d .evfevent ]; then mkdir -p {d}.evf; mv .evfevent/* {d}.evf/ 2>/dev/null; fi",
            f"echo \"@@BOB_END {i} $rc\" >&2",
        ]
    lines.append(f"tar -cf - {REMOTE_WORK}/logs")
    return "\n".join(lines) + "\n"


def safe_extract(data: bytes, dest: str):
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for m in tar.getmembers():
            name = m.name
            if name.startswith("/") or ".." in name.split("/") or not (m.isfile() or m.isdir()):
                continue
            target = os.path.join(dest, *name.split("/"))
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            with open(target, "wb") as out:
                out.write(src.read())


def remote_compile(root, cfg, targets):
    script = remote_script(cfg, targets).encode()
    res = ssh(cfg, f"{shlex.quote(cfg['IBMI_REMOTE_SHELL'])} -s", input_bytes=script)
    err = res.stderr.decode(errors="replace")
    if ssh_failed(res):
        raise EnvError("SSH connection to IBM i failed:\n" + err.strip())
    fatal = re.search(r"@@BOB_FATAL (.*)", err)
    if fatal:
        raise EnvError("Remote build could not start: " + fatal.group(1))
    rcs = {int(m.group(1)): int(m.group(2)) for m in re.finditer(r"@@BOB_END (\d+) (\d+)", err)}
    local_remote = os.path.join(build_dir(root), "remote")
    if os.path.isdir(local_remote):
        import shutil
        shutil.rmtree(local_remote)
    if res.stdout:
        try:
            safe_extract(res.stdout, local_remote)
        except tarfile.TarError as exc:
            raise EnvError(f"Could not read build results returned by IBM i: {exc}\n{err.strip()}")
    return rcs, os.path.join(local_remote, REMOTE_WORK, "logs"), err


# --------------------------------------------------------------------------- parsing results

def parse_evfevent(path, cfg, root, project_set):
    """Parse an EVFEVENT file into a list of message dicts."""
    rd = cfg["IBMI_REMOTE_DIR"].rstrip("/") + "/"
    files, stack, msgs = {}, [], []
    basenames = {}
    for p in project_set:
        basenames.setdefault(os.path.basename(p).lower(), []).append(p)

    def to_rel(name):
        n = name.strip()
        if n.startswith(rd):
            return n[len(rd):], False
        cands = basenames.get(os.path.basename(n).lower(), [])
        if len(cands) == 1:
            return cands[0], False
        return n, True  # e.g. a QTEMP member generated by the SQL precompiler

    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\r\n").split()
            if not parts:
                continue
            rec = parts[0]
            if rec == "FILEID" and len(parts) >= 6:
                fid = parts[2]
                files[fid] = to_rel(" ".join(parts[5:-2]) if len(parts) > 7 else parts[5])
            elif rec == "ERROR" and len(parts) >= 13:
                fid = parts[2]
                try:
                    line = int(parts[5])
                    col = int(parts[6])
                    sev = int(parts[11])
                except ValueError:
                    continue
                text = " ".join(parts[13:])
                fname, external = files.get(fid, ("<unknown>", True))
                msgs.append({"file": fname, "line": line, "column": col, "msgid": parts[9],
                             "severity": sev, "text": text, "generated_source": external})
    return msgs


def walk_json_messages(obj, out):
    if isinstance(obj, dict):
        keys = {k.lower(): k for k in obj}
        mid = next((obj[keys[k]] for k in ("msgid", "message_id", "msg_id", "id") if k in keys), None)
        sev = next((obj[keys[k]] for k in ("severity", "sev", "message_severity") if k in keys), None)
        txt = next((obj[keys[k]] for k in ("text", "message", "message_text", "msgtext", "first_level")
                    if k in keys), None)
        if mid and isinstance(mid, str) and re.fullmatch(r"[A-Z]{3}[0-9A-F]{4}", mid.strip()):
            try:
                s = int(sev) if sev is not None else 0
            except (TypeError, ValueError):
                s = 0
            out.append({"msgid": mid.strip(), "severity": s, "text": str(txt or "").strip()})
        for v in obj.values():
            walk_json_messages(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_json_messages(v, out)


def parse_joblog(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    walk_json_messages(data, out)
    seen, uniq = set(), []
    for m in out:
        k = (m["msgid"], m["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(m)
    return uniq


def tail(path, n=40):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:]).rstrip()
    except OSError:
        return ""


def collect(root, cfg, targets, rcs, logs_dir):
    err_sev = int(cfg["IBMI_ERROR_SEVERITY"])
    warn_sev = int(cfg["IBMI_WARNING_SEVERITY"])
    pset = set(project_files(root))
    for i, t in enumerate(targets, 1):
        rc = rcs.get(i)
        msgs = []
        evf_dir = os.path.join(logs_dir, f"{i}.evf")
        if os.path.isdir(evf_dir):
            for name in sorted(os.listdir(evf_dir)):
                msgs += parse_evfevent(os.path.join(evf_dir, name), cfg, root, pset)
        errors = [m for m in msgs if m["severity"] >= err_sev]
        warnings = [m for m in msgs if warn_sev <= m["severity"] < err_sev]
        joblog = [m for m in parse_joblog(os.path.join(logs_dir, f"{i}.joblog.json")) if m["severity"] >= 30]
        t["rc"] = rc
        t["status"] = "success" if rc == 0 and not errors else "failed"
        if rc is None:
            t["status"] = "failed"
            t["note"] = "No result returned for this target (remote script stopped early)."
        t["errors"] = errors
        t["warning_count"] = len(warnings)
        t["joblog_messages"] = joblog if t["status"] == "failed" else []
        t["output_tail"] = tail(os.path.join(logs_dir, f"{i}.out")) if t["status"] == "failed" else ""
    return targets


# --------------------------------------------------------------------------- reporting

def script_cmd():
    rel = os.path.relpath(os.path.abspath(__file__), os.getcwd()).replace("\\", "/")
    return f"python3 {rel}"


def write_reports(root, cfg, state, targets, scope, notes, skipped):
    failed = [t for t in targets if t["status"] == "failed"]
    report = {
        "timestamp": now_iso(), "run_id": state["run_id"], "compile_number": state["compiles"],
        "attempt": attempt_label(state), "max_fix_attempts": state["max_fix_attempts"],
        "fix_attempts_remaining": max(0, 1 + state["max_fix_attempts"] - state["compiles"]),
        "host": cfg["IBMI_HOST"], "objlib": cfg["IBMI_OBJLIB"].upper(), "scope": scope,
        "notes": notes, "skipped": skipped,
        "summary": {"targets": len(targets), "succeeded": len(targets) - len(failed), "failed": len(failed)},
        "targets": targets,
        "retry_command": (f"{script_cmd()} compile " + " ".join(shlex.quote(t["source"]) for t in failed))
        if failed else None,
    }
    bd = build_dir(root)
    with open(os.path.join(bd, "last-build.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    out = []
    out.append(f"TOBi build | {report['attempt']} | objlib {report['objlib']} | host {report['host']}")
    out.append(f"Scope: {scope}")
    for t in targets:
        tag = "[OK]    " if t["status"] == "success" else "[FAILED]"
        obj = f" -> {t['object']}" if t.get("object") else ""
        extra = ""
        if t["status"] == "failed":
            extra = f"  (rc={t['rc']}, {len(t['errors'])} error(s))"
        elif t.get("warning_count"):
            extra = f"  ({t['warning_count']} warning(s))"
        why = "" if t["reason"] in ("requested", "changed") else f"  [{t['reason']}]"
        out.append(f"  {tag} {t['source']}{obj}{extra}{why}")
        for e in t["errors"][:25]:
            gen = "  (line refers to generated source)" if e["generated_source"] else ""
            out.append(f"      {e['file']}:{e['line']}:{e['column']} {e['msgid']} sev{e['severity']:02d} {e['text']}{gen}")
        if len(t["errors"]) > 25:
            out.append(f"      ... {len(t['errors']) - 25} more (see .bob/build/last-build.json)")
        if t["status"] == "failed" and not t["errors"]:
            for m in t["joblog_messages"][:10]:
                out.append(f"      joblog {m['msgid']} sev{m['severity']:02d} {m['text']}")
            if t["output_tail"]:
                out.append("      --- makei output (tail) ---")
                out += ["      " + l for l in t["output_tail"].splitlines()[-20:]]
    for n in notes:
        out.append(f"Note: {n}")
    for s in skipped:
        out.append(f"Skipped: {s}")
    if failed:
        out.append(f"Fix attempts remaining: {report['fix_attempts_remaining']} of {state['max_fix_attempts']}")
        out.append(f"Retry: {report['retry_command']}")
    else:
        out.append("All targets compiled successfully.")
    text = "\n".join(out)
    with open(os.path.join(bd, "last-build.md"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return report, text


# --------------------------------------------------------------------------- commands

def cmd_check(root, cfg, args):
    validate_config(cfg)
    print(f"Project root : {root}")
    print(f"Config       : {cfg['_conf_path']}")
    print(f"Target       : {cfg['IBMI_USER']}@{cfg['IBMI_HOST']}:{cfg['IBMI_REMOTE_DIR']}  objlib={cfg['IBMI_OBJLIB'].upper()}")
    q = shlex.quote
    script = "\n".join([
        "export PATH=/QOpenSys/pkgs/bin:$PATH",
        "echo \"shell: $0\"",
        f"if [ -x {q(cfg['IBMI_MAKEI'])} ]; then echo 'makei: found'; {q(cfg['IBMI_MAKEI'])} -v 2>&1 | head -3; "
        f"else echo 'makei: NOT FOUND at {cfg['IBMI_MAKEI']}'; fi",
        f"mkdir -p {q(cfg['IBMI_REMOTE_DIR'])} && touch {q(cfg['IBMI_REMOTE_DIR'])}/.bob-write-test "
        f"&& rm -f {q(cfg['IBMI_REMOTE_DIR'])}/.bob-write-test && echo 'remote dir: writable' || echo 'remote dir: NOT writable'",
        f"if system -q \"CHKOBJ OBJ(QSYS/{cfg['IBMI_OBJLIB'].upper()}) OBJTYPE(*LIB)\" >/dev/null 2>&1; "
        f"then echo 'objlib: exists'; else echo 'objlib: NOT FOUND (CRTLIB {cfg['IBMI_OBJLIB'].upper()})'; fi",
        "command -v setccsid >/dev/null 2>&1 && echo 'setccsid: found' || echo 'setccsid: not found (set IBMI_SETCCSID= to skip)'",
    ]) + "\n"
    res = ssh(cfg, f"{q(cfg['IBMI_REMOTE_SHELL'])} -s", input_bytes=script.encode())
    if res.returncode != 0 and not res.stdout:
        raise EnvError("SSH check failed:\n" + res.stderr.decode(errors="replace").strip())
    body = res.stdout.decode(errors="replace").rstrip()
    print(body)
    bad = any(w in body for w in ("NOT FOUND", "NOT writable"))
    print("Result       : " + ("PROBLEMS FOUND - fix the items above" if bad else "ready"))
    return EXIT_ENV if bad else EXIT_OK


def do_build(root, cfg, args, requested, scope, reason, extra_notes=None):
    targets, notes, skipped = resolve_targets(root, cfg, requested, reason)
    notes = (extra_notes or []) + notes
    if getattr(args, "dry_run", False):
        print(f"Scope: {scope}")
        if not targets:
            print("Nothing to compile.")
        for t in targets:
            obj = f" -> {t['object']}" if t.get("object") else ""
            print(f"  {t['source']}{obj}  [{t['reason']}]")
        for n in notes:
            print(f"Note: {n}")
        for s in skipped:
            print(f"Skipped: {s}")
        return EXIT_OK if targets else EXIT_NOTHING
    if not targets:
        print(f"Scope: {scope}\nNothing to compile.")
        for s in skipped:
            print(f"Skipped: {s}")
        for n in notes:
            print(f"Note: {n}")
        return EXIT_NOTHING

    validate_config(cfg)
    state, ok = begin_attempt(root, cfg, args)
    if not ok:
        print(f"MAX FIX ATTEMPTS REACHED ({state['max_fix_attempts']}) for run {state['run_id']}. "
              "No compile was performed.")
        print("Last results: .bob/build/last-build.md")
        print("Start a fresh run explicitly with --new-run once a human has reviewed the situation.")
        return EXIT_MAX_ATTEMPTS

    sent = sync(root, cfg, full=args.full_sync)
    rcs, logs_dir, _ = remote_compile(root, cfg, targets)
    collect(root, cfg, targets, rcs, logs_dir)
    if sent:
        notes.insert(0, f"Synced {sent} changed file(s) to {cfg['IBMI_REMOTE_DIR']}.")
    report, text = write_reports(root, cfg, state, targets, scope, notes, skipped)

    failed = report["summary"]["failed"]
    state["history"].append({"compile": state["compiles"], "time": report["timestamp"],
                             "targets": [t["source"] for t in targets],
                             "failed": [t["source"] for t in targets if t["status"] == "failed"],
                             "error_count": sum(len(t["errors"]) for t in targets)})
    if not failed:
        state["complete"] = True
    save_state(root, state)

    print(json.dumps(report, indent=2) if args.json else text)
    if failed and report["fix_attempts_remaining"] == 0:
        print("This was the last allowed attempt. Stop and report to the developer.")
        return EXIT_MAX_ATTEMPTS
    return EXIT_COMPILE_FAILED if failed else EXIT_OK


def cmd_compile(root, cfg, args):
    if not args.files:
        raise EnvError("compile needs at least one source file.")
    return do_build(root, cfg, args, args.files, "explicit file list", "requested")


def cmd_changed(root, cfg, args):
    files, scope, note = changed_files(root, args)
    seen, uniq = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return do_build(root, cfg, args, uniq, scope, "changed", [note] if note else [])


def cmd_status(root, cfg, args):
    state = load_state(root)
    if not state:
        print("No build run recorded yet.")
        return EXIT_OK
    allowed = 1 + int(state["max_fix_attempts"])
    print(f"Run {state['run_id']} started {state['started']}  complete={state['complete']}")
    print(f"Compiles used: {state['compiles']} of {allowed} (1 initial + {state['max_fix_attempts']} fix attempts)")
    for h in state["history"]:
        print(f"  #{h['compile']} {h['time']}  failed={len(h['failed'])}  errors={h['error_count']}")
    return EXIT_OK


def cmd_reset(root, cfg, args):
    p = os.path.join(build_dir(root), "state.json")
    if os.path.exists(p):
        os.remove(p)
    print("Attempt counter reset.")
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compile IBM i sources with TOBi for IBM Bob.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--new-run", action="store_true", help="start a new run (resets the fix-attempt counter)")
        p.add_argument("--max-fix-attempts", type=int, help="override IBMI_MAX_FIX_ATTEMPTS for this run")
        p.add_argument("--full-sync", action="store_true", help="re-send every project file to the IBM i")
        p.add_argument("--dry-run", action="store_true", help="only list what would be compiled")
        p.add_argument("--json", action="store_true", help="print the JSON report instead of text")

    sub.add_parser("check", help="verify SSH, TOBi, remote folder and object library")
    p = sub.add_parser("compile", help="compile the given source files")
    p.add_argument("files", nargs="*")
    common(p)
    p = sub.add_parser("changed", help="compile sources changed in git")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--commit", help="files changed in this commit (e.g. HEAD, a1b2c3d)")
    g.add_argument("--range", help="files changed in A..B")
    g.add_argument("--base", help="files changed on this branch since <ref> (e.g. origin/main), plus uncommitted work")
    g.add_argument("--staged", action="store_true", help="staged changes only")
    g.add_argument("--unstaged", action="store_true", help="unstaged + untracked changes only")
    g.add_argument("--working", action="store_true", help="all uncommitted changes (default)")
    common(p)
    sub.add_parser("status", help="show the current run and attempts used")
    sub.add_parser("reset", help="reset the attempt counter")

    args = ap.parse_args(argv)
    try:
        root = find_root(os.getcwd())
        cfg = load_config(root)
        handler = {"check": cmd_check, "compile": cmd_compile, "changed": cmd_changed,
                   "status": cmd_status, "reset": cmd_reset}[args.cmd]
        return handler(root, cfg, args)
    except EnvError as exc:
        print(f"ENVIRONMENT ERROR: {exc}", file=sys.stderr)
        print("This is not a source-code problem. Do not edit source to work around it.", file=sys.stderr)
        return EXIT_ENV


if __name__ == "__main__":
    sys.exit(main())
