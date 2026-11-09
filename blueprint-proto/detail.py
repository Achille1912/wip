"""Generates via `claude -p` the pipeline phases and the inside (sub-steps) of each node. Writes detail.json."""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from describe import LANG, ask, function_sources, project

TIPI = {"step", "dec", "ok", "prop", "ai", "store", "actor"}
COLORI = ["blu", "verde", "viola", "arancio", "giallo", "grigio"]  # color names of the viewer's spec format


def json_of(raw):
    return json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]


def called_sources(fn_src, funcs, cap=250):
    """Source of the project functions called directly by fn_src, up to cap lines in total."""
    names = set(re.findall(r"\b([A-Za-z_]\w*)\(", fn_src))  # regex, not ast: fn_src may be truncated
    out, used = [], 0
    for name in sorted(names):
        src = funcs.get(name)
        if src and used + src.count("\n") < cap:
            out.append(src)
            used += src.count("\n") + 1
    return "\n\n".join(out)


def inside(node, funcs, ctx):
    src = funcs.get(node["function"], "") or node.get("snippet", "")
    prompt = (f"This is a step of a workflow in {ctx}. Break it down into its real sub-steps, in execution order "
              "(3 to 8): each must correspond to something the code really does, do not invent. "
              f"Write in {LANG}, no function or variable names. "
              "Reply ONLY with JSON: {\"titolo\": \"short title of the inside\", \"nodi\": [{\"id\": \"s1\", \"testo\": \"short title\", "
              "\"tipo\": \"step|dec|ai|store\", \"descrizione\": \"1 sentence\"}], \"archi\": [[\"s1\", \"s2\", \"optional label\"]]}. "
              "Use type dec for a decision/choice, ai for an LLM call, store for reading/writing data, step for the rest.\n\n"
              f"Function of the step:\n{src}\n\nProject functions it calls:\n{called_sources(src, funcs) if src else ''}")
    d = json_of(ask(prompt))
    ids = {n["id"] for n in d["nodi"]}
    d["nodi"] = [{**n, "tipo": n.get("tipo") if n.get("tipo") in TIPI else "step"} for n in d["nodi"]]
    d["archi"] = [a for a in d["archi"] if a[0] in ids and a[1] in ids]
    return d


def phases(bp, desc):
    nodes = [n["id"] for n in bp["pipeline"]["nodes"] if n["id"] not in ("START", "END")]
    items = [{"id": i, "description": desc["nodes"]["pipeline"][i]} for i in nodes]
    edges = [[e["source"], e["target"]] for e in bp["pipeline"]["edges"] if e["source"] != "START" and e["target"] != "END"]
    shape = '{"fasi": [{"id": "f1", "titolo": "...", "descrizione": "1 sentence", "nodi": [ids]}]}'
    d = json_of(ask("Group these pipeline steps into 2-6 consecutive, coherent PHASES (e.g. intake, processing, reporting, closing). "
                    f"Each step belongs to exactly one phase. Reply ONLY with JSON: {shape}. Write titles and descriptions in {LANG}. "
                    "Use the exact ids.\n\nSteps: " + json.dumps(items, ensure_ascii=False) + "\nLinks: " + json.dumps(edges)))
    fasi, seen = [], set()
    for f in d["fasi"]:
        f["nodi"] = [n for n in f["nodi"] if n in nodes and n not in seen]
        seen |= set(f["nodi"])
        if f["nodi"]:
            f["colore"] = COLORI[len(fasi) % len(COLORI)]
            fasi.append(f)
    missing = [n for n in nodes if n not in seen]
    assert not missing, f"nodes not assigned to any phase: {missing}"
    return fasi


if __name__ == "__main__":
    repo = Path(sys.argv[1]).resolve()
    load = lambda p, pre: json.loads(Path(p).read_text(encoding="utf-8").removeprefix(pre).removesuffix(";"))
    bp, desc = load(sys.argv[2], "const BP = "), load(sys.argv[3], "const DESC = ")
    funcs = function_sources(repo if bp.get("layout") == "flat" else repo / "src")
    ctx = project(repo)
    nodes = [n for n in bp["pipeline"]["nodes"] if n["id"] not in ("START", "END")]
    with ThreadPoolExecutor(6) as ex:
        ph = ex.submit(phases, bp, desc)
        ins = {n["id"]: ex.submit(inside, n, funcs, ctx) for n in nodes if n.get("function") or n.get("snippet")}  # an agent has no function: its definition (prompt, tools) is the source
        out = {"fasi": ph.result(), "interno": {k: f.result() for k, f in ins.items()}}
    Path(sys.argv[4]).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f'{len(out["fasi"])} phases, {sum(len(v["nodi"]) for v in out["interno"].values())} sub-steps in {len(out["interno"])} nodes')
