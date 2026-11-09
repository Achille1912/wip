"""Turns the engineer's notes into an architecture plan BEFORE any code is written: the structural changes the code must have afterwards,
in the vocabulary of archplan.py. The run later checks its result against it.
Usage: plan.py <repo> <state dir> make <input json> [model]   (input: {"notes": [{id, target, titolo, nota}], "comments": [text]})"""
import ast
import json
import sys
from pathlib import Path

from archplan import atom, fmt
from describe import ask, function_sources
from extract import blueprint, code_files, py_files

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
repo, state, cmd = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
src = repo / "src"
VOCAB = ('{"op":"add"|"remove","kind":"module","file":"path/to/file.py"}  a code file (Python, Terraform, YAML, shell) created / deleted\n'
         '{"op":"limit","kind":"module_size","file":"path/to/file.py","max_lines":N}  that file must have at most N lines afterwards\n'
         '{"op":"add"|"remove","kind":"package","id":"dir_name"}  a top-level component directory (or package) is created / removed\n'
         '{"op":"add"|"remove","kind":"component","id":"node_id"}  a node of the workflow / agent graph\n'
         '{"op":"add"|"remove","kind":"flow","from":"node_a","to":"node_b"}  an edge between graph nodes\n'
         '{"op":"add"|"remove","kind":"writes"|"reads","id":"node_id","key":"state_key"}  a node starts/stops writing/reading a state key\n'
         '{"op":"add"|"remove","kind":"dependency","from":"package_a","to":"package_b"}  component A starts/stops depending on component B')


def outline(path):
    """Top-level symbols of a big file with their sizes: enough to propose how to split it."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    rows = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rows.append(f"{n.name} ({n.end_lineno - n.lineno + 1} lines)")
            if isinstance(n, ast.ClassDef):
                rows += [f"  .{m.name} ({m.end_lineno - m.lineno + 1} lines)" for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return "\n".join(rows[:90])


def facts(targets):
    bp = blueprint(repo)
    sn, se, pn, pe = bp["structure"]["nodes"], bp["structure"]["edges"], bp["pipeline"]["nodes"], bp["pipeline"]["edges"]
    flat = bp["layout"] == "flat"
    funcs = function_sources(repo if flat else src)
    paths = code_files(repo) if flat else py_files(src)
    sizes = {p.relative_to(repo).as_posix(): len(p.read_text(encoding="utf-8-sig", errors="replace").splitlines()) for p in paths}
    big = [f for f, n in sorted(sizes.items(), key=lambda kv: -kv[1])[:3] if n > 600 and f.endswith(".py")]
    node_src = "\n\n".join(f"Code of {n['id']}:\n" + "\n".join((n.get("snippet") or funcs.get(n.get("function"), "")).splitlines()[:80]) for n in pn if n["id"] in targets)
    if flat:
        head = (f"Components (top-level directories): {[(n['id'], n['files'], n['lines']) for n in sn]}\n"
                f"Links between components (from, to, kind): {[(e['source'], e['target'], e['kind']) for e in se]}\n")
        if pn:
            head += f"Agent graph nodes: {[n['id'] for n in pn if n['id'] not in ('START', 'END')]}\nAgent graph flows: {[(e['source'], e['target']) for e in pe]}\n"
    else:
        head = (f"Pipeline components: {json.dumps([{k: n.get(k) for k in ('id', 'file', 'reads', 'writes')} for n in pn if n['id'] not in ('START', 'END')])}\n"
                f"Flows: {[(e['source'], e['target']) for e in pe]}\nPackage dependencies: {[(e['source'], e['target']) for e in se]}\n")
    return (head + f"Largest files: {sorted(sizes.items(), key=lambda kv: -kv[1])[:15]}\n"
            + "".join(f"\nOutline of {f}:\n{outline(repo / f)}\n" for f in big) + ("\n" + node_src if node_src else ""))


def make(inp_file, model):
    inp = json.loads(Path(inp_file).read_text(encoding="utf-8"))
    targets = {n["target"].split("/")[0] for n in inp["notes"]}
    notes = "\n".join(f"- on «{n['titolo']}» ({n['target']}): {n['nota']}" for n in inp["notes"])
    comments = f"\nLoose comments the engineer left on the diagram (unanchored): {inp['comments']}" if inp["comments"] else ""
    raw = ask("You are planning a change to the architecture of a software project BEFORE any code is written. An engineer left notes on a diagram of it. "
              "Translate them into the structural changes the code must have afterwards, using ONLY this vocabulary:\n" + VOCAB +
              "\nBe concrete and minimal: only structure the notes imply, no more. Paths are repo-relative with forward slashes. When a note asks to split "
              "a file, list the new modules (with real paths) and a size limit for the old file. When it asks for no structural change, return no items. "
              'Reply ONLY with JSON: {"summary": "one or two sentences: what the change does", "items": [...]}\n\n'
              f"Project facts:\n{facts(targets)}\n\nNotes:\n{notes}{comments}", model=model)
    d = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    items, atoms = [], []
    for i in d.get("items", [])[:40]:
        try:
            a = atom(i)
        except (ValueError, AttributeError):
            continue  # outside the vocabulary: dropped, never guessed at
        if a not in atoms:
            atoms.append(a)
            items.append(i)
    if not items:
        sys.exit("The notes do not imply a structural change, so there is nothing to plan: use Run for a direct edit.")
    state.mkdir(parents=True, exist_ok=True)
    (state / "plan.json").write_text(json.dumps({"summary": str(d.get("summary", ""))[:400], "items": items, "lines": [fmt(a) for a in atoms],
                                                 "notes": [n["id"] for n in inp["notes"]]}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} planned change(s)")


if cmd == "make":
    make(sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "sonnet")
