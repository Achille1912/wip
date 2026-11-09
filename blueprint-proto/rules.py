"""Architecture rules in plain words: the LLM compiles each one ONCE into a check from a small fixed vocabulary; the check then runs
deterministically on the current code. Usage: rules.py <repo> <rules.json> check | add <text file> | del <id>"""
import json
import secrets
import sys
from pathlib import Path

from describe import ask
from extract import blueprint, code_files, py_files

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
repo, rules_file, cmd = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), sys.argv[3]
src = repo / "src"


def load():
    return json.loads(rules_file.read_text(encoding="utf-8")) if rules_file.exists() else []


def save(rules):
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")


def facts():
    bp = blueprint(repo)
    return {"packages": [n["id"] for n in bp["structure"]["nodes"]],
            "deps": {(e["source"], e["target"]) for e in bp["structure"]["edges"] if e.get("kind", "import") != "shared"},  # imports and path references
            "nodes": [n for n in bp["pipeline"]["nodes"] if n["id"] not in ("START", "END")], "flat": bp["layout"] == "flat"}


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
    elif c["type"] == "max_file_lines":
        for p in (code_files(repo) if f["flat"] else py_files(src)):
            n, rel = len(p.read_text(encoding="utf-8-sig", errors="replace").splitlines()), p.relative_to(repo).as_posix()
            if n > c["n"]:
                v.append({"text": f"{rel} has {n} lines (limit {c['n']})", "nodes": [x["id"] for x in f["nodes"] if x.get("file") == rel]})
    return v


def compile_rule(text, f):
    keys = sorted({k for n in f["nodes"] for k in n["writes"]})
    raw = ask("Translate the architecture rule below into ONE JSON check from this vocabulary. Reply ONLY with the JSON.\n"
              '{"type":"forbid_dependency","from":<package>,"to":<package>}  (component A must not import or reference component B)\n'
              '{"type":"single_writer","key":<state key>}  (a state key may be written by at most one component)\n'
              '{"type":"max_file_lines","n":<integer>}  (no code file may exceed n lines)\n'
              'If the rule cannot be expressed with these, reply {"type":"unsupported","why":"short reason"}.\n'
              f"Packages: {f['packages']}\nState keys: {keys}\n\nRule: {text}")
    c = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    ok = {"forbid_dependency": c.get("from") in f["packages"] and c.get("to") in f["packages"], "single_writer": c.get("key") in keys,
          "max_file_lines": isinstance(c.get("n"), int) and c["n"] > 0}.get(c.get("type"))
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
