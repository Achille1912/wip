"""Run engine: notes -> agent in an isolated git worktree -> diff per file group + targeted tests.
Usage: run.py <repo> <blueprint.js> <state dir> run <prompt file> | fix | check <group,group> | apply <group,group> | discard
Nothing touches the repo's working tree until `apply`; .env files are never copied into the worktree."""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import run_agent
from archplan import check_plan, fmt, title
from extract import blueprint, code_files, py_files

repo, bp_file, state, cmd = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve(), sys.argv[4]
run_dir, wt, out = state / "run", state / "run" / "wt", state / "run.json"


def git(*a, cwd=repo, check=True):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {a[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def files_of(root):
    """{file: line count} of the project's code files (package markers excluded): module-level changes of a split or a merge."""
    fs = code_files(root)  # the same files the blueprint's components are made of
    return {p.relative_to(root).as_posix(): len(p.read_text(encoding="utf-8-sig", errors="replace").splitlines()) for p in fs if p.name != "__init__.py"}


def extract(root):
    bp = blueprint(root)  # the same extraction that builds the blueprint, whatever the layout
    return (bp["structure"]["nodes"], bp["structure"]["edges"]), (bp["pipeline"]["nodes"], bp["pipeline"]["edges"]), files_of(root)


def arch_atoms(a, b):
    """Architecture changes between two extractions, as atoms (see archplan): packages and their dependencies, components, flows,
    data keys each component reads/writes, and the Python modules that appeared or disappeared."""
    (sn0, se0), (pn0, pe0), f0 = a
    (sn1, se1), (pn1, pe1), f1 = b
    out = []

    def sets(kind, x, y):
        out.extend(("+", kind, *i) for i in sorted(y - x))
        out.extend(("-", kind, *i) for i in sorted(x - y))

    sets("package", {(n["id"],) for n in sn0}, {(n["id"],) for n in sn1})
    sets("dep", {(e["source"], e["target"]) for e in se0}, {(e["source"], e["target"]) for e in se1})
    sets("component", {(n["id"],) for n in pn0}, {(n["id"],) for n in pn1})
    sets("flow", {(e["source"], e["target"]) for e in pe0}, {(e["source"], e["target"]) for e in pe1})
    sets("module", {(f,) for f in f0}, {(f,) for f in f1})
    old = {n["id"]: n for n in pn0}
    for n in pn1:
        for k in ("writes", "reads") if n["id"] in old else ():
            sets(k, {(n["id"], x) for x in old[n["id"]][k]}, {(n["id"], x) for x in n[k]})
    return out


def groups_of(files):
    """One group per changed file; it names the blueprint nodes living in that file (several nodes can share one)."""
    bp = json.loads(Path(bp_file).read_text(encoding="utf-8").strip().removeprefix("const BP = ").removesuffix(";"))
    nodes = {}
    for n in bp["pipeline"]["nodes"]:
        nodes.setdefault(n.get("file"), []).append(n["id"])
    return [{"id": re.sub(r"\W", "_", f), "files": [f], "nodes": nodes.get(f, [])} for f in files]


def tests_for(files):
    """Test files that are changed, or mention the module of a changed file. ponytail: text match, cap 15; a generic module name over-selects."""
    tests = {f for f in files if re.search(r"(^|/)test[^/]*\.py$", f)}
    stems = [Path(f).stem for f in files if f.endswith(".py") and f not in tests]
    for t in sorted((wt / "test").rglob("test*.py")) if (wt / "test").is_dir() else []:
        text = t.read_text(encoding="utf-8", errors="replace")
        if any(re.search(rf"\b{re.escape(s)}\b", text) for s in stems):
            tests.add(t.relative_to(wt).as_posix())
    return sorted(tests)[:15]


def test_python():
    """(python, None) for the project's own environment, found from the real repo (a worktree path would make pipenv create a new one),
    or (None, why) when the project declares one that does not exist yet: the tool never installs anything."""
    venv = None
    for marker, probe, hint in (("Pipfile", ["pipenv", "--venv"], "pipenv install --dev"), ("poetry.lock", ["poetry", "env", "info", "-p"], "poetry install")):
        if (repo / marker).exists():
            try:
                r = subprocess.run(probe, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                venv = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else None
            except (OSError, subprocess.TimeoutExpired):
                venv = None
            if not venv:
                return None, f"the project has no environment yet: run `{hint}` in {repo}"
            break
    venv = venv or next((str(repo / d) for d in (".venv", "venv") if (repo / d).is_dir()), None)
    if not venv:
        return sys.executable, None  # ponytail: no declared environment, try the tool's own Python
    return str(Path(venv) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")), None


def config():
    f = Path(bp_file).parent / "config.json"  # one per repo: {"test": "shell command run in the copy instead of pytest"}
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def npm_test():
    """'npm test' for a JS project with a test script and no Python test setup."""
    if any((repo / m).exists() for m in ("pytest.ini", "Pipfile", "poetry.lock", "setup.cfg")):
        return None
    try:
        return "npm test --silent" if json.loads((repo / "package.json").read_text(encoding="utf-8")).get("scripts", {}).get("test") else None
    except (OSError, ValueError):
        return None


def unlink_deps(w):
    """Removes the borrowed node_modules link (only the link: the repo's own folder is never touched)."""
    nm = w / "node_modules"
    if os.path.isjunction(nm):
        os.rmdir(nm)  # on a junction, rmdir removes the link itself
    elif nm.is_symlink():
        nm.unlink()


def rm_wt(w):
    unlink_deps(w)  # first, so removing the copy can never walk into the repo's node_modules
    git("worktree", "remove", "--force", str(w), check=False)


def run_tests(tests, root):
    custom = config().get("test") or npm_test()
    node = bool(custom) and (repo / "node_modules").is_dir() and not (root / "node_modules").exists()
    if node:  # node_modules is not in git: the copy borrows the repo's while the tests run, and only then
        nm = root / "node_modules"
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(nm), str(repo / "node_modules")], capture_output=True)
        else:
            os.symlink(repo / "node_modules", nm)
    try:
        return _run_tests(tests, root, custom)
    finally:
        if node:
            unlink_deps(root)


def _run_tests(tests, root, custom):
    if not tests and not custom:
        return {"status": "none", "detail": "no test covers these files", "files": []}
    if custom:
        cmd, shell = custom, True
    else:
        py, why = test_python()
        if not py:
            return {"status": "unrunnable", "detail": why, "files": tests}
        # -o addopts="" : the project's own options need plugins and write report files
        cmd, shell = [py, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", "-o", "addopts=", *tests], False
    try:
        r = subprocess.run(cmd, shell=shell, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return {"status": "unrunnable", "detail": "tests timed out after 10 minutes", "files": tests}
    last = (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:] or f"exit code {r.returncode}"])[-1]
    if custom:
        status, last = "green" if r.returncode == 0 else "red", f"{custom}: {last}"
    else:  # red only if pytest itself reports failed or errored tests ("python -m pytest" without pytest also exits 1). An import error counts:
        # one caused by the environment shows up before the run too, and the viewer then reads it as "already failing before"
        status = "green" if r.returncode == 0 else "red" if r.returncode in (1, 2) and re.search(r"\d+ (failed|errors?)\b", r.stdout) else "unrunnable"
    return {"status": status, "detail": last, "files": tests, **({"output": (r.stdout + r.stderr)[-3000:]} if status == "red" else {})}  # what "Fix" sends back


def before_after(tests, base, root=None):
    """The same tests on the untouched code and on the run's result (or root): a red test that was already red is not the run's fault."""
    after = run_tests(tests, root or wt)
    old = [t for t in tests if (base / t).exists()]  # a test file the run created has no "before"
    after["before"] = run_tests(old, base) if old or config().get("test") or npm_test() else {"status": "none", "detail": "new tests", "files": []}
    return after


def discard(keep_plan=False):
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt, check=False).strip() if wt.is_dir() else ""  # only this view's run branch
    for w in (wt, run_dir / "base", run_dir / "sel"):  # base, sel: left behind only if a run or a check was killed while testing
        rm_wt(w)
    git("worktree", "prune", check=False)
    if branch.startswith("blueprint/run-"):
        git("branch", "-D", branch, check=False)
    for p in run_dir.glob("*.patch"):
        p.unlink()
    out.unlink(missing_ok=True)
    if not keep_plan:  # an approved plan lives until its run is applied or discarded
        (state / "plan.json").unlink(missing_ok=True)


def agent(prompt, on_fail):
    try:  # tool use the engineer has not pre-approved waits for an answer in the viewer (see agent.py)
        return asyncio.run(asyncio.wait_for(run_agent(prompt, wt, state), 3600))
    except Exception as e:
        on_fail()
        raise RuntimeError(str(e)[:300] or type(e).__name__)


def finish(base, dirty, summary, permissions, plan):
    """Everything after the agent: file groups with their patches, tests before/after, and the architecture change against the base."""
    git("add", "-A", cwd=wt)
    base_wt = run_dir / "base"
    rm_wt(base_wt)
    git("worktree", "add", "--detach", str(base_wt), base)  # the untouched code: "before" for the tests and for the architecture
    try:
        arch = conformance = None
        try:
            before, after = extract(base_wt), extract(wt)
            atoms = arch_atoms(before, after)
            arch = [fmt(a) for a in atoms]
            conformance = check_plan(plan["items"], atoms, after[2]) if plan else None
        except Exception as e:  # e.g. the agent left the graph builder unparsable
            arch = [f"! The blueprint could not be extracted from the changed code: {str(e)[:150]}"]
        groups = groups_of(git("diff", "--cached", "--name-only", cwd=wt).split())
        for p in run_dir.glob("*.patch"):
            p.unlink()
        for g in groups:
            patch = git("diff", "--cached", "--binary", "--", *g["files"], cwd=wt)
            (run_dir / f'{g["id"]}.patch').write_text(patch, encoding="utf-8", newline="")
            g.update(title=", ".join(title(n) for n in g["nodes"]) or g["files"][0], diff=patch[:15000], tests=before_after(tests_for(g["files"]), base_wt))
    finally:
        rm_wt(base_wt)
    out.write_text(json.dumps({"base": base, "dirty": dirty, "summary": summary.strip()[:600], "permissions": permissions, "arch": arch, "plan": plan and {"summary": plan["summary"], "lines": plan["lines"]}, "conformance": conformance, "groups": groups}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(groups)} file group(s) changed")


def run(prompt_file):
    discard(keep_plan=True)
    busy = [p.parent.parent.parent.name for p in Path(bp_file).parent.glob("views/*/.pipeline-viewer/run/wt") if p.resolve() != wt.resolve()]
    if busy:  # one run per repo: two runs would start from the same commit and could not both be applied
        raise RuntimeError(f"a run is already open in the {busy[0]} view of this repo: apply or discard it there first")
    plan = json.loads((state / "plan.json").read_text(encoding="utf-8")) if (state / "plan.json").exists() else None
    run_dir.mkdir(parents=True, exist_ok=True)
    dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    base = git("rev-parse", "--short", "HEAD").strip()
    git("worktree", "add", "-B", f"blueprint/run-{state.parent.name}-{base}", str(wt), "HEAD")  # -B: a branch left by a killed run is reused
    summary, permissions = agent(Path(prompt_file).read_text(encoding="utf-8"), lambda: discard(keep_plan=True))  # the plan survives a failed run
    finish(base, dirty, summary, permissions, plan)


def fix():
    """Sends the tests this run broke back to the agent, in the same copy, then redoes diffs and tests. The earlier work is kept on failure."""
    res = json.loads(out.read_text(encoding="utf-8"))
    broken = [g for g in res["groups"] if g["tests"]["status"] == "red" and g["tests"].get("before", {}).get("status") != "red"]
    if not broken:
        raise RuntimeError("no test broken by this run")
    prompt = ("Your previous change in this repository made these tests fail. Fix the code so they pass again, keeping what the change was meant to do. "
              "Change a test only if the test itself is wrong, and say so.\n\n"
              + "\n\n".join(f'Changed file(s): {", ".join(g["files"])}\nTests: {", ".join(g["tests"]["files"])}\nOutput:\n{g["tests"].get("output", g["tests"]["detail"])}' for g in broken))
    plan = json.loads((state / "plan.json").read_text(encoding="utf-8")) if (state / "plan.json").exists() else None
    summary, permissions = agent(prompt, lambda: None)
    finish(res["base"], res["dirty"], res["summary"] + "\nFix: " + summary.strip(), res["permissions"] + permissions, plan)


def check(ids):
    """Only the selected file groups on the base commit, with their tests before and after: leaving a file out can break the others."""
    res = json.loads(out.read_text(encoding="utf-8"))
    sel = [g for g in res["groups"] if g["id"] in ids]
    sw, bw = run_dir / "sel", run_dir / "base"
    for w in (sw, bw):
        rm_wt(w)
        git("worktree", "add", "--detach", str(w), res["base"])
    try:
        for g in sel:
            git("apply", "--binary", str(run_dir / f'{g["id"]}.patch'), cwd=sw)
        tests = sorted({t for g in sel for t in g["tests"]["files"] if (sw / t).exists()})  # a new test file left out is not part of this selection
        res["selection"] = {"groups": sorted(g["id"] for g in sel), **before_after(tests, bw, sw)}
    finally:
        for w in (sw, bw):
            rm_wt(w)
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f'selection of {len(sel)} group(s): {res["selection"]["status"]}')


def apply(ids):
    res = json.loads(out.read_text(encoding="utf-8"))
    patches = [run_dir / f'{g["id"]}.patch' for g in res["groups"] if g["id"] in ids]
    flags = ["--binary", "--ignore-whitespace"]
    for p in patches:  # check all first, so a conflict leaves the working tree untouched
        git("apply", "--check", *flags, str(p))
    for p in patches:
        git("apply", *flags, str(p))
    discard()
    print(f"applied {len(patches)} group(s)")


try:
    {"run": lambda: run(sys.argv[5]), "fix": fix, "check": lambda: check(set(sys.argv[5].split(","))), "apply": lambda: apply(set(sys.argv[5].split(",")) if len(sys.argv) > 5 else set()), "discard": discard}[cmd]()
except Exception as e:
    sys.exit(str(e))
