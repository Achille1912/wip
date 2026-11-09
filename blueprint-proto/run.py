"""Run engine: notes -> agent in an isolated git worktree -> diff per file group + targeted tests.
Usage: run.py <repo> <blueprint.js> <state dir> run <prompt file> | apply <group,group> | discard
Nothing touches the repo's working tree until `apply`; .env files are never copied into the worktree."""
import asyncio
import json
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


def run_tests(tests):
    if not tests:
        return {"status": "none", "detail": "no test covers these files", "files": []}
    try:  # -o addopts="" : the project's own options need plugins and write report files
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", "-o", "addopts=", *tests],
                           cwd=wt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return {"status": "unrunnable", "detail": "tests timed out after 10 minutes", "files": tests}
    last = (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1]
    # red only if pytest itself reports failed tests: "python -m pytest" without pytest installed also exits 1, and collection/import errors are not failures
    status = "green" if r.returncode == 0 else "red" if r.returncode == 1 and re.search(r"\d+ failed", r.stdout) else "unrunnable"
    return {"status": status, "detail": last, "files": tests}


def discard(keep_plan=False):
    git("worktree", "remove", "--force", str(wt), check=False)
    git("worktree", "prune", check=False)
    for b in git("branch", "--list", "blueprint/run-*", check=False).split():
        if b != "*":
            git("branch", "-D", b, check=False)
    for p in run_dir.glob("*.patch"):
        p.unlink()
    out.unlink(missing_ok=True)
    if not keep_plan:  # an approved plan lives until its run is applied or discarded
        (state / "plan.json").unlink(missing_ok=True)


def run(prompt_file):
    discard(keep_plan=True)
    plan = json.loads((state / "plan.json").read_text(encoding="utf-8")) if (state / "plan.json").exists() else None
    run_dir.mkdir(parents=True, exist_ok=True)
    dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    base = git("rev-parse", "--short", "HEAD").strip()
    git("worktree", "add", "-b", f"blueprint/run-{base}", str(wt), "HEAD")
    try:
        before = extract(wt)
    except Exception:  # not a LangGraph-shaped repo: the run still works, only without the architecture diff
        before = None
    try:  # tool use the engineer has not pre-approved waits for an answer in the viewer (see agent.py)
        summary, permissions = asyncio.run(asyncio.wait_for(run_agent(Path(prompt_file).read_text(encoding="utf-8"), wt, state), 3600))
    except Exception as e:
        discard(keep_plan=True)  # the plan survives a failed run: approve again or discard it
        raise RuntimeError(str(e)[:300] or type(e).__name__)
    git("add", "-A", cwd=wt)
    arch = conformance = None
    try:
        if before:
            after = extract(wt)
            atoms = arch_atoms(before, after)
            arch = [fmt(a) for a in atoms]
            conformance = check_plan(plan["items"], atoms, after[2]) if plan else None
    except Exception as e:  # e.g. the agent left the graph builder unparsable
        arch = [f"! The blueprint could not be extracted from the changed code: {str(e)[:150]}"]
    groups = groups_of(git("diff", "--cached", "--name-only", cwd=wt).split())
    for g in groups:
        patch = git("diff", "--cached", "--binary", "--", *g["files"], cwd=wt)
        (run_dir / f'{g["id"]}.patch').write_text(patch, encoding="utf-8", newline="")
        g.update(title=", ".join(title(n) for n in g["nodes"]) or g["files"][0], diff=patch[:15000], tests=run_tests(tests_for(g["files"])))
    out.write_text(json.dumps({"base": base, "dirty": dirty, "summary": summary.strip()[:600], "permissions": permissions, "arch": arch, "plan": plan and {"summary": plan["summary"], "lines": plan["lines"]}, "conformance": conformance, "groups": groups}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(groups)} file group(s) changed")


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
    {"run": lambda: run(sys.argv[5]), "apply": lambda: apply(set(sys.argv[5].split(",")) if len(sys.argv) > 5 else set()), "discard": discard}[cmd]()
except Exception as e:
    sys.exit(str(e))
