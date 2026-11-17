"""Architecture plan: the structural changes a change must produce, written in the same atoms the diff of two extractions yields,
so a plan made BEFORE the code can be checked against the result AFTER it. An atom is a tuple (sign, kind, *args), e.g.
("+", "flow", "a", "b"), ("-", "module", "src/x.py"), ("=", "size", "src/x.py", 800)."""
import re

from extract import CODE_EXT, blueprint, code_files

title = lambda s: s.replace("_", " ").capitalize()
NAME = re.compile(r"[\w.\-/]{1,120}")


def atom(item):
    """A plan item (dict from the LLM) -> atom. ValueError if it is outside the vocabulary or malformed: the plan is a trust boundary."""
    def s(k):
        v = item.get(k)
        if not (isinstance(v, str) and NAME.fullmatch(v)):
            raise ValueError(k)
        return v

    def py(k):  # a repo-relative code file: no absolute path, no ".."
        f = s(k)
        if f.startswith("/") or ".." in f.split("/") or not f.endswith(tuple(CODE_EXT)):
            raise ValueError(k)
        return f

    op, kind = item.get("op"), item.get("kind")
    if op == "limit" and kind == "module_size":
        n = item.get("max_lines")
        if isinstance(n, int) and not isinstance(n, bool) and 50 <= n <= 100000:
            return ("=", "size", py("file"), n)
        raise ValueError("max_lines")
    sign = {"add": "+", "remove": "-"}.get(op)
    if not sign:
        raise ValueError("op")
    if kind == "module":
        return (sign, "module", py("file"))
    if kind in ("component", "package"):
        return (sign, kind, s("id"))
    if kind == "flow":
        return (sign, "flow", s("from"), s("to"))
    if kind == "dependency":
        return (sign, "dep", s("from"), s("to"))
    if kind in ("writes", "reads"):
        return (sign, kind, s("id"), s("key"))
    raise ValueError("kind")


def fmt(a):
    sign, kind, *x = a
    word = {"+": "New", "-": "Removed"}.get(sign)
    if kind == "size":
        return f"= {x[0]} ends with at most {x[1]} lines"
    if kind in ("writes", "reads"):
        return f"~ {title(x[0])} {'now' if sign == '+' else 'no longer'} {kind} {x[1]}"
    if kind in ("flow", "dep"):
        return f"{sign} {word} {'flow' if kind == 'flow' else 'package dependency'}: {title(x[0])} → {title(x[1])}"
    if kind == "module":
        return f"{sign} {word} module: {x[0]}"
    return f"{sign} {word} {kind}: {title(x[0])}"


def check_plan(items, atoms, sizes):
    """Plan vs result: what was planned and found, planned and missing, found but not planned. sizes = {file: line count} after the change."""
    planned, actual = [a for a in map(atom, items) if not (a[1] == "module" and a[2].endswith("__init__.py"))], set(atoms)  # package markers are not tracked as modules
    ok = lambda a: sizes.get(a[2], 0) <= a[3] if a[1] == "size" else a in actual  # a size limit on a file that is gone is met
    return {"done": [fmt(a) for a in planned if ok(a)], "missing": [fmt(a) for a in planned if not ok(a)],
            "unplanned": [fmt(a) for a in atoms if a not in set(planned)]}


def files_of(root):
    """{file: line count} of the project's code files (package markers excluded): module-level changes of a split or a merge."""
    fs = code_files(root)  # the same files the blueprint's components are made of
    return {p.relative_to(root).as_posix(): len(p.read_text(encoding="utf-8-sig", errors="replace").splitlines()) for p in fs if p.name != "__init__.py"}


def extract(root):
    bp = blueprint(root)  # the same extraction that builds the blueprint, whatever the layout
    return (bp["structure"]["nodes"], bp["structure"]["edges"]), (bp["pipeline"]["nodes"], bp["pipeline"]["edges"]), files_of(root)


def arch_atoms(a, b):
    """Architecture changes between two extractions, as atoms (see archplan): packages and their dependencies, components, flows,
    data keys each component reads/writes, and the Python modules that appeared or disappeared."""
    (sn0, se0), (pn0, pe0), f0 = a
    (sn1, se1), (pn1, pe1), f1 = b
    out = []

    def sets(kind, x, y):
        out.extend(("+", kind, *i) for i in sorted(y - x))
        out.extend(("-", kind, *i) for i in sorted(x - y))

    sets("package", {(n["id"],) for n in sn0}, {(n["id"],) for n in sn1})
    sets("dep", {(e["source"], e["target"]) for e in se0}, {(e["source"], e["target"]) for e in se1})
    sets("component", {(n["id"],) for n in pn0}, {(n["id"],) for n in pn1})
    sets("flow", {(e["source"], e["target"]) for e in pe0}, {(e["source"], e["target"]) for e in pe1})
    sets("module", {(f,) for f in f0}, {(f,) for f in f1})
    old = {n["id"]: n for n in pn0}
    for n in pn1:
        for k in ("writes", "reads") if n["id"] in old else ():
            sets(k, {(n["id"], x) for x in old[n["id"]][k]}, {(n["id"], x) for x in n[k]})
    return out
