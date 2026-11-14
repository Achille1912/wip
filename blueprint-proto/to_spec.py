"""Converte blueprint.js + descriptions.js nel formato spec del pipeline-viewer (viewer-kit/)."""
import json
import sys
from pathlib import Path

load = lambda p, pre: json.loads(Path(p).read_text(encoding="utf-8").strip().removeprefix(pre).removesuffix(";"))
title = lambda s: s.replace("_", " ").capitalize()


def pipeline_spec(bp, desc, detail=None):
    nodes = desc["nodes"]["pipeline"]
    detail = detail or {"fasi": [], "interno": {}}
    nodi = [n for n in bp["pipeline"]["nodes"] if n["id"] not in ("START", "END")]
    cond = {e["source"] for e in bp["pipeline"]["edges"] if e.get("conditional")}
    agent = lambda n: n.get("snippet", "").lstrip().startswith("Agent(")  # an LLM agent node of an agent graph
    anchor = lambda n: [f'{n["file"]}:{n.get("function") or n.get("enclosing")}'] if n.get("file") and (n.get("function") or n.get("enclosing")) else []
    blocchi = [{"id": n["id"], "testo": title(n["id"]) + (" (dynamic)" if n.get("dynamic") else ""),
                "tipo": "ai" if agent(n) else "dec" if n["id"] in cond else "ai" if "LLM" in nodes[n["id"]] else "step",
                "descrizione": nodes[n["id"]], "ancore": anchor(n),  # the viewer checks the anchor against the code: changed/missing markers
                **({"interno": detail["interno"][n["id"]]} if n["id"] in detail["interno"] else {})} for n in nodi]
    gruppi = [{"id": f["id"], "titolo": f["titolo"], "colore": f["colore"], "nodi": f["nodi"], "descrizione": f.get("descrizione", ""), "direzione": "RIGHT"} for f in detail["fasi"]]

    def contract(c):  # edge contract in words: what travels, and when a conditional branch is taken
        return (f'Passes: {c["data"]}.' if c.get("data") else "Execution order only.") + (f' Taken when {c["when"]}.' if c.get("when") else "")

    archi = [[e["source"], e["target"], title(e.get("label", "")), contract(desc["edges"]["pipeline"].get(f'{e["source"]}->{e["target"]}|{e.get("label", "")}', {})),
              *([f'grafo: add_edge in {e["evidence"]}'] if e.get("evidence") else [])]  # optional 5th item: where the code declares the link
             for e in bp["pipeline"]["edges"] if e["source"] != "START" and e["target"] != "END"]
    return {"titolo": f'{bp["repo"]}: pipeline', "gruppi": gruppi, "blocchi": blocchi, "archi": archi}


LANES = [("Entry point", "arancio"), ("Interface", "arancio"), ("Orchestration", "viola"), ("Service", "blu"), ("Library", "grigio"), ("Data", "giallo"),
         ("Configuration", "giallo"), ("Scripts", "verde"), ("Build & deploy", "verde"), ("Infrastructure", "grigio")]  # top to bottom: who uses whom


def lanes(blocchi, meta):
    """Swimlanes by layer (only when there are at least two): position then says something, and each card takes its lane's color."""
    layer = {b["id"]: meta.get(b["id"], {}).get("layer") for b in blocchi}
    gruppi = [{"id": "l" + str(i), "titolo": name, "colore": col, "nodi": [k for k, v in layer.items() if v == name], "descrizione": "", "direzione": "RIGHT"}
              for i, (name, col) in enumerate(LANES)]
    gruppi = [g for g in gruppi if g["nodi"]]
    if len(gruppi) < 2:
        return []
    color = {n: g["colore"] for g in gruppi for n in g["nodi"]}
    for b in blocchi:
        if b["id"] in color:
            b["colore"] = color[b["id"]]
    return gruppi


def structure_spec(bp, desc):
    nodes = desc["nodes"]["structure"]
    infra = lambda n: 1 - n["py_lines"] / max(1, n["lines"])
    meta = desc.get("meta", {})
    infra_file = lambda f: f.endswith((".tf", ".yaml", ".yml", ".toml"))

    def block(n):
        m = meta.get(n["id"], {})
        size = "complex" if n["lines"] > 3000 else "moderate" if n["lines"] > 500 else "simple"  # from the code, not from the model
        sub = " · ".join(x for x in (m.get("layer"), size, ", ".join(m.get("tags", []))) if x)
        files = [{"id": f"f{i}", "testo": f["path"].rsplit("/", 1)[-1], "tipo": "store" if infra_file(f["path"]) else "step", "descrizione": f["role"],
                  "ancore": [f["path"]]} for i, f in enumerate(m.get("files", []))]
        return {"id": n["id"], "testo": title(n["id"]) + (f"|{sub}" if sub else ""), "descrizione": nodes[n["id"]], "ancore": n["paths"],
                "tipo": "store" if infra(n) > .8 else "ai" if any(w in nodes[n["id"]].lower() for w in ("agent", "llm")) else "step",
                **({"interno": {"titolo": title(n["id"]) + ": key files", "nodi": files, "archi": []}} if files else {})}
    blocchi = [block(n) for n in bp["structure"]["nodes"] if n["id"] in nodes]
    links, archi = desc["edges"].get("structure", {}), []
    for e in bp["structure"]["edges"]:  # only the links the model kept; each carries the literal evidence the extractor found
        cur = links.get(f'{e["source"]}>{e["target"]}')
        if not cur or not cur.get("keep"):
            continue
        a, b = (e["target"], e["source"]) if cur.get("from") == e["target"] else (e["source"], e["target"])
        proof = ("shares " + ", ".join(e["evidence"][:5])) if e["kind"] == "shared" else "; ".join(e["evidence"][:3])
        archi.append([a, b, cur.get("label", ""), cur.get("text", ""), proof, cur.get("type", "depends_on")])  # 6th item: link type
    return {"titolo": f'{bp["repo"]}: structure', "blocchi": blocchi, "archi": archi, "gruppi": lanes(blocchi, meta)}


if __name__ == "__main__":
    bp, desc, out = load(sys.argv[1], "const BP = "), load(sys.argv[2], "const DESC = "), Path(sys.argv[3])
    detail = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8")) if len(sys.argv) > 4 else None
    specs = [("struttura", structure_spec(bp, desc))]
    if bp["pipeline"]["nodes"]:
        specs.insert(0, ("pipeline", pipeline_spec(bp, desc, detail)))
    for name, spec in specs:
        (out / name).mkdir(parents=True, exist_ok=True)
        (out / name / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    print("scritte spec", " e ".join(n for n, _ in specs), "in", out)
