"""Estrae un blueprint (struttura + pipeline LangGraph) da un repo Python, solo con ast."""
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

SKIP = {"__pycache__", ".git", "resources", "extension", "node_modules", ".terraform", ".venv", "venv", "site-packages", "dist", "build"}
CODE_EXT = {".py", ".tf", ".yaml", ".yml", ".sh", ".toml"}  # in a flat layout infra is architecture too


def py_files(root):
    return [p for p in root.rglob("*.py") if not SKIP & set(p.relative_to(root).parts)]


def package_of(path, src):
    parts = path.relative_to(src).parts
    return parts[0].removesuffix(".py") if len(parts) > 1 or parts[0] != "__init__.py" else "src"


def structure(src):
    """Nodo = sottopacchetto di src; arco = import src.X (aggregato)."""
    nodes, edges = {}, Counter()
    for f in py_files(src):
        pkg = package_of(f, src)
        n = nodes.setdefault(pkg, {"id": pkg, "files": 0, "lines": 0})
        n["files"] += 1
        text = f.read_text(encoding="utf-8", errors="replace")
        n["lines"] += text.count("\n") + 1
        for node in ast.walk(ast.parse(text)):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                p = m.split(".")
                if p[0] == "src" and len(p) > 1 and p[1] != pkg:
                    edges[(pkg, p[1])] += 1
    return list(nodes.values()), [{"source": a, "target": b, "weight": w} for (a, b), w in edges.items()]


def state_keys(fn):
    """Chiavi di state lette (state["k"], state.get("k")) e scritte (chiavi letterali nei return)."""
    reads, writes = set(), set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == "state":
            if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                reads.add(n.slice.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get":
            if isinstance(n.func.value, ast.Name) and n.func.value.id == "state" and n.args:
                if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    reads.add(n.args[0].value)
        elif isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
            writes |= {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
    return reads, writes


def pipeline(src):
    funcs = {}
    for f in py_files(src):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8", errors="replace"))):
            if isinstance(n, ast.FunctionDef):
                funcs[n.name] = (n, f.relative_to(src.parent).as_posix())
    builder = ast.parse((src / "pipeline" / "builder.py").read_text(encoding="utf-8"))
    nodes, edges = {}, []
    const = lambda a: a.value if isinstance(a, ast.Constant) else ("END" if isinstance(a, ast.Name) and a.id == "END" else None)
    for c in ast.walk(builder):
        if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)):
            continue
        m = c.func.attr
        if m == "add_node":
            name, wrapped = const(c.args[0]), c.args[1]
            fn = wrapped.args[1] if isinstance(wrapped, ast.Call) and len(wrapped.args) > 1 else wrapped
            fname = fn.id if isinstance(fn, ast.Name) else None
            reads, writes = state_keys(funcs[fname][0]) if fname in funcs else (set(), set())
            nodes[name] = {"id": name, "function": fname, "file": funcs.get(fname, (0, None))[1],
                           "reads": sorted(reads), "writes": sorted(writes)}
        elif m == "set_entry_point":
            edges.append({"source": "START", "target": const(c.args[0])})
        elif m == "add_edge":
            edges.append({"source": const(c.args[0]), "target": const(c.args[1])})
        elif m == "add_conditional_edges":
            for k, v in zip(c.args[2].keys, c.args[2].values):
                edges.append({"source": const(c.args[0]), "target": const(v), "label": const(k), "conditional": True})
    nodes["START"] = {"id": "START", "reads": [], "writes": []}
    nodes["END"] = {"id": "END", "reads": [], "writes": []}
    for e in edges:  # contratto dedotto: chiavi scritte dal sorgente e lette dal destinatario
        shared = set(nodes[e["source"]]["writes"]) & set(nodes[e["target"]]["reads"])
        e["state_keys"] = sorted(shared)
    return list(nodes.values()), edges


def code_files(root):
    """Code files of a flat layout (any CODE_EXT), skipping hidden and vendored directories."""
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP and not d.startswith("."))
        out += [Path(dp) / f for f in sorted(fn) if Path(f).suffix in CODE_EXT]
    return out


ENVVAR = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
CFGPATH = re.compile(r"/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-{}$.]+)+")
SYSPATH = ("/dev/", "/usr/", "/bin/", "/tmp/", "/etc/", "/var/", "/opt/", "/proc/")


def flat_structure(repo):
    """Components of a repo without a src/ package: each top-level directory (a deployable or a layer), plus "(root)" for loose files.
    Edges are only what the text proves, each with its evidence: Python imports across components; a component naming another's path;
    identifiers (env vars, config paths) that only 2-3 components share. The LLM later curates and describes them, it never invents one."""
    files = code_files(repo)
    rel = lambda f: f.relative_to(repo).as_posix()
    comp = lambda f: f.relative_to(repo).parts[0] if len(f.relative_to(repo).parts) > 1 else "(root)"
    nodes, text = {}, {}
    for f in files:
        text[f] = f.read_text(encoding="utf-8-sig", errors="replace")
        n = nodes.setdefault(comp(f), {"id": comp(f), "files": 0, "lines": 0, "py_lines": 0, "paths": []})
        n["files"] += 1
        n["lines"] += text[f].count("\n") + 1
        n["py_lines"] += (text[f].count("\n") + 1) if f.suffix == ".py" else 0
        n["paths"].append(rel(f))
    names = {c for c in nodes if c != "(root)"}
    edges = {}  # (source, target) -> edge; one edge per ordered pair, the strongest kind wins (import > mention > shared)

    def add(a, b, kind, evidence):
        e = edges.setdefault((a, b), {"source": a, "target": b, "kind": kind, "evidence": []})
        e["evidence"] += [x for x in evidence if x not in e["evidence"]]

    for f in files:  # 1. imports across components
        if f.suffix != ".py":
            continue
        try:
            tree = ast.parse(text[f])
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [a.name for a in node.names] if isinstance(node, ast.Import) else []
            for m in mods:
                if m.split(".")[0] in names and m.split(".")[0] != comp(f):
                    add(comp(f), m.split(".")[0], "import", [f"{rel(f)} imports {m}"])
    for f in files:  # 2. a component names another component's path
        for b in names - {comp(f)}:
            if re.search(rf"(?<![\w.-]){re.escape(b)}(?![\w-])", text[f]):
                e = edges.get((comp(f), b))
                if e is None or e["kind"] == "mention":
                    add(comp(f), b, "mention", [rel(f)])
    users = defaultdict(set)  # 3. identifiers shared by few components
    for f in files:
        for tok in set(ENVVAR.findall(text[f])) | {p for p in CFGPATH.findall(text[f]) if not p.startswith(SYSPATH)}:
            users[tok].add(comp(f))
    pairs = defaultdict(list)
    for tok, cs in users.items():
        if 2 <= len(cs) <= 3:
            for a, b in combinations(sorted(cs), 2):
                pairs[(a, b)].append(tok)
    infra_share = lambda c: 1 - nodes[c]["py_lines"] / max(1, nodes[c]["lines"])
    for (a, b), toks in pairs.items():
        if len(toks) < 2:  # one shared name between siblings is a naming convention, not a link
            continue
        if (a, b) in edges or (b, a) in edges:  # already linked by an import or a mention: the shared names are more evidence for the same link
            edges.get((a, b), edges.get((b, a)))["evidence"].append("shares " + ", ".join(sorted(toks)[:5]))
            continue
        src, dst = (a, b) if infra_share(a) >= infra_share(b) else (b, a)  # the infra side provides the configuration
        add(src, dst, "shared", sorted(toks)[:8])
    return list(nodes.values()), list(edges.values())


def graph_nodes(repo):
    """Agent/workflow graphs declared in code: LangGraph add_node("name", fn) and Strands add_node(agent, "name"); add_edge with an optional
    condition=. A name that is a variable (nodes created in a loop) becomes one dynamic node. Evidence of each edge = file:line of the call."""
    nodes, edges = {}, []
    for f in py_files(repo):
        text = f.read_text(encoding="utf-8-sig", errors="replace")
        if ".add_node(" not in text or ".add_edge(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        rel = f.relative_to(repo).as_posix()
        ref = lambda a: a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else a.id if isinstance(a, ast.Name) else None
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            calls = sorted((c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)), key=lambda c: c.lineno)
            if not any(c.func.attr == "add_edge" for c in calls):
                continue
            assigned = {t.id: ast.get_source_segment(text, a.value) for a in ast.walk(fn) if isinstance(a, ast.Assign) for t in a.targets if isinstance(t, ast.Name)}
            for c in calls:
                m, a = c.func.attr, c.args
                if m == "add_node" and len(a) == 2:
                    lg = isinstance(a[0], ast.Constant) and isinstance(a[0].value, str)  # LangGraph add_node("name", fn) vs Strands add_node(agent, "name")
                    name, obj, dyn = (a[0].value, a[1], False) if lg else (ref(a[1]), a[0], not isinstance(a[1], ast.Constant))
                    if name:
                        nodes[name] = {"id": name, "function": obj.id if lg and isinstance(obj, ast.Name) else None, "file": rel, "enclosing": fn.name,
                                       "dynamic": dyn, "line": c.lineno, "reads": [], "writes": [],
                                       "snippet": (assigned.get(obj.id, "") if isinstance(obj, ast.Name) else "")[:3000]}
                elif m == "set_entry_point" and a and ref(a[0]):
                    edges.append({"source": "START", "target": ref(a[0]), "evidence": f"{rel}:{c.lineno}"})
                elif m == "add_edge" and len(a) >= 2 and ref(a[0]) and ref(a[1]):
                    cond = next((k.value for k in c.keywords if k.arg == "condition"), None)
                    e = {"source": ref(a[0]), "target": ref(a[1]), "evidence": f"{rel}:{c.lineno}"}
                    if cond is not None:
                        e.update(conditional=True, label=cond.id if isinstance(cond, ast.Name) else "condition")
                    edges.append(e)
    for e in edges:
        e.setdefault("state_keys", [])
        for k in ("source", "target"):
            if e[k] not in nodes and e[k] not in ("START", "END"):
                nodes[e[k]] = {"id": e[k], "function": None, "file": None, "enclosing": None, "dynamic": True, "line": 0, "snippet": "", "reads": [], "writes": []}
    nodes["START"] = {"id": "START", "reads": [], "writes": []}
    nodes["END"] = {"id": "END", "reads": [], "writes": []}
    return list(nodes.values()), edges


def blueprint(repo):
    """The whole blueprint of a repo. src/ layout (packages under src/, LangGraph in src/pipeline/builder.py): as before.
    Otherwise (flat layout): components = top-level directories with their evidence, plus any agent graph found in the code."""
    repo = Path(repo).resolve()
    if (repo / "src").is_dir():
        sn, se = structure(repo / "src")
        try:
            pn, pe = pipeline(repo / "src")
        except Exception:
            pn, pe = [], []
        return {"repo": repo.name, "layout": "src", "structure": {"nodes": sn, "edges": se}, "pipeline": {"nodes": pn, "edges": pe}}
    sn, se = flat_structure(repo)
    pn, pe = graph_nodes(repo)
    if len(pn) <= 2:  # only START and END: no graph in this repo
        pn, pe = [], []
    return {"repo": repo.name, "layout": "flat", "structure": {"nodes": sn, "edges": se}, "pipeline": {"nodes": pn, "edges": pe}}


if __name__ == "__main__":
    repo = Path(sys.argv[1]).resolve()
    out = blueprint(repo)
    # .js e non .json: una pagina aperta da file:// non può fare fetch di un json locale
    Path(sys.argv[2]).write_text("const BP = " + json.dumps(out, indent=1) + ";", encoding="utf-8")
    s, p = out["structure"], out["pipeline"]
    print(f"{out['layout']} layout | structure: {len(s['nodes'])} nodi, {len(s['edges'])} archi | pipeline: {len(p['nodes'])} nodi, {len(p['edges'])} archi")
