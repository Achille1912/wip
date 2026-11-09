"""Reviews the generated blueprint before it becomes a spec: rewrites generic descriptions once, reports what is still weak.
Usage: review.py <repo> <blueprint.js> <descriptions.js> <report.json>. Errors = the graph is wrong; warnings = it is poor."""
import json
import re
import sys
from pathlib import Path

from describe import LANG, STYLE, ask, project

load = lambda p, pre: json.loads(Path(p).read_text(encoding="utf-8").removeprefix(pre).removesuffix(";"))
GENERIC = re.compile(r"^(this|the) (directory|folder|component|module|package)\b|^(utility|utilities|helper|misc)\b|\bvarious\b|\bmiscellaneous\b", re.I)
EXPECTS = {"Build & deploy": {"builds", "deploys"}, "Infrastructure": {"provisions", "deploys"}}  # a component of this layer should have such a link


def generic(text, name):
    t = text.strip()
    return len(t) < 45 or t.lower().replace("_", " ") == name.lower().replace("_", " ") or bool(GENERIC.search(t))


def main(repo, bp_file, desc_file, report_file):
    bp, desc = load(bp_file, "const BP = "), load(desc_file, "const DESC = ")
    ctx, warn, err = project(repo), [], []
    for kind in ("pipeline", "structure"):  # 1. generic descriptions are rewritten once, with the weakness named
        for k, text in desc["nodes"][kind].items():
            if not generic(text, k):
                continue
            new = ask(f"The description below of '{k}' ({ctx}) is too generic to tell an engineer anything. Rewrite it so it names the concrete role "
                      f"of '{k}' in this project and why it exists. {STYLE}\n\nWeak description: {text}", model="sonnet").strip()
            if not generic(new, k):
                desc["nodes"][kind][k] = new
            else:
                warn.append(f"{kind} '{k}': description is still generic")
    for k, text in desc["nodes"]["structure"].items():  # 2. every component must say what it is made of
        if not desc.get("meta", {}).get(k, {}).get("tags") and bp.get("layout") == "flat":
            warn.append(f"component '{k}': no tags")
    names = {n["id"] for n in bp["structure"]["nodes"]}
    kept = {k: v for k, v in desc["edges"].get("structure", {}).items() if v.get("keep")}
    linked = {x for k in kept for x in k.split(">")}
    ev = {f'{e["source"]}>{e["target"]}': e.get("evidence", ["imports"]) for e in bp["structure"]["edges"]}  # src layout: imports counted by the extractor
    for k in kept:  # 3. referential integrity and proof
        if not all(x in names for x in k.split(">")):
            err.append(f"link {k}: points to a component that does not exist")
        elif not ev.get(k):
            err.append(f"link {k}: no evidence in the code")
    if bp.get("layout") == "flat":
        warn += [f"component '{n}': no link to anything (orphan)" for n in sorted(names - linked)]
        for n in names:  # 4. a build/infra component with no matching link is suspicious (either way: infra is often what gets deployed)
            want, layer = EXPECTS.get(desc.get("meta", {}).get(n, {}).get("layer", "")), desc.get("meta", {}).get(n, {}).get("layer")
            outs = {v.get("type") for k, v in kept.items() if n in k.split(">")}
            if want and not outs & want:
                warn.append(f"component '{n}' ({layer}): no {' or '.join(sorted(want))} link found")
    pn = [n["id"] for n in bp["pipeline"]["nodes"] if n["id"] not in ("START", "END")]
    pe = {x for e in bp["pipeline"]["edges"] for x in (e["source"], e["target"])}
    warn += [f"graph node '{n}': not connected" for n in pn if n not in pe]
    Path(desc_file).write_text("const DESC = " + json.dumps(desc, indent=1, ensure_ascii=False) + ";", encoding="utf-8")
    Path(report_file).write_text(json.dumps({"errors": err, "warnings": warn}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"review: {len(err)} errors, {len(warn)} warnings" + "".join("\n  ! " + m for m in err + warn))
    return not err


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3], sys.argv[4]) else 1)
