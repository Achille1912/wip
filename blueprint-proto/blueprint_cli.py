"""blueprint <repo>: refreshes the blueprint only if the code changed, starts a viewer per view on free ports, opens the browser.
Generated views and state live in blueprint-proto/work/<repo name>/views/<pipeline|struttura>/."""
import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a folder: {repo}")
    work = HERE / "work" / repo.name
    out = work / "views"
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
    ext = ".py,.tf,.yaml,.yml,.sh,.toml" if data["layout"] == "flat" else ".py"  # same as refresh.py's sealing
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
        while True:  # not proc.wait(): a viewer restarts itself (execv) when its file changes, which ends the first process
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
