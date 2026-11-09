"""Generates natural-language descriptions of the blueprint's nodes, components and links via `claude -p`."""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from extract import py_files

LANG = os.environ.get("BLUEPRINT_LANG", "English")
STYLE = (f"Write in {LANG}, at most 2 short sentences (under 40 words in total), for an engineer thinking about architecture. "
         "Explain WHAT it does and WHY it exists, not HOW; no special cases or internal branches. "
         "No function, variable or file names, no code, no caveats or notes about what you did not verify. "
         "Reply with the description text only.")

# Answers saved by prompt hash: unchanged code = same prompt = no LLM call (incremental refresh).
CACHE_FILE = Path(__file__).with_name(".llm_cache.json")
_lock = threading.Lock()
_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}


def ask(prompt, tries=2, model="sonnet", key=None):
    """key: what the answer really depends on, when the prompt also carries text that may drift (e.g. other nodes' descriptions)."""
    full = hashlib.sha256((prompt if model == "sonnet" else f"{model}\0{prompt}").encode()).hexdigest()  # sonnet keeps the old keys: the cache stays valid
    key = hashlib.sha256(f"{model}\0key\0{key}".encode()).hexdigest() if key is not None else full
    if key not in _cache and full in _cache:  # same prompt answered before under the full-prompt key: reuse it, no call
        with _lock:
            _cache[key] = _cache[full]
            CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")  # saved, so later prompt drift still hits
    if key in _cache:
        return _cache[key]
    print("   llm:", re.sub(r"\s+", " ", prompt)[:90], file=sys.stderr, flush=True)  # every real call shows in the refresh output
    for _ in range(tries):  # `claude -p` occasionally exits with a transient error
        r = subprocess.run(["claude", "-p", "--model", model, "--output-format", "text"],
                           input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=300)
        if not r.returncode:
            with _lock:
                _cache[key] = r.stdout.strip()
                CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
            return _cache[key]
    raise RuntimeError((r.stderr + r.stdout)[:300])


def project(repo):
    """What the LLM is told the project is: its name and the first prose paragraph of its README (any language)."""
    blurb = ""
    f = next((p for p in (Path(repo) / n for n in ("README.md", "README.rst", "README")) if p.exists()), None)
    if f:
        for para in f.read_text(encoding="utf-8-sig", errors="replace").split("\n\n"):
            para = para.strip()
            if len(para) > 40 and not para.startswith(("#", "`", "│", "┌", "[!", "<", "|", "-", "=")):
                blurb = re.sub(r"[*`_>]|\s+", " ", para).strip()[:300]
                break
    return f"the software project '{Path(repo).name}'" + (f" ({blurb})" if blurb else "")


def function_sources(src):
    out = {}
    for f in py_files(src):
        text = f.read_text(encoding="utf-8-sig", errors="replace")
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                out[n.name] = "\n".join((ast.get_source_segment(text, n) or "").splitlines()[:150])
    return out


def describe_node(node, funcs, ctx):
    if node["id"] in ("START", "END"):
        return {"START": "Pipeline entry point.", "END": "End of the execution."}[node["id"]]
    code = node.get("snippet") or funcs.get(node.get("function"), "")  # an agent graph node has no function of its own: its definition is the code
    extra = " It is created dynamically, once for each of several similar agents." if node.get("dynamic") else ""
    return ask(f"This is a step of a workflow in {ctx}.{extra}\n{STYLE}\n\nCode:\n{code}")


def describe_group(node, src, ctx):
    """A package (or module) under src/: same answer as a flat component, from the repo-relative paths of its files."""
    repo = src.parent
    paths = [f.relative_to(repo).as_posix() for f in py_files(src) if f.relative_to(src).parts[0].removesuffix(".py") == node["id"]]
    return describe_component({**node, "paths": paths, "files": len(paths)}, repo, ctx)


def digest(repo, rel):
    """One line about a file of any kind: Python = docstring + top-level names; anything else (Terraform, YAML, shell…) = its first lines."""
    f = Path(repo) / rel
    text = f.read_text(encoding="utf-8-sig", errors="replace")
    if f.suffix == ".py":
        try:
            t = ast.parse(text)
            names = ", ".join(n.name for n in t.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)))[:160]
            return f"{rel}: {(ast.get_docstring(t) or '')[:160]} | defines: {names}"
        except (SyntaxError, ValueError):
            pass
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not set(ln.strip()) <= set("-=#/ *")]
    return f"{rel}: " + " / ".join(lines[:8])[:260]


LAYERS = ("Entry point", "Interface", "Orchestration", "Service", "Library", "Data", "Configuration", "Scripts", "Build & deploy", "Infrastructure")


def describe_component(node, repo, ctx):
    """A top-level directory of a flat repo, from its file digests and its own README: {summary, layer, tags, files:[{path, role}]}."""
    facts = [digest(repo, p) for p in node["paths"][:25]]
    readme = next((p for p in Path(repo, node["id"]).glob("README*") if p.is_file()), None) if node["id"] != "(root)" else None
    if readme:
        facts.insert(0, "README: " + re.sub(r"\s+", " ", readme.read_text(encoding="utf-8-sig", errors="replace"))[:500])
    extra = f" ({node['files'] - 25} more similar files not shown)" if node["files"] > 25 else ""
    raw = ask(f"This is a directory ('{node['id']}') of {ctx}: a deployable service, infrastructure code or a set of scripts. "
              f"The text may be in another language: answer in {LANG} anyway.\n"
              'Reply ONLY with JSON: {"summary": "' + STYLE.replace("Reply with the description text only.", "") + '", '
              f'"layer": one of {list(LAYERS)}, "tags": 3-5 lowercase hyphenated words naming what it is made of or does (e.g. "terraform", "a2a", "ci-cd"), '
              '"files": up to 8 of the MOST IMPORTANT files, each {"path": exactly as listed, "role": "what this file is responsible for, under 15 words, no code"}}.\n\n'
              f"Content{extra}:\n" + "\n".join(facts))
    d = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    ok = set(node["paths"])
    return {"summary": str(d.get("summary", "")).strip(), "layer": d.get("layer") if d.get("layer") in LAYERS else "Library",
            "tags": [re.sub(r"[^a-z0-9-]", "", str(t).lower().replace(" ", "-")) for t in d.get("tags", [])][:5],
            "files": [{"path": f["path"], "role": str(f.get("role", ""))[:120]} for f in d.get("files", []) if isinstance(f, dict) and f.get("path") in ok][:8]}


def describe_edges(edges, node_desc):
    items = [{"id": f'{e["source"]}->{e["target"]}|{e.get("label", "")}', "from": node_desc[e["source"]], "to": node_desc[e["target"]],
              "branch": e.get("label", ""), "data_passed": e["state_keys"]} for e in edges if e["source"] != "START" and e["target"] != "END"]
    raw = ask("Each item is a link between two steps of a pipeline. 'data_passed' are technical variable names: use them to understand what "
              f"travels, but do not quote them. For each item reply in {LANG} with {{\"data\": the pieces of information that pass from the first step "
              "to the second, as a short comma-separated list in plain words (empty string if data_passed is empty), "
              "\"when\": only if 'branch' is set, a short clause completing 'Taken when ...' (otherwise empty string)}. "
              "Reply ONLY with a JSON object {id: {data, when}}.\n\n" + json.dumps(items, ensure_ascii=False),
              key=json.dumps(["edges", LANG, [(i["id"], i["data_passed"]) for i in items]]))  # a reworded step description does not redo every link
    return json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]


LINK_TYPES = ("imports", "builds", "deploys", "provisions", "configures", "calls", "depends_on")


def curate_links(edges, desc, ctx):
    """The static links come with their evidence; the model only decides which are real relationships, orients them and says what they are."""
    items = [{"id": f'{e["source"]}>{e["target"]}', "kind": e["kind"], "evidence": e["evidence"][:6], "a": e["source"], "a_is": desc.get(e["source"], ""),
              "b": e["target"], "b_is": desc.get(e["target"], "")} for e in edges]
    raw = ask(f"Each item is a candidate relationship between two components of {ctx}. 'evidence' is literal text found in the code: kind 'import' = Python imports, "
              "'mention' = component a refers to the path of b (build, deploy or script reference), 'shared' = a and b use the same identifiers (shared configuration). "
              "For each item decide: keep = a real dependency, deployment, build or runtime/configuration relationship; drop = a coincidence "
              "(e.g. sibling components that merely follow the same naming convention). For kept items give 'from' (the component that depends on, uses or deploys the other: "
              f"it must be exactly a or b), 'type' (exactly one of {list(LINK_TYPES)}: imports = code imports, builds = builds or packages, deploys = deploys or releases, "
              "provisions = creates infrastructure for, configures = supplies settings or secrets to, calls = invokes at runtime, depends_on = anything else), 'label' (2-3 words) and 'text' (one sentence in {LANG} saying what the relationship is, without quoting the evidence). "
              'Reply ONLY with a JSON object {id: {"keep": true|false, "from": "...", "type": "...", "label": "...", "text": "..."}}.\n\n' + json.dumps(items, ensure_ascii=False),
              key=json.dumps(["links", LANG, ctx, [(i["id"], i["kind"], i["evidence"]) for i in items]]))  # links change with their evidence, not with a reworded summary
    out = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    out = {k: v for k, v in out.items() if isinstance(v, dict) and k in {i["id"] for i in items}}
    for v in out.values():  # a type outside the vocabulary is never trusted
        v["type"] = v.get("type") if v.get("type") in LINK_TYPES else "depends_on"
    return out


if __name__ == "__main__":
    repo = Path(sys.argv[1]).resolve()
    bp = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8").removeprefix("const BP = ").removesuffix(";"))
    flat, ctx = bp.get("layout") == "flat", project(repo)
    funcs = function_sources(repo if flat else repo / "src")
    with ThreadPoolExecutor(6) as ex:
        pf = {n["id"]: ex.submit(describe_node, n, funcs, ctx) for n in bp["pipeline"]["nodes"]}
        sf = {n["id"]: ex.submit(describe_component, n, repo, ctx) if flat else ex.submit(describe_group, n, repo / "src", ctx)
              for n in bp["structure"]["nodes"] if flat or n["id"] not in ("src", "__main__")}
        pipe = {k: f.result() for k, f in pf.items()}
        struct = {k: f.result() for k, f in sf.items()}
    meta = {k: {x: v[x] for x in ("layer", "tags", "files")} for k, v in struct.items() if isinstance(v, dict)}
    struct = {k: v["summary"] if isinstance(v, dict) else v for k, v in struct.items()}
    edges = describe_edges(bp["pipeline"]["edges"], pipe) if bp["pipeline"]["edges"] else {}
    links = curate_links(bp["structure"]["edges"], struct, ctx) if flat and bp["structure"]["edges"] else {}
    out = {"nodes": {"pipeline": pipe, "structure": struct}, "meta": meta, "edges": {"pipeline": edges, "structure": links}}
    Path(sys.argv[3]).write_text("const DESC = " + json.dumps(out, indent=1, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"{len(pipe)} pipeline nodes, {len(struct)} groups, {len(edges)} pipeline edges, {len(links)} curated links")
