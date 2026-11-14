"""Answers an engineer's question about ONE diagram element (a graph node, a component directory, or a link between two), grounded in the
extracted blueprint; if the message asks to change how the element LOOKS, replies with a JSON style instead (the viewer validates and applies it).
Usage: ask.py <repo> <element id | a>b> <question file> [model] [node|edge: which style keys apply]"""
import json
import sys
from pathlib import Path

from describe import ask, digest, function_sources, project
from extract import blueprint

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
repo, cid, question = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).read_text(encoding="utf-8").strip()
model, kind = (sys.argv[4] if len(sys.argv) > 4 else "sonnet"), (sys.argv[5] if len(sys.argv) > 5 else "node")
bp = blueprint(repo)
nodes, edges = bp["pipeline"]["nodes"], bp["pipeline"]["edges"]
N = {n["id"]: n for n in nodes if n["id"] not in ("START", "END")}  # graph nodes (a pipeline, an agent graph)
C = {n["id"]: n for n in bp["structure"]["nodes"]}  # components: top-level directories or packages
KEYS = {"node": "bg, fg (text color), border (color), borderStyle (solid|dashed|dotted), radius (corner roundness in px: 0 = sharp rectangle, 60 = pill)",
        "edge": "color, width (1-8), line (solid|dashed|dotted), arrow (triangle|vee|none|circle|diamond|tee)"}
STYLE = ("If the message asks to change how this element LOOKS (color, shape, border, line style, arrow...), reply with ONLY this JSON and nothing else: "
         '{"style": {...}} using only these keys, and only the ones requested: ' + KEYS[kind] + ". Colors are CSS colors (a name or #hex). "
         'null for a key resets it; {"style": {"reset": true}} restores the default look. Otherwise answer the question.')


def reach(start, forward):
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        for e in edges:
            a, b = (e["source"], e["target"]) if forward else (e["target"], e["source"])
            if a == cur and b in N and b not in seen:
                seen.add(b)
                todo.append(b)
    return sorted(seen)


others = lambda cid, key, field: [m for m in N if m != cid and key in N[m][field]]
code = lambda n: (n.get("snippet") or function_sources(repo).get(n.get("function"), ""))[:3000]
if ">" in cid:  # a link between two elements
    a, b = cid.split(">")
    if a in N and b in N:
        facts = f"Link: {a} -> {b}\nData passing along it: {sorted(set(N[a]['writes']) & set(N[b]['reads']))}\nCode of {a}:\n{code(N[a])}"
    elif a in C and b in C:
        e = next((x for x in bp["structure"]["edges"] if x["source"] == a and x["target"] == b), None) or {"kind": "?", "evidence": []}
        facts = f"Link: component {a} -> component {b}\nHow the code shows it ({e['kind']}): {e['evidence'][:8]}"
    else:
        sys.exit("unknown link")
elif cid in N:
    n = N[cid]
    facts = (f"Component: {cid}\nIts code:\n{code(n)}\n\n"
             f"Runs after it (downstream, transitively): {reach(cid, True)}\nRuns before it (upstream, transitively): {reach(cid, False)}\n"
             f"Data it writes and the components that read each: {json.dumps({k: others(cid, k, 'reads') for k in n['writes']})}\n"
             f"Data it reads and the components that also write each: {json.dumps({k: others(cid, k, 'writes') for k in n['reads']})}")
elif cid in C:
    n = C[cid]
    links = [(e["source"], e["target"], e.get("kind", "import")) for e in bp["structure"]["edges"] if cid in (e["source"], e["target"])]
    facts = (f"Component (a directory of the project): {cid}, {n['files']} files, {n['lines']} lines\n"
             f"Links with other components (from, to, kind): {links}\n"
             + ("Files:\n" + "\n".join(digest(repo, p) for p in n["paths"][:15]) if "paths" in n else ""))
else:
    sys.exit("unknown element")
print(ask(
    f"An engineer sends a message about one element of the architecture diagram of {project(repo)}. " + STYLE + " "
    "For a question: answer in the same language as the question, in at most 120 words, in plain text (no markdown), using ONLY the facts below; "
    "if they do not answer it, say what is missing. Name components, do not quote code. "
    f"Reply with the answer only: no closing notes about what was skipped or risks.\n\n{facts}\n\nMessage: {question}", model=model))
