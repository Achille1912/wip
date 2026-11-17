"""Architecture rules in plain words: the LLM compiles each one ONCE into a check from a small fixed vocabulary; the check then runs
deterministically on the current code. Usage: rules.py <repo> <rules.json> check | add <text file> | del <id>"""
import json
import secrets
from collections import defaultdict
import sys
from pathlib import Path

from blueprint_cli import DATA
from describe import ask
from extract import blueprint, code_files

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
repo, rules_file, cmd = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), sys.argv[3]


def load():
    return json.loads(rules_file.read_text(encoding="utf-8")) if rules_file.exists() else []


def save(rules):
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")


def facts():
    bp = blueprint(repo)
    desc = DATA / "work" / repo.name / "descriptions.js"  # the layer of each component, given by the last refresh
    meta = json.loads(desc.read_text(encoding="utf-8").removeprefix("const DESC = ").removesuffix(";")).get("meta", {}) if desc.exists() else {}
    return {"packages": [n["id"] for n in bp["structure"]["nodes"]], "layers": {k: v["layer"] for k, v in meta.items() if v.get("layer")},
            "deps": {(e["source"], e["target"]) for e in bp["structure"]["edges"] if e.get("kind", "import") != "shared"},  # imports and path references
            "imports": {(e["source"], e["target"]) for e in bp["structure"]["edges"] if e.get("kind", "import") == "import"},
            "nodes": [n for n in bp["pipeline"]["nodes"] if n["id"] not in ("START", "END")]}


def check(c, f):
    """Violations of one check: [{text, nodes}]; nodes = blueprint components to flag."""
    v = []
    if c["type"] == "forbid_dependency":
        if (c["from"], c["to"]) in f["deps"]:
            v.append({"text": f'Package "{c["from"]}" imports package "{c["to"]}"', "nodes": []})
    elif c["type"] == "single_writer":
        w = [n["id"] for n in f["nodes"] if c["key"] in n["writes"]]
        if len(w) > 1:
            v.append({"text": f'"{c["key"]}" is written by {len(w)} components: {", ".join(w)}', "nodes": w})
    elif c["type"] == "layer_order":
        L = f["layers"]
        for a, b in sorted(f["deps"]):
            if L.get(a) == c["from"] and L.get(b) == c["to"]:
                v.append({"text": f'"{a}" ({c["from"]}) depends on "{b}" ({c["to"]})', "nodes": [a, b]})
    elif c["type"] == "no_cycles":
        for cyc in cycles(f["imports"]):
            v.append({"text": "Import cycle: " + " ↔ ".join(cyc), "nodes": cyc})
    elif c["type"] == "max_file_lines":
        for p in code_files(repo):
            n, rel = len(p.read_text(encoding="utf-8-sig", errors="replace").splitlines()), p.relative_to(repo).as_posix()
            if n > c["n"]:
                v.append({"text": f"{rel} has {n} lines (limit {c['n']})", "nodes": [x["id"] for x in f["nodes"] if x.get("file") == rel]})
    return v


def cycles(edges):
    """Groups of components that import each other, directly or through others (each group sorted, groups sorted)."""
    out = defaultdict(set)
    for a, b in edges:
        out[a].add(b)

    def reach(x):
        seen, todo = set(), [x]
        while todo:
            for y in out[todo.pop()] - seen:
                seen.add(y)
                todo.append(y)
        return seen
    r = {x: reach(x) for x in list(out)}
    return sorted({tuple(sorted(y for y in r if y in r[x] and x in r[y])) for x in r if x in r[x]})


def compile_rule(text, f):
    keys = sorted({k for n in f["nodes"] for k in n["writes"]})
    raw = ask("Translate the architecture rule below into ONE JSON check from this vocabulary. Reply ONLY with the JSON.\n"
              '{"type":"forbid_dependency","from":<package>,"to":<package>}  (component A must not import or reference component B)\n'
              '{"type":"single_writer","key":<state key>}  (a state key may be written by at most one component)\n'
              '{"type":"max_file_lines","n":<integer>}  (no code file may exceed n lines)\n'
              '{"type":"layer_order","from":<layer>,"to":<layer>}  (no component of layer A may depend on a component of layer B)\n'
              '{"type":"no_cycles"}  (no import cycle between components)\n'
              'If the rule cannot be expressed with these, reply {"type":"unsupported","why":"short reason"}.\n'
              f"Packages: {f['packages']}\nLayers: {sorted(set(f['layers'].values()))}\nState keys: {keys}\n\nRule: {text}")
    c = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    ok = {"forbid_dependency": c.get("from") in f["packages"] and c.get("to") in f["packages"], "single_writer": c.get("key") in keys,
          "max_file_lines": isinstance(c.get("n"), int) and c["n"] > 0, "no_cycles": True,
          "layer_order": c.get("from") in f["layers"].values() and c.get("to") in f["layers"].values()}.get(c.get("type"))
    if not ok:  # an unknown name would make the rule pass forever: refuse it instead
        sys.exit("This rule cannot be checked automatically: " + (c.get("why") or "it names packages or data that do not exist"))
    return c


if cmd == "check":
    f = facts()
    print(json.dumps({"rules": [{**r, "violations": check(r["check"], f)} for r in load()]}))
elif cmd == "add":
    text = Path(sys.argv[4]).read_text(encoding="utf-8").strip()
    save(load() + [{"id": secrets.token_hex(4), "text": text, "check": compile_rule(text, facts())}])
elif cmd == "del":
    save([r for r in load() if r["id"] != sys.argv[4]])
