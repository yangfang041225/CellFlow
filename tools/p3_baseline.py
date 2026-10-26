#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CellFlow P3-00 基线审计生成器（可重复执行、非交互）。

一次采集工程现状 / 分支与 PR 拓扑 / 依赖 / 测试 / 数据资产证据，写出 6 份基线文件：

    baseline_inventory.md
    branch_matrix.md
    dependency_inventory.json
    known_failures.md
    test_baseline.json
    data_safety_inventory.json

设计约束
--------
* 不硬编码本机绝对路径：ROOT 由脚本位置推导（tools/p3_baseline.py -> 仓库根），
  同级工作副本从 ROOT.parent 推导。
* 只读：除 docs/p3/ 输出与工作目录（默认 tmp_p3_baseline/，已被 .gitignore 的 tmp*/ 覆盖）
  外不写任何文件；不执行 git commit/push/checkout/reset/clean。
* 后端测试在 HEAD 的导出副本里运行（git checkout-index），避免同仓库并行改动污染基线；
  .venv / web/node_modules / web/dist 用 junction 复用，不复制。
* 本机沙箱限制：Python 子进程只能写工作区内部（写 %TEMP% 会被拒绝），所以工作目录必须在仓库内。

用法
----
    .venv\Scripts\python.exe tools\p3_baseline.py                    # 全量
    .venv\Scripts\python.exe tools\p3_baseline.py --skip-tests      # 只采静态证据
    .venv\Scripts\python.exe tools\p3_baseline.py --compare-httpx2  # 另采“未装/已装 httpx2”两组测试
    .venv\Scripts\python.exe tools\p3_baseline.py --run-mode worktree
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
OUT_DIR = ROOT / "docs" / "p3"
DEFAULT_WORK_DIR = ROOT / "tmp_p3_baseline"

SIBLING_COPIES = ("CellFlow", "CellFlow-audit")

SKIP_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", "data", "results", "logs", "dist",
    ".tmp", ".tmp-work", "tmp-work", "sample-run", "verification", ".streamlit",
    ".dsh-project-memory", "patch", "legacy_ui", "legacy_launchers", ".tmp-test",
    "tmp_p3_baseline",
}
SKIP_DIR_PREFIX = (".venv", "tmp", "pip-", "pip-build-tracker-",
                   "pip-ephem-wheel-cache-", "pip-install-")
SKIP_REL_PATHS = {"tools/_hotfix", "web/.tmp-test"}

REQUIREMENT_FILES = ("requirements.txt", "requirements-cellrank.txt",
                     "requirements-regvelo.txt", "requirements-velovi.txt")

GENERATED_AT = _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def run(cmd, cwd=None, timeout=600, env=None):
    """执行命令，不抛异常；返回 dict(ok/exit/stdout/stderr/cmd)。"""
    full_env = dict(os.environ)
    full_env.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=full_env, stdin=subprocess.DEVNULL,
        )
        return {"ok": proc.returncode == 0, "exit": proc.returncode,
                "stdout": proc.stdout or "", "stderr": proc.stderr or "",
                "cmd": _cmd_str(cmd)}
    except FileNotFoundError as exc:
        return {"ok": False, "exit": -1, "stdout": "",
                "stderr": "FileNotFoundError: %s" % exc, "cmd": _cmd_str(cmd)}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return {"ok": False, "exit": -2, "stdout": out,
                "stderr": "TimeoutExpired after %ss" % timeout, "cmd": _cmd_str(cmd)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exit": -3, "stdout": "",
                "stderr": "%s: %s" % (type(exc).__name__, exc), "cmd": _cmd_str(cmd)}


def phase(label):
    """带时间戳的进度输出（-u 运行时可实时看到卡在哪一步）。"""
    print("[%s] %s" % (time.strftime("%H:%M:%S"), label), flush=True)


def _cmd_str(cmd):
    return " ".join(str(c) for c in cmd)


def sh(cmd, cwd=None, timeout=600, env=None):
    """同 run()，但走 shell：Windows 上 npm/npx 是 .cmd，必须经 cmd /c。"""
    if os.name == "nt":
        cmd = ["cmd", "/c"] + [str(c) for c in cmd]
    return run(cmd, cwd=cwd, timeout=timeout, env=env)


def git(*args, cwd=None, timeout=180):
    # core.quotepath=false 必须先于子命令，否则中文路径会被转义成 \346\223\215 形式
    return run(["git", "-c", "core.quotepath=false"] + [str(a) for a in args],
               cwd=cwd or ROOT, timeout=timeout)


def git_out(*args, cwd=None):
    res = git(*args, cwd=cwd)
    return res["stdout"].strip() if res["ok"] else ""


def human_bytes(num):
    if num is None:
        return "n/a"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f TB" % value


def dir_usage(path):
    path = Path(path)
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0}
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            files += 1
            total += st.st_size
    return {"exists": True, "files": files, "bytes": total}


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def sha1_blob(path):
    """按 git blob 规则算 SHAsh；文本文件先做 CRLF->LF（本仓库 core.autocrlf=true）。

    不归一化会把「工作区 CRLF / 仓库 LF」误判成内容不同——实测 HEAD 自己会被算成
    137 个文件“不同”，归一化后与 git hash-object 一致。
    """
    data = Path(path).read_bytes()
    if b"\x00" not in data[:8192]:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def walk_source_files(root):
    root = Path(root)
    found = {}
    if not root.exists():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        keep = []
        for name in dirnames:
            if name in SKIP_DIR_NAMES or name.startswith(SKIP_DIR_PREFIX):
                continue
            rel = (rel_dir + "/" + name) if rel_dir else name
            if rel in SKIP_REL_PATHS:
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            rel = (rel_dir + "/" + name) if rel_dir else name
            if rel.startswith(SKIP_DIR_PREFIX):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                found[rel] = sha1_blob(path)
            except OSError:
                continue
    return found


def tree_blobs(rev):
    out = git("ls-tree", "-r", "--full-tree", rev)
    result = {}
    if not out["ok"]:
        return result
    for line in out["stdout"].splitlines():
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            result[path] = parts[2]
    return result


# --------------------------------------------------------------------------- #
# 采集：Git / 分支 / PR
# --------------------------------------------------------------------------- #
def collect_git():
    info = {"repo": str(ROOT), "has_git": (ROOT / ".git").exists()}
    if not info["has_git"]:
        return info
    info["branch"] = git_out("rev-parse", "--abbrev-ref", "HEAD")
    info["head"] = git_out("rev-parse", "HEAD")
    info["head_short"] = info["head"][:7]
    info["head_subject"] = git_out("log", "-1", "--pretty=%s")
    info["head_date"] = git_out("log", "-1", "--pretty=%cI")
    porcelain = git_out("status", "--porcelain=v1")
    info["dirty_lines"] = [line for line in porcelain.splitlines() if line.strip()]
    info["dirty_count"] = len(info["dirty_lines"])
    info["is_clean"] = info["dirty_count"] == 0
    info["remotes"] = git_out("remote", "-v").splitlines()
    info["stash"] = [line for line in git_out("stash", "list").splitlines() if line.strip()]
    info["total_commits"] = int(git_out("rev-list", "--count", "HEAD") or 0)
    return info


def collect_refs():
    refs = {"local": [], "remote": [], "ls_remote": [], "errors": []}
    if not (ROOT / ".git").exists():
        return refs
    fmt = "--format=%(refname)|%(objectname)|%(committerdate:iso8601)"
    for line in git_out("for-each-ref", fmt, "refs/heads", "refs/remotes").splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        name, sha, when = parts
        record = {"name": name, "sha": sha, "sha_short": sha[:7], "date": when}
        if name.startswith("refs/heads/"):
            refs["local"].append(record)
        else:
            refs["remote"].append(record)
    remote = git("ls-remote", "--heads", "origin", timeout=180)
    if remote["ok"]:
        for line in remote["stdout"].splitlines():
            sha, _, name = line.partition("\t")
            if name.startswith("refs/heads/"):
                refs["ls_remote"].append({"name": name[len("refs/heads/"):],
                                          "sha": sha, "sha_short": sha[:7]})
    else:
        refs["errors"].append("git ls-remote 失败：%s" % remote["stderr"].strip()[:200])
    return refs


def _branch_keys():
    keys = {}
    for name, ref in (("main", "origin/main"),
                      ("hotfix", "origin/hotfix/public-data-download-connectivity"),
                      ("audit", "origin/audit/phase2-review-20261026"),
                      ("p3", "HEAD")):
        if git("rev-parse", "--verify", "--quiet", ref)["ok"]:
            keys[name] = ref
    return keys


def collect_branch_matrix():
    keys = _branch_keys()
    matrix = {"base_ref": keys.get("main", "origin/main"), "branches": {},
              "pairwise": [], "ancestry": {}}
    base = matrix["base_ref"]
    matrix["base_sha"] = git_out("rev-parse", base)
    refs = list(keys.values())
    for name, ref in keys.items():
        changed = [p for p in git_out("diff", "--name-only", base + "..." + ref).splitlines() if p]
        entry = {
            "ref": ref,
            "sha": git_out("rev-parse", ref),
            "subject": git_out("log", "-1", "--pretty=%s", ref),
            "date": git_out("log", "-1", "--pretty=%cI", ref),
        }
        entry["sha_short"] = entry["sha"][:7]
        entry["merge_base"] = git_out("merge-base", base, ref)
        entry["commits_ahead_of_base"] = int(git_out("rev-list", "--count", base + ".." + ref) or 0)
        entry["commits_behind_base"] = int(git_out("rev-list", "--count", ref + ".." + base) or 0)
        entry["diff_shortstat_vs_base"] = " ".join(
            git_out("diff", "--shortstat", base + "..." + ref).split())
        entry["changed_files"] = len(changed)
        entry["changed_file_names"] = changed
        matrix["branches"][name] = entry

    for index, a in enumerate(refs):
        for b in refs[index + 1:]:
            merge_base = git_out("merge-base", a, b)
            matrix["pairwise"].append({
                "a": a, "b": b, "merge_base": merge_base,
                "a_only": int(git_out("rev-list", "--count", merge_base + ".." + a) or 0),
                "b_only": int(git_out("rev-list", "--count", merge_base + ".." + b) or 0),
            })

    for label, anc, desc in (
            ("hotfix_fix_in_audit", "1ab49e0",
             "origin/audit/phase2-review-20261026"),
            ("audit_head_is_phase3_head", "origin/audit/phase2-review-20261026",
             "origin/phase3/cytelle-rebrand-and-validation"),
            ("hotfix_head_is_ancestor_of_p3",
             "origin/hotfix/public-data-download-connectivity", "HEAD"),
            ("p3_head_on_main",
             "origin/main", "HEAD"),
    ):
        if git("rev-parse", "--verify", "--quiet", anc)["ok"] and \
                git("rev-parse", "--verify", "--quiet", desc)["ok"]:
            matrix["ancestry"][label] = git("merge-base", "--is-ancestor", anc, desc)["ok"]

    # 两条线的重叠文件（决定合并冲突面）
    hot = matrix["branches"].get("hotfix", {}).get("changed_file_names") or []
    aud = matrix["branches"].get("audit", {}).get("changed_file_names") or []
    matrix["overlap_files"] = sorted(set(hot) & set(aud))
    matrix["hotfix_only_files"] = sorted(set(hot) - set(aud))
    return matrix


def collect_prs():
    prs = {"available": False, "items": [], "error": None}
    if not shutil.which("gh"):
        prs["error"] = "gh 未安装"
        return prs
    fields = ("number,title,state,isDraft,baseRefName,headRefName,headRefOid,"
              "mergeable,mergeStateStatus,additions,deletions,changedFiles,commits,"
              "url,createdAt,updatedAt")
    res = sh(["gh", "pr", "list", "--state", "all", "--limit", "30", "--json", fields],
             cwd=ROOT, timeout=120)
    if not res["ok"]:
        prs["error"] = (res["stderr"] or res["stdout"]).strip()[:500]
        return prs
    try:
        raw = json.loads(res["stdout"])
    except json.JSONDecodeError as exc:
        prs["error"] = "gh 输出不是 JSON：%s" % exc
        return prs
    prs["available"] = True
    for item in sorted(raw, key=lambda entry: entry["number"]):
        prs["items"].append({
            "number": item["number"], "title": item["title"], "state": item["state"],
            "draft": item["isDraft"], "base": item["baseRefName"],
            "head": item["headRefName"], "head_sha": item["headRefOid"],
            "head_sha_short": item["headRefOid"][:7],
            "mergeable": item.get("mergeable"), "merge_state": item.get("mergeStateStatus"),
            "additions": item["additions"], "deletions": item["deletions"],
            "changed_files": item["changedFiles"],
            "commits": len(item.get("commits") or []),
            "url": item["url"], "created_at": item["createdAt"],
            "updated_at": item["updatedAt"],
        })
    return prs


def collect_copy_diffs():
    revs = {}
    for key, ref in (("main", "origin/main"),
                     ("hotfix", "origin/hotfix/public-data-download-connectivity"),
                     ("audit", "origin/audit/phase2-review-20261026"),
                     ("HEAD", "HEAD")):
        if git("rev-parse", "--verify", "--quiet", ref)["ok"]:
            revs[key] = ref

    trees = {}
    for key, ref in revs.items():
        trees[key] = {"ref": ref, "sha": git_out("rev-parse", ref),
                      "files": tree_blobs(ref)}

    copies = [{"name": ROOT.name, "path": str(ROOT)}]
    for sibling in SIBLING_COPIES:
        candidate = WORKSPACE / sibling
        if candidate.exists() and candidate.resolve() != ROOT.resolve():
            copies.append({"name": sibling, "path": str(candidate)})

    result = {
        "revs": {key: {"ref": meta["ref"], "sha": meta["sha"],
                       "tracked_files": len(meta["files"])}
                 for key, meta in trees.items()},
        "copies": [],
    }
    for copy in copies:
        walked = walk_source_files(copy["path"])
        entry = {"name": copy["name"], "path": copy["path"],
                 "has_git": (Path(copy["path"]) / ".git").exists(),
                 "walked_files": len(walked), "vs": {}}
        for key, meta in trees.items():
            identical = modified = missing = 0
            modified_paths = []
            for path, blob in meta["files"].items():
                if path not in walked:
                    missing += 1
                elif walked[path] == blob:
                    identical += 1
                else:
                    modified += 1
                    if len(modified_paths) < 12:
                        modified_paths.append(path)
            extra = sorted(set(walked) - set(meta["files"]))
            entry["vs"][key] = {
                "tracked_in_rev": len(meta["files"]),
                "identical": identical, "modified": modified,
                "missing_in_copy": missing, "extra_in_copy": len(extra),
                "pct_identical": round(100.0 * identical / max(1, len(meta["files"])), 1),
                "modified_sample": modified_paths, "extra_sample": extra[:12],
            }
        result["copies"].append(entry)
    return result


# --------------------------------------------------------------------------- #
# 采集：环境与依赖
# --------------------------------------------------------------------------- #
def platform_str():
    import platform
    return {"system": platform.system(), "release": platform.release(),
            "version": platform.version(), "machine": platform.machine(),
            "hostname": platform.node()}


def collect_env():
    env = {
        "generated_at": GENERATED_AT,
        "platform": platform_str(),
        "python_runtime": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "implementation": sys.implementation.name,
            "in_venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
            "prefix": sys.prefix,
            "base_prefix": getattr(sys, "base_prefix", None),
        },
        "tools": {}, "venv": {}, "paths_outside_repo": {},
    }
    for tool, args in (("python", ["--version"]), ("node", ["--version"]),
                       ("npm", ["--version"]), ("git", ["--version"]),
                       ("gh", ["--version"]), ("uv", ["--version"])):
        exe = shutil.which(tool)
        if not exe:
            env["tools"][tool] = {"found": False, "version": None, "path": None}
            continue
        res = sh([tool] + args, timeout=90)
        lines = (res["stdout"] or res["stderr"]).strip().splitlines()
        env["tools"][tool] = {"found": True, "path": exe,
                              "version": lines[0] if lines else ""}

    venv_dir = ROOT / ".venv"
    usage = dir_usage(venv_dir)
    env["venv"] = {
        "path": str(venv_dir), "exists": venv_dir.exists(),
        "size": usage, "size_human": human_bytes(usage["bytes"]),
        "pyvenv_cfg": read_text(venv_dir / "pyvenv.cfg").strip().splitlines()[:8],
        "has_pip": (venv_dir / "Scripts" / "pip.exe").exists() or
                   (venv_dir / "bin" / "pip").exists(),
    }
    local_app = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local_app:
        settings = Path(local_app) / "CellFlow"
        usage_s = dir_usage(settings)
        env["paths_outside_repo"]["settings_dir"] = {
            "path": str(settings), "exists": settings.exists(),
            "files": usage_s["files"], "bytes": usage_s["bytes"],
        }
    return env


def _parse_requirements(path, seen=None):
    seen = seen or set()
    path = Path(path)
    if not path.exists() or path.resolve() in seen:
        return []
    seen.add(path.resolve())
    items = []
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r") or line.startswith("--requirement"):
            target = line.split(maxsplit=1)[-1].strip()
            items.extend(_parse_requirements(path.parent / target, seen))
            continue
        body, _, marker = line.partition(";")
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", body.strip())
        if not match:
            continue
        items.append({"file": path.name, "name": match.group(1),
                      "spec": match.group(2).strip(), "raw": raw.strip(),
                      "marker": marker.strip()})
    return items


INSTALLED_SNIPPET = (
    "import json,importlib.metadata as m;"
    "d={};"
    "[d.__setitem__(dist.metadata['Name'], dist.version) for dist in m.distributions() "
    "if dist.metadata['Name']];"
    "print(json.dumps(d))"
)


def _installed_packages(python_exe):
    """用 importlib.metadata 读已安装包（本机 pip list 会长时间挂起，实测不可用）。"""
    res = run([python_exe, "-c", INSTALLED_SNIPPET], cwd=ROOT, timeout=180)
    if not res["ok"]:
        return {}, "importlib.metadata 读取失败：%s" % res["stderr"][:400]
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError as exc:
        return {}, "已安装包 JSON 解析失败：%s" % exc
    return {str(name): str(version) for name, version in data.items()}, None


def _parse_spec(spec):
    return re.findall(r"(===|==|>=|<=|~=|!=|>|<)\s*([0-9][0-9A-Za-z.\-]*)", spec or "")


def _cmp_versions(left, right):
    def norm(text):
        return [int(part) if part.isdigit() else part
                for part in re.split(r"[.\-+]", text)]
    na, nb = norm(left), norm(right)
    for x, y in zip(na, nb):
        if isinstance(x, int) and isinstance(y, int):
            if x != y:
                return -1 if x < y else 1
        else:
            xs, ys = str(x), str(y)
            if xs != ys:
                return -1 if xs < ys else 1
    if len(na) != len(nb):
        return -1 if len(na) < len(nb) else 1
    return 0


def _satisfies(version, spec):
    for op, want in _parse_spec(spec):
        cmp = _cmp_versions(version, want)
        if op in ("==", "===") and cmp != 0:
            return False
        if op == ">=" and cmp < 0:
            return False
        if op == "<=" and cmp > 0:
            return False
        if op == ">" and cmp <= 0:
            return False
        if op == "<" and cmp >= 0:
            return False
        if op == "!=" and cmp == 0:
            return False
        if op == "~=" and cmp < 0:
            return False
    return True


def collect_deps(env):
    deps = {"generated_at": GENERATED_AT, "python": {}, "node": {}, "isolated_envs": {}}
    python_exe = sys.executable
    installed, pip_error = _installed_packages(python_exe)
    lowered = {name.lower().replace("_", "-"): (name, version)
               for name, version in installed.items()}

    requirements = []
    for filename in REQUIREMENT_FILES:
        requirements.extend(_parse_requirements(ROOT / filename))

    satisfied, missing, mismatched = [], [], []
    for item in requirements:
        key = item["name"].lower().replace("_", "-")
        if key in lowered:
            real_name, version = lowered[key]
            record = {"name": item["name"], "installed": version,
                      "spec": item["spec"], "file": item["file"],
                      "marker": item["marker"]}
            if _satisfies(version, item["spec"]):
                satisfied.append(record)
            else:
                mismatched.append(record)
        else:
            missing.append({"name": item["name"], "spec": item["spec"],
                            "file": item["file"], "marker": item["marker"]})

    lock_files = {}
    for lock in ("environment-venv.lock.txt", "environment-venv-velovi.lock.txt"):
        path = ROOT / lock
        lock_files[lock] = {
            "exists": path.exists(),
            "lines": len([line for line in read_text(path).splitlines() if line.strip()]),
        }

    deps["python"] = {
        "interpreter": python_exe,
        "version": sys.version.split()[0],
        "pip_error": pip_error,
        "installed_count": len(installed),
        "installed": dict(sorted(installed.items(), key=lambda kv: kv[0].lower())),
        "requirement_files": [name for name in REQUIREMENT_FILES
                              if (ROOT / name).exists()],
        "requirements": requirements,
        "requirement_check": {
            "satisfied": satisfied, "missing": missing, "version_mismatch": mismatched,
            "counts": {"satisfied": len(satisfied), "missing": len(missing),
                       "mismatch": len(mismatched)},
        },
        "lock_files": lock_files,
    }

    web = ROOT / "web"
    package_json = {}
    try:
        package_json = json.loads(read_text(web / "package.json") or "{}")
    except json.JSONDecodeError:
        package_json = {}
    npm_listing, npm_error = {}, None
    if web.exists():
        res = sh(["npm", "ls", "--depth=0", "--json"], cwd=web, timeout=300)
        try:
            payload = json.loads(res["stdout"] or "{}")
            for name, node in (payload.get("dependencies") or {}).items():
                npm_listing[name] = node.get("version") if isinstance(node, dict) else str(node)
        except json.JSONDecodeError as exc:
            npm_error = "npm ls 输出解析失败：%s" % exc
    declared = {}
    declared.update(package_json.get("dependencies") or {})
    declared.update(package_json.get("devDependencies") or {})
    node_state = {"declared_but_missing": [], "installed_not_declared": [], "version_drift": []}
    for name, spec in declared.items():
        if name not in npm_listing:
            node_state["declared_but_missing"].append({"name": name, "spec": spec})
        else:
            wanted = (spec or "").lstrip("^~>=< ")
            have = npm_listing[name]
            if wanted and have and have != wanted:
                node_state["version_drift"].append({"name": name, "declared": spec,
                                                    "installed": have})
    for name in npm_listing:
        if name not in declared:
            node_state["installed_not_declared"].append({"name": name,
                                                          "installed": npm_listing[name]})
    deps["node"] = {
        "node_version": (env["tools"].get("node") or {}).get("version"),
        "npm_version": (env["tools"].get("npm") or {}).get("version"),
        "package_json_path": str(web / "package.json"),
        "scripts": package_json.get("scripts") or {},
        "declared": dict(sorted(declared.items())),
        "installed_top_level": dict(sorted(npm_listing.items())),
        "installed_count": len(npm_listing),
        "consistency": node_state,
        "npm_error": npm_error,
    }
    deps["isolated_envs"]["velovi"] = check_velovi_env()
    return deps


def check_velovi_env():
    candidates = [ROOT / ".venv-velovi"]
    for sibling in SIBLING_COPIES:
        candidates.append(WORKSPACE / sibling / ".venv-velovi")
    found = []
    for candidate in candidates:
        exe = candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        record = {"path": str(candidate), "python_exists": exe.exists(), "usable": False}
        if exe.exists():
            probe = run([str(exe), "-c",
                         "import sys,scvi,torch;print('PY',sys.version.split()[0]);"
                         "print('SCVI',scvi.__version__);print('TORCH',torch.__version__)"],
                        cwd=candidate.parent, timeout=300)
            record["probe_ok"] = probe["ok"]
            record["probe_stdout"] = probe["stdout"].strip().splitlines()[-3:]
            record["probe_error"] = None if probe["ok"] else probe["stderr"].strip()[-400:]
            record["usable"] = probe["ok"]
        found.append(record)
    active = next((item for item in found if item.get("usable")), None)
    return {
        "required_by": "requirements-velovi.txt（scvi-tools 1.2 要求 numpy<2，与主环境 numpy 2.x 冲突，故必须独立环境）",
        "expected_in_this_checkout": (ROOT / ".venv-velovi").exists(),
        "candidates": found,
        "usable_anywhere": active is not None,
        "usable_path": active["path"] if active else None,
        "note": ("本工作副本没有 .venv-velovi；同级 CellFlow 里的 .venv-velovi 可导入 scvi/torch，"
                 "但不会被本仓库使用。" if not (ROOT / ".venv-velovi").exists() else ""),
    }


# --------------------------------------------------------------------------- #
# 采集：数据资产
# --------------------------------------------------------------------------- #
SAFETY_DIRS = ("data", "results", "logs", "web/dist", "web/node_modules", ".venv",
               ".venv-velovi", ".git", "verification", "sample-run", "tmp_p3_baseline")


def measure_tree(root, targets):
    """一次遍历统计 total + 各目标子目录（跳过 junction/symlink，避免重复计数）。"""
    import stat as _stat
    root = Path(root)
    per = {target: {"exists": (root / target.replace("/", os.sep)).exists(),
                    "files": 0, "bytes": 0} for target in targets}
    total = {"exists": root.exists(), "files": 0, "bytes": 0}
    if not root.exists():
        return total, per
    reparse_flag = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            if reparse_flag:
                try:
                    if os.lstat(full).st_file_attributes & reparse_flag:
                        continue
                except (OSError, AttributeError):
                    pass
            kept.append(name)
        dirnames[:] = kept
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                size = os.lstat(full).st_size
            except OSError:
                continue
            total["files"] += 1
            total["bytes"] += size
            rel = (rel_dir + "/" + name) if rel_dir else name
            for target in targets:
                if rel == target or rel.startswith(target + "/"):
                    per[target]["files"] += 1
                    per[target]["bytes"] += size
                    break
    return total, per


def collect_storage():
    storage = {"generated_at": GENERATED_AT, "repo": str(ROOT), "copies": []}
    copies = [("CellFlow-hotfix", ROOT)]
    for sibling in SIBLING_COPIES:
        candidate = WORKSPACE / sibling
        if candidate.exists():
            copies.append((sibling, candidate))
    for name, path in copies:
        total, per = measure_tree(path, SAFETY_DIRS)
        entry = {"name": name, "path": str(path),
                 "has_git": (path / ".git").exists(),
                 "dirs": per, "total": total}
        storage["copies"].append(entry)
    storage["gitignore"] = _gitignore_report()
    storage["user_data"] = _user_data_counts()
    storage["large_files"] = _large_files(ROOT / "data", ROOT / "results",
                                          limit=20, min_bytes=1 << 20)
    return storage


def _gitignore_report():
    path = ROOT / ".gitignore"
    report = {"path": str(path), "exists": path.exists(),
              "lines": [line for line in read_text(path).splitlines() if line.strip()],
              "checks": {}}
    probes = ["data", "results", "logs", ".venv", ".venv-velovi", "web/dist",
              "web/node_modules", "installation.json", "tmp_p3_baseline",
              "sample-run", "verification", "tools/_hotfix",
              "docs/p3/baseline_inventory.md"]
    for probe in probes:
        # 目录型规则（data/、verification/）只对“目录”生效；不存在的路径要带尾斜杠才算目录
        res = git("check-ignore", "-v", "--no-index", "--", probe)
        rule = res["stdout"].strip().splitlines()[0] if res["ok"] and res["stdout"].strip() else None
        is_dir_probe = probe in SAFETY_DIRS or probe.endswith("/")
        if not res["ok"] and is_dir_probe:
            # 目录型规则（data/、verification/）只对目录生效；对文件路径强加尾斜杠会得到假阳性
            alt = git("check-ignore", "-v", "--no-index", "--", probe.rstrip("/") + "/")
            if alt["ok"]:
                res, rule = alt, alt["stdout"].strip().splitlines()[0]
        report["checks"][probe] = {
            "ignored": res["ok"], "rule": rule,
            "note": None if res["ok"] else "未被 .gitignore 覆盖",
        }
    tracked = [line for line in git_out("ls-files").splitlines() if line.strip()]
    report["tracked_file_count"] = len(tracked)
    report["tracked_data_like"] = [p for p in tracked
                                   if re.match(r"^(data|results|logs)/", p)][:20]
    return report


def _user_data_counts():
    counts = {"projects_json": None, "jobs": None, "downloads": None, "notes": []}
    projects_file = ROOT / "results" / "projects.json"
    if projects_file.exists():
        try:
            payload = json.loads(read_text(projects_file) or "{}")
            items = payload.get("items") or []
            counts["projects_json"] = {
                "path": str(projects_file), "active": payload.get("active"),
                "project_count": len(items),
                "projects": [{"id": item.get("id"), "name": item.get("name"),
                              "created": item.get("created")} for item in items],
            }
        except json.JSONDecodeError as exc:
            counts["notes"].append("projects.json 解析失败：%s" % exc)
    else:
        counts["notes"].append("results/projects.json 不存在")

    queue = ROOT / "results" / "queue.sqlite"
    if queue.exists():
        try:
            con = sqlite3.connect("file:%s?mode=ro" % queue.as_posix(), uri=True)
            cur = con.cursor()
            tables = [row[0] for row in cur.execute(
                "select name from sqlite_master where type='table'")]
            counts["jobs"] = {"path": str(queue), "tables": tables, "row_counts": {}}
            for table in tables:
                try:
                    counts["jobs"]["row_counts"][table] = cur.execute(
                        "select count(*) from [%s]" % table).fetchone()[0]
                except sqlite3.Error as exc:
                    counts["jobs"]["row_counts"][table] = "error: %s" % exc
            con.close()
        except sqlite3.Error as exc:
            counts["notes"].append("queue.sqlite 读取失败：%s" % exc)
    else:
        counts["notes"].append("results/queue.sqlite 不存在")

    state = ROOT / "data" / "public" / ".downloads" / "state.json"
    if state.exists():
        try:
            payload = json.loads(read_text(state) or "[]")
            counts["downloads"] = {
                "path": str(state),
                "task_count": len(payload),
                "states": {st: sum(1 for task in payload if task.get("state") == st)
                           for st in sorted({task.get("state") for task in payload})},
                "identifiers": sorted({task.get("identifier") for task in payload
                                       if task.get("identifier")}),
            }
        except json.JSONDecodeError as exc:
            counts["notes"].append("downloads state.json 解析失败：%s" % exc)
    else:
        counts["notes"].append("data/public/.downloads/state.json 不存在")
    return counts


def _large_files(*dirs, limit=20, min_bytes=1 << 20):
    found = []
    for base in dirs:
        base = Path(base)
        if not base.exists():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size >= min_bytes:
                    found.append({"path": str(path), "bytes": size,
                                  "size": human_bytes(size)})
    found.sort(key=lambda item: item["bytes"], reverse=True)
    return found[:limit]


# --------------------------------------------------------------------------- #
# 测试：HEAD 导出副本 + 结构化 unittest 结果
# --------------------------------------------------------------------------- #
INTERNAL_RUNNER_SRC = r'''
import io, json, os, sys, time, traceback, unittest

repo = sys.argv[1]
out_path = sys.argv[2]
os.chdir(repo)
sys.path.insert(0, repo)

_blocked = [m for m in os.environ.get("P3_BLOCK_MODULES", "").split(",") if m]
if _blocked:
    import importlib.abc

    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in _blocked:
                raise ModuleNotFoundError("No module named %r (blocked by P3 baseline runner)" % fullname)
            return None

    sys.meta_path.insert(0, _Blocker())


class Rec(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.records = []
        self._t0 = {}

    def startTest(self, test):
        self._t0[test.id()] = time.time()
        super().startTest(test)

    def _add(self, test, status, detail):
        self.records.append({"id": test.id(), "status": status, "detail": detail,
                             "seconds": round(time.time() - self._t0.get(test.id(), time.time()), 3)})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "passed", "")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "failed", self._short(err))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "error", self._short(err))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, "skipped", reason)

    @staticmethod
    def _short(err):
        etype, evalue, tb = err
        return "".join(traceback.format_exception(etype, evalue, tb)[-3:]).strip()[:1200]


loader = unittest.TestLoader()
suite = loader.discover(start_dir="tests", top_level_dir=".")
stream = io.StringIO()
t0 = time.time()
res = unittest.TextTestRunner(stream=stream, verbosity=1, resultclass=Rec).run(suite)
payload = {
    "collected": res.testsRun,
    "elapsed_seconds": round(time.time() - t0, 1),
    "counts": {
        "passed": sum(1 for r in res.records if r["status"] == "passed"),
        "failed": sum(1 for r in res.records if r["status"] == "failed"),
        "errors": sum(1 for r in res.records if r["status"] == "error"),
        "skipped": sum(1 for r in res.records
                       if r["status"] == "skipped" and not r["id"].startswith(("setUpClass", "tearDownClass"))),
        "class_fixture_records": sum(1 for r in res.records
                                     if r["id"].startswith(("setUpClass", "tearDownClass"))),
    },
    "load_errors": len(getattr(loader, "errors", []) or []),
    "records": res.records,
    "text_tail": stream.getvalue()[-4000:],
}
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=1)
print("RESULT", json.dumps(payload["counts"]), "collected", payload["collected"])
'''


def _export_head(dest):
    """把 HEAD 的所有 blob 写到 dest（不依赖 index、不受 core.quotepath 影响）。"""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "HEAD"],
        cwd=str(ROOT), capture_output=True)
    if listing.returncode != 0:
        return None, "git ls-tree 失败：%s" % listing.stderr.decode("utf-8", "replace")[:300]
    entries = []
    for chunk in listing.stdout.split(b"\0"):
        if not chunk:
            continue
        meta, _, path = chunk.partition(b"\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == b"blob":
            entries.append((parts[2], path))
    if not entries:
        return None, "HEAD 里没有可导出的 blob"
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=str(ROOT), input=b"\n".join(sha for sha, _ in entries) + b"\n",
        capture_output=True)
    if batch.returncode != 0:
        return None, "git cat-file 失败：%s" % batch.stderr.decode("utf-8", "replace")[:300]
    stream = io_bytes(batch.stdout)
    written = 0
    for _sha, raw_path in entries:
        header = stream.readline()
        parts = header.split()
        if len(parts) != 3:
            continue
        size = int(parts[2])
        data = stream.read(size)
        stream.read(1)  # 对象之间的换行
        target = dest / raw_path.decode("utf-8", "surrogateescape")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written += 1
    # data/ results/ logs/ 被 .gitignore 排除，导出里不会有；但应用与测试隐式依赖它们存在
    # （实测：缺这三个空目录时 tests.test_pipeline 的 2 个 auto 用例会静默丢掉 'fate'）
    for runtime_dir in ("data", "results", "logs"):
        (dest / runtime_dir).mkdir(parents=True, exist_ok=True)
    return dest, None if written else "导出 0 个文件"


def io_bytes(data):
    import io
    return io.BytesIO(data)


def _link_shared(export_dir):
    """把 .venv / node_modules / dist 以 junction 复用进导出副本。"""
    warnings = []
    links = [(export_dir / ".venv", ROOT / ".venv"),
             (export_dir / "web" / "node_modules", ROOT / "web" / "node_modules"),
             (export_dir / "web" / "dist", ROOT / "web" / "dist")]
    for link, target in links:
        if not target.exists() or link.exists():
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            out = run(["cmd", "/c", "mklink", "/J", str(link), str(target)], timeout=120)
            if not out["ok"]:
                warnings.append("junction 失败：%s（%s）" % (link.name, out["stderr"][:120]))
        else:
            try:
                os.symlink(target, link, target_is_directory=True)
            except OSError as exc:
                warnings.append("symlink 失败：%s（%s）" % (link, exc))
    return warnings


def _python_exe():
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return candidate if candidate.exists() else Path(sys.executable)


def run_backend_suite(repo_dir, work_dir, label, extra_env=None):
    runner_path = work_dir / "run_unittest_json.py"
    runner_path.write_text(INTERNAL_RUNNER_SRC, encoding="utf-8")
    out_path = work_dir / ("unittest_%s.json" % label)
    if out_path.exists():
        out_path.unlink()
    cmd = [str(_python_exe()), str(runner_path), str(repo_dir), str(out_path)]
    started = time.time()
    env = {"PYTHONIOENCODING": "utf-8"}
    if extra_env:
        env.update(extra_env)
    res = run(cmd, cwd=repo_dir, timeout=3600, env=env)
    elapsed = round(time.time() - started, 1)
    payload = None
    if out_path.exists():
        try:
            payload = json.loads(read_text(out_path))
        except json.JSONDecodeError:
            payload = None
    return {
        "label": label,
        "command": "%s -m unittest discover -s tests（等价的程序化 runner，见 tools/p3_baseline.py）"
                   % _python_exe().name,
        "cwd": str(repo_dir),
        "exit_code": res["exit"],
        "wall_seconds": elapsed,
        "result_file": str(out_path),
        "collected": (payload or {}).get("collected"),
        "counts": (payload or {}).get("counts"),
        "load_errors": (payload or {}).get("load_errors"),
        "records": (payload or {}).get("records") or [],
        "text_tail": (payload or {}).get("text_tail") or (res["stdout"] + res["stderr"])[-3000:],
    }


def _has_module(python_exe, module):
    res = run([str(python_exe), "-c", "import %s" % module], cwd=ROOT, timeout=120)
    return res["ok"]


def _pip_install(python_exe, specs, uninstall=False):
    if uninstall:
        cmd = [str(python_exe), "-m", "pip", "uninstall", "-y",
               "--disable-pip-version-check"] + list(specs)
    else:
        cmd = [str(python_exe), "-m", "pip", "install", "--no-input",
               "--disable-pip-version-check"] + list(specs)
    res = run(cmd, cwd=ROOT, timeout=1200)
    return {"cmd": _cmd_str(cmd), "ok": res["ok"], "exit": res["exit"],
            "tail": (res["stdout"] + res["stderr"]).strip()[-1200:]}


def collect_tests(args, work_dir):
    tests = {"generated_at": GENERATED_AT, "backend": {"runs": [], "notes": []},
             "frontend": {"runs": [], "notes": []}, "environment_notes": []}
    python_exe = _python_exe()

    if args.run_mode == "export":
        export_dir, warning = _export_head(work_dir / "head-export")
        if export_dir is None:
            tests["backend"]["notes"].append(warning or "导出失败")
            tests["backend"]["notes"].append("退化为在当前工作区运行（结果可能被并行改动污染）。")
            repo_dir = ROOT
        else:
            repo_dir = export_dir
            link_warnings = _link_shared(export_dir)
            tests["backend"]["notes"].append(
                "后端测试在 HEAD(%s) 的导出副本中运行：git ls-tree/cat-file 导出到 %s，"
                "并 junction 复用 .venv / web/node_modules / web/dist。"
                % (git_out("rev-parse", "--short", "HEAD"), export_dir))
            tests["backend"]["notes"].extend(link_warnings)
    else:
        repo_dir = ROOT
        dirty = [line for line in git_out("status", "--porcelain=v1").splitlines() if line.strip()]
        tests["backend"]["notes"].append(
            "后端测试直接在当前工作区运行；工作区有 %d 项未提交改动（与并行任务共享同一副本），"
            "结果不是纯净基线。" % len(dirty))

    if args.reuse_existing and (OUT_DIR / "test_baseline.json").exists():
        try:
            previous = json.loads(read_text(OUT_DIR / "test_baseline.json"))
            tests["backend"] = previous.get("backend") or tests["backend"]
            tests["frontend"] = previous.get("frontend") or tests["frontend"]
            notes = list(previous.get("environment_notes") or [])
            reuse_note = ("本次复用上一轮 test_baseline.json 的测试结果（--reuse-existing），"
                          "未重新执行测试。")
            if reuse_note not in notes:
                notes.append(reuse_note)
            tests["environment_notes"] = notes
            if not args.skip_frontend and not tests["frontend"]["runs"]:
                pass
            return tests
        except json.JSONDecodeError:
            tests["backend"]["notes"].append("复用失败：已有 test_baseline.json 不是合法 JSON")

    if not args.skip_tests:
        httpx_present = _has_module(python_exe, "httpx2")
        tests["environment_notes"].append(
            "采集开始时 venv 是否已安装 httpx2：%s" % ("是" if httpx_present else "否"))
        if args.compare_httpx2 and httpx_present:
            # 不真正卸载共享 venv 的包（会被并行任务同时使用），改用 import 阻断模拟“未安装”
            tests["environment_notes"].append(
                "before_httpx2 用 import 阻断（P3_BLOCK_MODULES=httpx2,httpcore2）模拟“包不存在”，"
                "没有真正卸载共享 venv 里的包——因为同一 venv 正被并行任务使用。")
            tests["backend"]["runs"].append(run_backend_suite(
                repo_dir, work_dir, "before_httpx2_blocked",
                extra_env={"P3_BLOCK_MODULES": "httpx2,httpcore2"}))
            tests["backend"]["runs"].append(run_backend_suite(repo_dir, work_dir,
                                                             "after_httpx2"))
        else:
            label = "with_httpx2" if httpx_present else "without_httpx2"
            tests["backend"]["runs"].append(run_backend_suite(repo_dir, work_dir, label))

    web_dir = (Path(repo_dir) / "web") if (Path(repo_dir) / "web").exists() else (ROOT / "web")
    if not args.skip_frontend and web_dir.exists():
        tsc = sh(["npx", "--no-install", "tsc", "--noEmit"], cwd=web_dir, timeout=1200)
        tests["frontend"]["runs"].append({
            "label": "tsc --noEmit",
            "command": "npx --no-install tsc --noEmit",
            "cwd": str(web_dir), "exit_code": tsc["exit"], "passed": tsc["ok"],
            "error_count": len(re.findall(r"error TS\d+", tsc["stdout"] + tsc["stderr"])),
            "output_tail": (tsc["stdout"] + tsc["stderr"]).strip()[-1500:],
        })
        npm_test = sh(["npm", "test"], cwd=web_dir, timeout=1200)
        combined = npm_test["stdout"] + npm_test["stderr"]
        summary = {}
        for key in ("tests", "pass", "fail", "skipped", "todo", "cancelled"):
            match = re.search(r"(?:ℹ|#)\s*%s\s+(\d+)" % key, combined)
            if match:
                summary[key] = int(match.group(1))
        tests["frontend"]["runs"].append({
            "label": "npm test",
            "command": "npm test（tsc -p tsconfig.test.json && node --test tests/net.test.mjs）",
            "cwd": str(web_dir), "exit_code": npm_test["exit"], "passed": npm_test["ok"],
            "summary": summary, "output_tail": combined.strip()[-2500:],
        })
    return tests


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def build_inventory_md(ctx):
    git_info, env, copies, storage = ctx["git"], ctx["env"], ctx["copy_diffs"], ctx["storage"]
    lines = ["# P3-00 基线审计：工程现状清单", ""]
    lines.append("- 生成时间：%s" % GENERATED_AT)
    lines.append("- 生成脚本：tools/p3_baseline.py（可重复执行，见第 6 节）")
    lines.append("- 仓库：%s" % ROOT)
    lines.append("- 当前分支：%s；HEAD：%s（%s）" % (
        git_info.get("branch"), git_info.get("head_short"), git_info.get("head_subject")))
    lines.append("- HEAD 提交时间：%s" % git_info.get("head_date"))
    lines.append("")
    lines.append("> **快照口径**：本文件是采集时刻的快照。该工作副本与其它并行任务共享，"
                 "采集期间 HEAD 会被并行任务推进（本次就发生过：审计开始时 HEAD=f688fc7，"
                 "采集时已是 %s），未提交改动也会变化。测试基线因此改为在 HEAD 的导出副本里跑，"
                 "见 test_baseline.json 的 backend.notes。" % git_info.get("head_short"))
    lines.append("")

    lines.append("## 1. 三个本地工作副本")
    lines.append("")
    rows = []
    for copy in copies["copies"]:
        head_vs = copy["vs"].get("HEAD", {})
        rows.append([copy["name"], copy["path"], "是" if copy["has_git"] else "**否**",
                     copy["walked_files"],
                     "%s / %s / %s" % (head_vs.get("identical", "-"),
                                       head_vs.get("modified", "-"),
                                       head_vs.get("missing_in_copy", "-"))])
    lines.append(_md_table(["副本", "路径", "有 .git", "受控源文件数",
                            "vs HEAD：相同 / 内容不同 / 缺失"], rows))
    lines.append("")
    lines.append("结论：只有 CellFlow-hotfix 是 git 工作副本；CellFlow 与 CellFlow-audit 是"
                 "**没有 .git 的普通目录拷贝**，谈不上分支与 HEAD，只能按文件内容与提交比对。")
    lines.append("")

    lines.append("### 1.1 源文件与仓库提交的差异规模")
    lines.append("")
    lines.append("方法：对提交执行 git ls-tree -r 取 blob SHA；对副本内受控源文件按同一算法"
                 "（sha1(\"blob <size>\\0\" + content)）计算；排除 .venv*、node_modules、data/、"
                 "results/、logs/、web/dist、__pycache__、pip-*、tmp* 等非源码目录。")
    lines.append("")
    lines.append("基准提交：")
    for key, meta in copies["revs"].items():
        lines.append("- %s = %s（%d 个 blob）" % (key, meta["sha"], meta["tracked_files"]))
    lines.append("")
    rows = []
    for copy in copies["copies"]:
        for key in ("HEAD", "main", "hotfix", "audit"):
            stat = copy["vs"].get(key)
            if not stat:
                continue
            rows.append([copy["name"], key, stat["tracked_in_rev"], stat["identical"],
                         stat["modified"], stat["missing_in_copy"], stat["extra_in_copy"],
                         "%.1f%%" % stat["pct_identical"]])
    lines.append(_md_table(["副本", "对比提交", "提交内文件", "相同", "内容不同", "副本缺失",
                            "副本多出", "相同占比"], rows))
    lines.append("")
    for copy in copies["copies"]:
        stat = copy["vs"].get("audit") or {}
        if copy["name"] != ROOT.name and stat:
            lines.append("- %s 相对 audit（a43ae48）：相同 %d、内容不同 %d、缺失 %d、多出 %d；"
                         "多出样例：%s" % (copy["name"], stat["identical"], stat["modified"],
                                          stat["missing_in_copy"], stat["extra_in_copy"],
                                          "、".join(stat["extra_sample"][:5]) or "无"))
    lines.append("")

    lines.append("## 2. 解释器与依赖环境")
    lines.append("")
    rows = []
    for tool in ("python", "node", "npm", "git", "gh", "uv"):
        meta = env["tools"].get(tool) or {}
        rows.append([tool, "是" if meta.get("found") else "否",
                     meta.get("version") or "-", meta.get("path") or "-"])
    lines.append(_md_table(["工具", "可用", "版本", "路径"], rows))
    lines.append("")
    venv = env["venv"]
    lines.append("- 项目 venv：%s（存在=%s，%s，%d 个文件，含 pip=%s）" % (
        venv["path"], venv["exists"], venv["size_human"], venv["size"]["files"],
        venv["has_pip"]))
    lines.append("- venv 解释器：Python %s（sys.prefix = %s）" % (
        env["python_runtime"]["version"], env["python_runtime"]["prefix"]))
    lines.append("- pyvenv.cfg：%s" % ("；".join(venv["pyvenv_cfg"][:5]) or "缺失"))
    settings = env.get("paths_outside_repo", {}).get("settings_dir")
    if settings:
        lines.append("- 仓库外设置目录：%s（存在=%s，%d 个文件）。HEAD 版本的数据根在仓库内："
                     "cellflow/workspace.py 中 ROOT = Path(__file__).resolve().parents[1]；"
                     "%%LOCALAPPDATA%%/CellFlow 目前为空。" % (
                         settings["path"], settings["exists"], settings["files"]))
    lines.append("- veloVI 隔离环境：本仓库没有 .venv-velovi（详见 dependency_inventory.json "
                 "的 isolated_envs.velovi）。")
    lines.append("")

    lines.append("## 3. 数据目录位置与体积")
    lines.append("")
    rows = []
    for copy in storage["copies"]:
        row = [copy["name"], human_bytes(copy["total"]["bytes"]), copy["total"]["files"]]
        for target in ("data", "results", "logs", "web/dist", ".venv", ".venv-velovi",
                       "web/node_modules"):
            usage = copy["dirs"].get(target) or {}
            row.append(human_bytes(usage.get("bytes", 0)) if usage.get("exists") else "-")
        rows.append(row)
    lines.append(_md_table(["副本", "总大小", "总文件数", "data", "results", "logs",
                            "web/dist", ".venv", ".venv-velovi", "web/node_modules"], rows))
    lines.append("")
    if storage["large_files"]:
        lines.append("本仓库 data/ 与 results/ 中 ≥1 MB 的文件：")
        for item in storage["large_files"][:8]:
            lines.append("- %s — %s" % (item["path"], item["size"]))
        lines.append("")

    lines.append("## 4. 采集时刻的工作区状态")
    lines.append("")
    lines.append("- git status --porcelain 条目数：%d（干净=%s）" % (
        git_info.get("dirty_count"), "是" if git_info.get("is_clean") else "否"))
    for line in git_info.get("dirty_lines", [])[:40]:
        lines.append("  - %s" % line)
    lines.append("- git stash list：%s" % ("无" if not git_info.get("stash")
                                           else git_info["stash"]))
    lines.append("- remote：%s" % "；".join(git_info.get("remotes", [])[:4]))
    lines.append("")
    lines.append("未提交改动里的两类东西要分清：")
    lines.append("")
    lines.append("1. **PR #4 遗留**：.github/workflows/ci.yml 增加 run: npm test —— 因 gh 令牌"
                 "缺 workflow scope 无法推送（见 branch_matrix.md / known_failures.md）。")
    lines.append("2. **并行任务的在途改动**（cellflow/api.py、cellflow/downloads.py、"
                 "web/src/views/Data.tsx、新增 cellflow/paths.py、tests/test_geo_*.py 等），"
                 "不属于本审计，也不应被本审计的结论覆盖。")
    lines.append("")
    lines.append("## 5. 交付物本身")
    lines.append("")
    lines.append("- 本目录 docs/p3/ 未被 .gitignore 覆盖（git check-ignore 结果见 "
                 "data_safety_inventory.json），属于要提交的文档。")
    lines.append("- 6 份文件全部由 tools/p3_baseline.py 生成，重复执行会覆盖。")
    lines.append("")
    lines.append("## 6. 复现命令")
    lines.append("")
    lines.append("    cd <repo>")
    lines.append("    .venv\\Scripts\\python.exe tools\\p3_baseline.py")
    lines.append("")
    lines.append("可选：--skip-tests 只采静态证据；--compare-httpx2 额外采「未装/已装 httpx2」"
                 "两组后端测试；--run-mode worktree 在当前工作区跑测试（会被并行改动污染）。")
    lines.append("")
    return "\n".join(lines)


def build_branch_matrix_md(ctx):
    branch, prs = ctx["branch"], ctx["prs"]
    matrix = branch["matrix"]
    lines = ["# P3-00 基线审计：分支与 PR 拓扑", ""]
    lines.append("- 生成时间：%s" % GENERATED_AT)
    lines.append("- 仓库：%s（origin = yangfang041225/CellFlow）" % ROOT)
    lines.append("")

    lines.append("## 1. 远端分支（git ls-remote --heads origin）")
    lines.append("")
    local_by_name = {rec["name"].replace("refs/heads/", ""): rec
                     for rec in branch["refs"]["local"]}
    rows = []
    for item in branch["refs"]["ls_remote"]:
        local = local_by_name.get(item["name"])
        rows.append([item["name"], item["sha_short"],
                     local["sha_short"] if local else "-",
                     local["date"][:19].replace("T", " ") if local else "-"])
    lines.append(_md_table(["远端分支", "远端 SHA", "本地同名分支 SHA", "本地提交时间"], rows))
    lines.append("")
    remote_names = [item["name"] for item in branch["refs"]["ls_remote"]]
    if "p3/batch1-data-acquisition" not in remote_names:
        lines.append("- p3/batch1-data-acquisition **只在本地存在，尚未推送到 origin**"
                     "（远端分支列表里没有它），因此 CI 和协作者目前看不到 P3 分支。")
    if matrix["ancestry"].get("audit_head_is_phase3_head"):
        lines.append("- 远端 phase3/cytelle-rebrand-and-validation 与 "
                     "audit/phase2-review-20261026 指向同一个提交（%s），"
                     "说明 phase3 分支是从 audit 提交拉出的，不含下载 hotfix。"
                     % matrix["branches"]["audit"]["sha_short"])
    lines.append("")

    lines.append("## 2. 四个 PR（gh pr list --state all）")
    lines.append("")
    if prs["available"]:
        rows = []
        for item in prs["items"]:
            rows.append(["#%d" % item["number"],
                         item["state"] + ("（DRAFT）" if item["draft"] else ""),
                         item["base"], item["head"], item["head_sha_short"],
                         item["changed_files"],
                         "+%d/−%d" % (item["additions"], item["deletions"]),
                         item["commits"]])
        lines.append(_md_table(["PR", "状态", "base", "head 分支", "head SHA", "文件",
                                "增/删", "提交数"], rows))
        lines.append("")
        for item in prs["items"]:
            lines.append("### PR #%d — %s" % (item["number"], item["title"]))
            lines.append("")
            lines.append("- URL：%s" % item["url"])
            lines.append("- 状态：%s%s；mergeable=%s；mergeStateStatus=%s" % (
                item["state"], "（草稿）" if item["draft"] else "",
                item.get("mergeable"), item.get("merge_state")))
            lines.append("- base：%s；head：%s @ %s" % (
                item["base"], item["head"], item["head_sha"]))
            lines.append("- 规模：%d 个文件、+%d/−%d、%d 个提交" % (
                item["changed_files"], item["additions"], item["deletions"], item["commits"]))
            lines.append("")
    else:
        lines.append("- gh 不可用或失败：%s" % prs.get("error"))
        lines.append("")

    lines.append("## 3. 合并关系（merge-base 与提交计数）")
    lines.append("")
    rows = []
    for name, entry in matrix["branches"].items():
        rows.append([name, entry["sha_short"], entry["merge_base"][:12] or "-",
                     entry["commits_ahead_of_base"], entry["commits_behind_base"],
                     entry["changed_files"], entry["diff_shortstat_vs_base"]])
    lines.append(_md_table(["分支", "HEAD", "与 main 的 merge-base", "领先 main 提交",
                            "落后 main 提交", "变更文件", "git diff --shortstat main...分支"],
                           rows))
    lines.append("")
    lines.append("两两关系：")
    lines.append("")
    for pair in matrix["pairwise"]:
        lines.append("- %s ↔ %s：merge-base %s，各自独有提交 %d / %d" % (
            pair["a"], pair["b"], pair["merge_base"][:12] or "-",
            pair["a_only"], pair["b_only"]))
    lines.append("")
    labels = {
        "hotfix_fix_in_audit": "hotfix 的修复提交 1ab49e0 是否已在 audit 分支里",
        "audit_head_is_phase3_head": "audit HEAD 是否就是 phase3 分支 HEAD",
        "hotfix_head_is_ancestor_of_p3": "hotfix HEAD 是否是当前 P3 分支（HEAD）的祖先",
        "p3_head_on_main": "main 是否是当前 HEAD 的祖先",
    }
    lines.append("关键祖先判断（git merge-base --is-ancestor）：")
    lines.append("")
    for key, desc in labels.items():
        if key in matrix["ancestry"]:
            lines.append("- %s：%s" % (desc, "**是**" if matrix["ancestry"][key] else "**否**"))
    lines.append("")
    lines.append("两条线改动的**重叠文件**（决定合并冲突面，由 diff --name-only 集合求交）：")
    lines.append("")
    if matrix.get("overlap_files"):
        for path in matrix["overlap_files"]:
            lines.append("- %s" % path)
    else:
        lines.append("- 无")
    lines.append("")
    hotfix_only = [p for p in matrix.get("hotfix_only_files", []) if p.startswith("web/")
                   or p.startswith("cellflow/")]
    if hotfix_only:
        lines.append("hotfix 独有的代码文件（audit 分支上不存在）：%s" %
                     "、".join(hotfix_only))
        lines.append("")

    lines.append("## 4. 结论：P3 应以哪个 commit 为基线")
    lines.append("")
    hotfix = matrix["branches"].get("hotfix", {})
    audit = matrix["branches"].get("audit", {})
    p3_entry = matrix["branches"].get("p3", {})
    p3_ahead_hotfix = int(git_out("rev-list", "--count",
                                  "origin/hotfix/public-data-download-connectivity..HEAD") or 0)
    lines.append("**建议：以 hotfix 线的 tip %s 为 P3 集成基线（当前 P3 分支 HEAD 是在它之上再走 "
                 "%d 个提交：%s）；并且必须在继续写 P3 代码前，把 %s"
                 "（audit/phase2-review-20261026）合并一次进 P3 分支。**" % (
                     hotfix.get("sha_short", "?"), p3_ahead_hotfix,
                     p3_entry.get("sha_short", "?"), audit.get("sha_short", "?")))
    lines.append("")
    lines.append("一句话答案：**P3 继续开发的基线是 %s（当前 P3 分支 HEAD，= hotfix tip %s + "
                 "%d 个 P3 提交）；要让基线同时包含契约/证据层，必须先合入 %s 再继续。**" % (
                     p3_entry.get("sha_short", "?"), hotfix.get("sha_short", "?"),
                     p3_ahead_hotfix, audit.get("sha_short", "?")))
    lines.append("")
    lines.append("可核查的理由：")
    lines.append("")
    lines.append("1. 下载修复只在 hotfix 线上。ancestry 检查显示 1ab49e0 在 audit 分支里：**%s**；"
                 "web/src/services/net.ts、web/src/services/download-monitor.ts、"
                 "tests/test_download_hotfix.py、web/tests/net.test.mjs 只存在于 hotfix 树。"
                 "P3 batch1 的主题就是数据获取，从 audit 起手会丢掉已验收的修复。" % (
                     "是" if matrix["ancestry"].get("hotfix_fix_in_audit") else "否"))
    lines.append("2. 契约/证据层只在 audit 线上：audit 领先 main %d 个提交、%d 个变更文件"
                 "（%s）；hotfix 只有 %d 个提交。两者都不能省。" % (
                     audit.get("commits_ahead_of_base", 0), audit.get("changed_files", 0),
                     audit.get("diff_shortstat_vs_base", ""),
                     hotfix.get("commits_ahead_of_base", 0)))
    lines.append("3. 两条线真正的分叉点是 main %s：彼此不包含（各自独有提交 %s），"
                 "重叠改动只有 %d 个文件（%s），冲突面可控。" % (
                     matrix["base_sha"][:12],
                     "、".join("%d" % pair["a_only"] for pair in matrix["pairwise"]
                              if pair["a"].endswith("hotfix/public-data-download-connectivity")),
                     len(matrix.get("overlap_files") or []),
                     "、".join(matrix.get("overlap_files") or []) or "无"))
    lines.append("4. P3 分支已经建在 hotfix tip 上，改用 audit 起手会让已有分支作废；而 audit 的 "
                 "tip 已经被远端 phase3/cytelle-rebrand-and-validation 占用，把 P3 建在那里会把 "
                 "phase2 审计与 phase3 品牌工作混到同一条线上。")
    lines.append("5. main 至今停在 %s，PR #1–#4 全部是 DRAFT、一个都没合并；"
                 "所以“以 main 为基线”会同时丢掉上面两层，不成立。" % matrix["base_sha"][:12])
    lines.append("")
    lines.append("不合并 audit 就开工，等于显式接受这些风险：")
    lines.append("")
    lines.append("- P3 产出的证据/导入/进程代码会与 PR #1–#3 的契约重复实现或直接冲突，"
                 "评审时无法判定先后；")
    lines.append("- 重叠的前端文件会在最后合并时必然冲突，且冲突点正落在 P3 要改的下载 UI 上；")
    lines.append("- audit 线上的 benchmarks/、cellflow/evidence.py 等在 P3 分支上不存在，"
                 "任何引用它们的验收脚本在 P3 分支上会直接失败。")
    lines.append("")
    lines.append("执行建议（由 Lead 决策；本审计不做任何 git 写操作）：")
    lines.append("")
    lines.append("    # 在 p3/batch1-data-acquisition 上合并契约/审计线；产生的 merge commit 就是 P3 真实基线")
    lines.append("    git merge --no-ff origin/audit/phase2-review-20261026")
    lines.append("    # 预计冲突：web/package.json、web/src/services/api.ts、web/src/views/Data.tsx")
    lines.append("")
    lines.append("无论是否合并，P3 报告里的「基线提交」都应写 40 位 SHA，不要只写分支名——"
                 "本仓库的 main 就停在 %s 没动。" % matrix["base_sha"])
    lines.append("")
    lines.append("## 5. 各分支相对 main 的差异规模")
    lines.append("")
    rows = []
    for name, entry in matrix["branches"].items():
        sample = entry.get("changed_file_names", [])[:4]
        rows.append([name, entry["sha_short"], entry["changed_files"],
                     entry["diff_shortstat_vs_base"] or "-",
                     ("、".join(sample) + " …") if sample else "-"])
    lines.append(_md_table(["分支", "HEAD", "变更文件数", "shortstat", "变更文件样例"], rows))
    lines.append("")
    return "\n".join(lines)


def build_known_failures_md(ctx):
    tests = ctx["tests"]
    lines = ["# P3-00 基线审计：已知故障清单", ""]
    lines.append("- 生成时间：%s" % GENERATED_AT)
    lines.append("- 依据：docs/hotfix-2026-10-26-public-data-download.md 第 10 节、"
                 "tools/_hotfix/ENV_REPORT.md、本次实测（见 test_baseline.json）。")
    lines.append("- 口径：**已修**＝有提交且被测试覆盖；**未修**＝确认存在未改；"
                 "**未验证**＝没有证据，不下结论。")
    lines.append("")

    lines.append("## 1. 本次 hotfix 已修（f688fc7 树上）")
    lines.append("")
    rows = [
        ["F1", "前端不区分失败层级：TypeError: Failed to fetch 被原样拼进界面",
         "已修", "web/src/services/net.ts（requestJson / classifyFailure）",
         "web/tests/net.test.mjs 覆盖失败分类 3 项"],
        ["F2", "轮询一次异常就丢任务：setInterval + 任意异常即 forgetDownload()",
         "已修", "web/src/services/download-monitor.ts（轮询状态机）",
         "net.test.mjs：单次失联不丢 / 连续失联只停轮询 / 404 才清除"],
        ["F3", "后端幽灵任务：_run() 准备工作在 try 之外，失败后永远停在 queued",
         "已修", "cellflow/downloads.py（准备阶段移入异常处理 + _fail()）",
         "tests/test_download_hotfix.py（旧代码上 'queued' != 'failed'）"],
        ["F4", "非幂等 POST 在传输层失败后被重发，可能建两个下载任务",
         "已修", "web/src/views/Data.tsx（改用幂等 GET /api/downloads 对账）",
         "net.test.mjs 对账用例"],
    ]
    lines.append(_md_table(["编号", "现象", "状态", "位置", "证据"], rows))
    lines.append("")

    lines.append("## 2. 仍未修（本次确认）")
    lines.append("")
    rows = [
        ["NF1", "CELLFLOW_PORT 不存在，端口在 3 处硬编码 8600",
         "cellflow_app.pyw、cellflow/desktop_qt.py（LocalPage）、cellflow/api.py::__main__",
         "git grep -n 8600 可复现；hotfix 报告第 10 节第 4 条"],
        ["NF2", ".github/workflows/ci.yml 的 npm test 步骤未提交",
         ".github/workflows/ci.yml（工作区未提交改动）",
         "gh 令牌只有 gist/read:org/repo，推送 workflow 文件被 GitHub 拒绝 → 前端回归在 CI 里实际未启用"],
        ["NF3", "复制 venv 的 Scripts/*.exe 与发行版不一致（PE subsystem=3）",
         ".venv/Scripts/pythonw.exe 等",
         "tests/test_desktop.py 2 项失败：3 != 2、重定向器字节比对不一致；"
         "install.py::repair_gui_python() 存在就是为了修这个"],
        ["NF4", "veloVI 隔离环境在本仓库缺失",
         ".venv-velovi（不存在）",
         "相关测试 skipped；见 dependency_inventory.json.isolated_envs.velovi"],
        ["NF5", "web/dist 未入库（gitignore），部署/安装前必须构建",
         "web/dist、.gitignore",
         "git check-ignore -v web/dist 命中 web/dist/；install.py --check 依赖 web/dist/index.html"],
        ["NF6", "远端 phase3/cytelle-rebrand-and-validation 指向 audit 提交",
         "远端分支",
         "见 branch_matrix.md 第 1、3 节（merge-base --is-ancestor 与 ls-remote 证据）"],
    ]
    lines.append(_md_table(["编号", "问题", "位置", "复现 / 证据"], rows))
    lines.append("")

    lines.append("## 3. hotfix 报告「未验证项」逐条落账")
    lines.append("")
    rows = [
        ["用户那次 Failed to fetch 的确切触发方式", "未验证",
         "现场日志被旧构建覆盖；本次只修了“本机服务不可达/连接中断”这一类路径"],
        ["GSE181411 在线验收", "未验证", "hotfix 只端到端验了 GSE72857"],
        ["CELLxGENE 数据源", "未验证", "本次审计未跑该源"],
        ["Qt 验收是否走 cellflow_app.pyw 完整入口", "未验证",
         "当时用自建脚本 + uvicorn；建议真实安装的 .venv 下再点一次"],
        ["“服务中途挂掉再恢复”双进程端到端", "未验证", "前端只有单元测试覆盖"],
        ["复制 venv 缺 httpx2 导致 9 个 API 测试未执行", "已实测",
         "见 test_baseline.json：安装前后的对照数字"],
        ["web/dist 未入库", "确认", "部署前必须 npm run build"],
    ]
    lines.append(_md_table(["项目", "状态", "说明"], rows))
    lines.append("")

    lines.append("## 4. 本次审计新发现的工程约束/故障")
    lines.append("")
    rows = [
        ["NF7", "Python 子进程只能写工作区内部，写 %TEMP% 报 PermissionError",
         "本机执行环境（DSH 文件沙箱）",
         "复现：.venv\\Scripts\\python.exe -c \"open(r'%TEMP%\\\\x.json','w')\" → "
         "PermissionError: [Errno 13]；写仓库内目录正常。副作用：tempfile.gettempdir() 回落到 cwd，"
         "任何把中间产物写进 %TEMP% 的脚本在本机都会失败"],
        ["NF8", "并行任务共享同一工作副本，HEAD 与 git status 随时在变",
         "仓库工作区",
         "审计开始时 HEAD=f688fc7，采集期间被并行任务推进到 %s；未提交改动也从 1 项涨到 8+ 项。"
         "因此本审计把后端测试改到 HEAD 导出副本里跑（不受工作区改动影响）" % (
             git_out("rev-parse", "--short", "HEAD"), )],
        ["NF9", "pip install httpx2 首次长时间无输出（两次并发 pip 同时挂起）",
         "本机 venv",
         "改用 --no-cache-dir --timeout 10 --retries 0 -v 后 30 秒内成功；"
         "CI 里是 pip install \"pypdf==6.21.2\" \"httpx2\""],
        ["NF10", "requirements.txt 声明的包并非全部已安装",
         "venv",
         "见 dependency_inventory.json 的 python.requirement_check.missing；"
         "属环境差异，不是产品缺陷，但会让本地验收与 CI 结论不一致"],
        ["NF11", "测试套件隐式依赖 data/、results/、logs/ 三个目录已存在（全新 clone 里没有）",
         "tests/test_pipeline.py 的 2 个 auto 用例 / cellflow 运行时目录",
         "实测：HEAD 的干净导出（无这三个 gitignore 目录）跑 "
         "tests.test_pipeline → 2 个 auto 用例失败（'fate' not found / KeyError: 'fate'）；"
         "补建空的 data/results/logs 后同一代码 11 个用例通过（1 skipped）。"
         "具体被吞掉的异常未定位（未验证）——但这意味着 fresh clone 直接跑 unittest 会红"],
    ]
    lines.append(_md_table(["编号", "问题", "位置", "复现 / 证据"], rows))
    lines.append("")

    lines.append("## 5. 与测试基线的对应")
    lines.append("")
    for run in tests["backend"]["runs"]:
        if "collected" not in run:
            continue
        counts = run.get("counts") or {}
        lines.append("- %s：collected=%s，passed=%s，failed=%s，errors=%s，skipped=%s" % (
            run["label"], run.get("collected"), counts.get("passed"),
            counts.get("failed"), counts.get("errors"), counts.get("skipped")))
    for run in tests["frontend"]["runs"]:
        lines.append("- 前端 %s：exit=%s（%s）" % (
            run["label"], run.get("exit_code"),
            "通过" if run.get("passed") else "失败"))
    lines.append("")
    lines.append("逐条失败/跳过的分类见 test_baseline.json 的 failure_analysis。")
    lines.append("")
    return "\n".join(lines)


def build_test_baseline(ctx):
    tests = ctx["tests"]
    payload = {
        "generated_at": GENERATED_AT,
        "repo": str(ROOT),
        "head": ctx["git"].get("head"),
        "head_short": ctx["git"].get("head_short"),
        "branch": ctx["git"].get("branch"),
        "run_mode": ctx["args"].run_mode,
        "backend": tests["backend"],
        "frontend": tests["frontend"],
        "environment_notes": list(dict.fromkeys(tests["environment_notes"])),
        "failure_analysis": [],
    }
    categories = {
        "environment_missing_httpx2": {
            "category": "environment",
            "reason": "starlette.testclient 需要 httpx2；复制来的 venv 未安装（CI 会 pip install httpx2）。"
                      "装上 httpx2 后同一批用例全部通过，因此归为环境缺失而不是代码缺陷"},
        "environment_missing_pypdf": {
            "category": "environment",
            "reason": "pypdf 未列入 requirements.txt，CI 单独安装 pypdf==6.21.2"},
        "environment_missing_velovi": {
            "category": "environment",
            "reason": "本仓库缺 .venv-velovi 隔离环境（scvi-tools 1.2）"},
        "environment_copied_venv": {
            "category": "environment",
            "reason": "复制 venv 的 Scripts/*.exe 与官方发行版不一致（PE subsystem / 字节不同）"},
        "real_defect": {"category": "defect", "reason": "真实缺陷（非环境缺失）"},
    }
    # 收集每个失败/跳过用例在各轮里的状态，按用例聚合（避免同一用例重复列两次）
    aggregate = {}
    order = []
    for run in tests["backend"]["runs"]:
        label = run.get("label")
        for record in run.get("records") or []:
            if record["status"] == "passed":
                continue
            if record["id"] not in aggregate:
                aggregate[record["id"]] = {"statuses": {}, "detail": "", "runs": []}
                order.append(record["id"])
            entry = aggregate[record["id"]]
            entry["statuses"][label] = record["status"]
            entry["runs"].append(label)
            if not entry["detail"]:
                entry["detail"] = (record.get("detail") or "").replace("\n", " ")

    passed_after = set()
    for run in tests["backend"]["runs"]:
        if run.get("label") == "after_httpx2":
            passed_after = {rec["id"] for rec in (run.get("records") or [])
                            if rec["status"] == "passed"}

    for test_id in order:
        entry = aggregate[test_id]
        status = "failed" if "failed" in entry["statuses"].values() else "error"
        if all(st == "skipped" for st in entry["statuses"].values()):
            status = "skipped"
        detail = entry["detail"]
        detail_lower = detail.lower()
        is_loader = "unittest.loader._FailedTest" in test_id
        # 模块级 import 失败（unittest.loader._FailedTest）：模块名形如 tests.test_xxx；
        # 若该模块的用例在 after_httpx2 对照轮里全部通过，则失败原因是缺 httpx2（模块级 import TestClient）。
        loader_module = test_id.split("unittest.loader._FailedTest.")[-1] if is_loader else None
        loader_module_ok_after = bool(loader_module) and any(
            rec_id.startswith(loader_module + ".") for rec_id in passed_after)
        only_fails_without_httpx2 = (
            is_loader or status in ("error", "skipped")) and test_id in passed_after
        if "httpx2" in detail_lower or only_fails_without_httpx2 or loader_module_ok_after:
            kind = "environment_missing_httpx2"
        elif "pypdf" in detail_lower:
            kind = "environment_missing_pypdf"
        elif "velovi" in detail_lower or "scvi" in detail_lower:
            kind = "environment_missing_velovi"
        elif ("pythonw.exe" in detail_lower or "redirector" in test_id
              or "subsystem" in detail_lower or "desktop" in test_id):
            kind = "environment_copied_venv"
        elif status == "skipped":
            kind = "environment_missing_velovi"
        else:
            kind = "real_defect"
        payload["failure_analysis"].append({
            "test": test_id, "status": status,
            "status_by_run": entry["statuses"],
            "category": categories[kind]["category"],
            "classification": kind,
            "reason": categories[kind]["reason"],
            "detail": detail[:800],
            "seen_in_runs": entry["runs"],
        })

    # 各轮计数自检：passed+failed+errors+skipped 是否等于 collected
    for run in tests["backend"]["runs"]:
        counts = run.get("counts") or {}
        if not run.get("collected") or not counts:
            continue
        total = sum(counts.get(key, 0) for key in ("passed", "failed", "errors", "skipped"))
        run["arithmetic_check"] = {
            "passed+failed+errors+skipped": total,
            "collected": run["collected"],
            "ok": total == run["collected"],
            "note": ("类级 setUpClass 跳过会额外产生 1 条记录，不计入上面四项"
                     if total != run["collected"] else ""),
        }
    return payload


def build_data_safety(ctx):
    storage = ctx["storage"]
    payload = {
        "generated_at": GENERATED_AT,
        "repo": str(ROOT),
        "policy": {
            "never_commit": ["data/", "results/", "logs/", ".venv/", ".venv-*/",
                             "web/node_modules/", "web/dist/", "installation.json",
                             "*.sqlite", "*.sqlite3", "*.log", ".env", ".env.*",
                             "*.key", "*.pem", "sample-run/", "verification/",
                             "tools/_hotfix/", "tmp*/", "pip-*/"],
            "rules_source": str(ROOT / ".gitignore"),
        },
        "copies": storage["copies"],
        "gitignore": storage["gitignore"],
        "user_data": storage["user_data"],
        "large_files": storage["large_files"],
        "notes": [],
    }
    notes = payload["notes"]
    notes.append("数据根在仓库内部：HEAD 的 cellflow/workspace.py 里 "
                 "ROOT = Path(__file__).resolve().parents[1]，所以 data/、results/、logs/ 都落在"
                 "仓库目录下，全部被 .gitignore 覆盖，绝不能提交。")
    notes.append("data/ 里是真实科研数据（含 GSE72857 的下载产物），删除或移动需用户确认；"
                 "本审计只读。")
    notes.append("results/queue.sqlite 是任务队列库（jobs/supervisor 表），"
                 "results/projects.json 是用户项目清单，data/public/.downloads/state.json 是下载任务状态。")
    user = storage["user_data"]
    if user.get("projects_json"):
        notes.append("用户项目数：%d（active=%s）。" % (
            user["projects_json"]["project_count"], user["projects_json"]["active"]))
    if user.get("jobs"):
        notes.append("queue.sqlite 各表行数：%s。" % user["jobs"]["row_counts"])
    if user.get("downloads"):
        notes.append("下载任务记录：%d 条，状态分布 %s，数据集 %s。" % (
            user["downloads"]["task_count"], user["downloads"]["states"],
            user["downloads"]["identifiers"]))
    return payload


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(description="CellFlow P3-00 基线审计生成器")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--compare-httpx2", action="store_true")
    parser.add_argument("--run-mode", choices=("export", "worktree"), default="export")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--reuse-existing", action="store_true",
                        help="复用 docs/p3/test_baseline.json 里已有的测试结果，不重新执行测试")
    parser.add_argument("--internal-runner", nargs=2, metavar=("REPO", "OUT"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.internal_runner:
        repo, out = args.internal_runner
        namespace = {"__name__": "__main__", "__file__": out}
        saved = sys.argv[:]
        sys.argv = [out, repo, out]
        try:
            exec(compile(INTERNAL_RUNNER_SRC, out, "exec"), namespace)  # noqa: S102
        finally:
            sys.argv = saved
        return 0

    work_dir = Path(args.work_dir)
    out_dir = Path(args.out_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    phase("[1/6] git 状态 ...")
    git_info = collect_git()
    phase("[1/6] 远端分支 ...")
    refs = collect_refs()
    phase("[1/6] merge-base / 提交计数 ...")
    matrix = collect_branch_matrix()
    phase("[1/6] PR 列表（gh）...")
    prs = collect_prs()

    phase("[2/6] 环境与依赖 ...")
    env = collect_env()
    deps = collect_deps(env)

    phase("[3/6] 本地工作副本与提交比对 ...")
    copy_diffs = collect_copy_diffs()

    phase("[4/6] 数据资产 ...")
    storage = collect_storage()

    phase("[5/6] 测试 ...")
    tests = collect_tests(args, work_dir)

    ctx = {"git": git_info, "refs": refs, "branch": {"refs": refs, "matrix": matrix},
           "prs": prs, "env": env, "deps": deps, "copy_diffs": copy_diffs,
           "storage": storage, "tests": tests, "args": args}

    print("[6/6] 写出基线文件 ...")
    outputs = {}
    for name, builder in (("baseline_inventory.md", build_inventory_md),
                          ("branch_matrix.md", build_branch_matrix_md),
                          ("known_failures.md", build_known_failures_md)):
        path = out_dir / name
        path.write_text(builder(ctx), encoding="utf-8")
        outputs[name] = path
    outputs["dependency_inventory.json"] = write_json(
        out_dir / "dependency_inventory.json",
        dict(deps, repo=str(ROOT), head=git_info.get("head"),
             branch=git_info.get("branch")))
    outputs["test_baseline.json"] = write_json(out_dir / "test_baseline.json",
                                               build_test_baseline(ctx))
    outputs["data_safety_inventory.json"] = write_json(out_dir / "data_safety_inventory.json",
                                                       build_data_safety(ctx))

    print("")
    print("生成完成：")
    for name, path in outputs.items():
        size = path.stat().st_size if path.exists() else -1
        print("  - %-30s %s (%d bytes)" % (name, path, size))

    if not args.keep_work and args.run_mode == "export":
        shutil.rmtree(work_dir / "head-export", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

