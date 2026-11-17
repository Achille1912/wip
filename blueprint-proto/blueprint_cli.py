"""blueprint <repo>: refreshes the blueprint only if the code changed, starts a viewer per view on free ports, opens the browser.
Generated views and state live in blueprint-proto/work/<repo name>/views/<pipeline|struttura>/.
No LLM, no viewer, for CI or a hook: --diff <ref> prints the architecture changes since <ref>; --check exits 1 on a broken rule."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BLUEPRINT_DATA", HERE))  # the packaged app sets it: the code folder is read-only there
VIEWER = HERE.parent / "viewer-kit" / "pipeline-viewer"
PY = sys.executable


def fingerprint(repo):
    """Hash of path, size and mtime of every file git sees (tracked and untracked, not ignored); None outside git."""
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True)
    if r.returncode:
        return None  # ponytail: not a git repo, so refresh every launch; walk the tree if non-git repos matter
    h = hashlib.sha256()
    for f in sorted(set(r.stdout.decode("utf-8", "replace").split("\0")) - {""}):
        try:
            st = (repo / f).stat()
        except OSError:
            continue  # deleted but still in the index
        h.update(f"{f}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def arch_diff(repo, ref):
    """Architecture changes from <ref> to the working tree, one line each (components, dependencies, flows, modules)."""
    from archplan import arch_atoms, extract, fmt
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "base"
        r = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(base), ref], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"git worktree: {r.stderr.strip()[:300]}")
        try:
            return [fmt(x) for x in arch_atoms(extract(base), extract(repo))]
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(base)], capture_output=True)


def check_rules(repo, rule_files):
    """Runs every rule (rules.py check, deterministic) and returns the violations as lines."""
    bad = []
    for f in rule_files:
        r = subprocess.run([PY, str(HERE / "rules.py"), str(repo), str(f), "check"], capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            sys.exit(f"rules check failed on {f}: {r.stderr.strip()[-300:]}")
        bad += [f'{x["text"]}: {v["text"]}' for x in json.loads(r.stdout)["rules"] for v in x["violations"]]
    return bad


def free_port(start):
    for p in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                pass
    sys.exit(f"no free port from {start}")


def cmd(*args):
    return subprocess.list2cmdline([str(a) for a in args])  # the viewer runs these through the shell


def main():
    ap = argparse.ArgumentParser(prog="blueprint", description=__doc__)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--refresh", action="store_true", help="rebuild even if the code did not change")
    ap.add_argument("--port", type=int, default=8766, help="first port to try")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--diff", metavar="REF", help="print the architecture changes from REF (a commit, branch or tag) to the working tree, then exit")
    ap.add_argument("--check", action="store_true", help="check the architecture rules, print the violations, exit 1 if there are any")
    ap.add_argument("--rules", metavar="FILE", action="append", help="rules file for --check (repeatable; default: the rules saved in the viewer)")
    ap.add_argument("--pick", action="store_true", help="choose the repo in a folder dialog, stop from a dialog (the packaged app's double click)")
    a = ap.parse_args()
    if a.pick:
        from tkinter import Tk, filedialog
        Tk().withdraw()
        a.repo = filedialog.askdirectory(title="Blueprint: choose a repository") or sys.exit("no folder chosen")
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a folder: {repo}")
    work = DATA / "work" / repo.name
    out = work / "views"
    if a.diff or a.check:
        if a.diff:
            lines = arch_diff(repo, a.diff)
            print("\n".join(lines) if lines else f"no architecture change since {a.diff}")
        if a.check:
            files = [Path(f) for f in a.rules] if a.rules else sorted(out.glob("*/.pipeline-viewer/rules.json"))
            bad = check_rules(repo, files)
            print("\n".join(["rule violations:"] + bad) if bad else f"{len(files)} rules file(s): no violation")
            sys.exit(1 if bad else 0)
        return
    stamp = work / "fingerprint.txt"
    fp = fingerprint(repo)
    stale = a.refresh or fp is None or not stamp.exists() or stamp.read_text() != fp or not (out / "struttura" / "spec.json").exists()
    if stale:
        print("code changed since the last refresh: rebuilding the blueprint", flush=True)
        if subprocess.run([PY, str(HERE / "refresh.py"), str(repo), str(out)]).returncode:
            sys.exit("refresh failed, see the output above")
    else:
        print("code unchanged since the last refresh: using the saved blueprint", flush=True)

    data = json.loads((work / "blueprint.js").read_text(encoding="utf-8").removeprefix("const BP = ").removesuffix(";"))
    from extract import CODE_EXT
    ext = ",".join(sorted(CODE_EXT))  # same as refresh.py's sealing
    views = (["pipeline"] if data["pipeline"]["nodes"] else []) + ["struttura"]
    procs, urls, port = [], [], a.port
    for v in views:
        state = out / v / ".pipeline-viewer"
        port = free_port(port)
        procs.append(subprocess.Popen([PY, str(VIEWER), str(out / v / "spec.json"), "--port", str(port), "--root", str(repo), "--code-ext", ext,
                                       "--refresh-cmd", cmd(PY, HERE / "refresh.py", repo, out),
                                       "--run-cmd", cmd(PY, HERE / "run.py", repo, work / "blueprint.js", state),
                                       "--rules-cmd", cmd(PY, HERE / "rules.py", repo, state / "rules.json"),
                                       "--ask-cmd", cmd(PY, HERE / "ask.py", repo),
                                       "--plan-cmd", cmd(PY, HERE / "plan.py", repo, state)]))
        urls.append((v, f"http://127.0.0.1:{port}/"))
        port += 1
    for _ in range(100):  # wait until the first viewer answers before opening the browser
        try:
            socket.create_connection(("127.0.0.1", int(urls[0][1].rsplit(":", 1)[1].strip("/"))), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    for v, u in urls:
        print(f"{v:10} {u}", flush=True)
    if not a.no_browser:
        webbrowser.open(urls[0][1])
    print("Ctrl+C stops the viewers", flush=True)
    try:
        if a.pick:  # no terminal to Ctrl+C when double-clicked: the dialog is the stop button
            from tkinter import messagebox
            messagebox.showinfo("Blueprint", f"{repo.name} is open in the browser:\n\n" + "\n".join(u for _, u in urls) + "\n\nOK stops Blueprint.")
            return
        while True:  # not proc.wait(): a viewer restarts itself (execv) when its file changes, which ends the first process
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
