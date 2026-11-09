"""Rebuilds the whole blueprint from the repo: extract -> describe -> detail -> viewer specs (and seals the code as verified).
Usage: refresh.py <repo> <spec dir>. Intermediate files live in work/<repo name>/. Unchanged code costs no LLM calls (cache in describe.py)."""
import json
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
repo, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
work = here / "work" / repo.name
work.mkdir(parents=True, exist_ok=True)
bp, desc, det = work / "blueprint.js", work / "descriptions.js", work / "detail.json"
sys.path.insert(0, str(here))
from blueprint_cli import fingerprint  # noqa: E402
fp = fingerprint(repo)  # taken before reading the code, written only after a full refresh


def run(*args):
    subprocess.run([sys.executable, "-W", "ignore", *map(str, args)], cwd=here, check=True)


print("-> extract.py", flush=True)
run("extract.py", repo, bp)
print("-> describe.py", flush=True)
run("describe.py", repo, bp, desc)
print("-> review.py", flush=True)
subprocess.run([sys.executable, "-W", "ignore", "review.py", repo, bp, desc, work / "review.json"], cwd=here)  # findings are reported, they do not stop the refresh
data = json.loads(bp.read_text(encoding="utf-8").removeprefix("const BP = ").removesuffix(";"))
if data["pipeline"]["nodes"]:
    print("-> detail.py", flush=True)
    run("detail.py", repo, bp, desc, det)
print("-> to_spec.py", flush=True)
run("to_spec.py", bp, desc, out, *([det] if data["pipeline"]["nodes"] else []))
# seal the anchored code as verified: from now on a block turns yellow when its code changes
from extract import CODE_EXT  # noqa: E402
ext = ",".join(sorted(CODE_EXT))  # ponytail: Dockerfiles have no extension, so the drift check does not watch them
for name in ("pipeline", "struttura"):
    spec = out / name / "spec.json"
    if spec.exists() and '"ancore"' in spec.read_text(encoding="utf-8"):
        print(f"-> sealing {name}", flush=True)
        subprocess.run([sys.executable, str(here.parent / "viewer-kit" / "pipeline-viewer"), str(spec), "--root", str(repo), "--code-ext", ext, "--sigilla"], check=True)
if fp:
    (work / "fingerprint.txt").write_text(fp)  # blueprint_cli skips the next refresh while the code stays like this
