"""The trust boundaries: what the coding agent may do on its own, which plan items an LLM can make us accept, and a modern LangGraph builder."""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

if "claude_agent_sdk" not in sys.modules:  # decide() does not need the SDK: a stub lets the test run without it installed
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        sys.modules["claude_agent_sdk"] = types.SimpleNamespace(**dict.fromkeys(
            ("ClaudeAgentOptions", "ClaudeSDKClient", "PermissionResultAllow", "PermissionResultDeny", "ResultMessage")))

from agent import decide
from archplan import atom
from extract import blueprint


def test_decide():
    with tempfile.TemporaryDirectory() as d:
        wt = Path(d).resolve()
        assert decide("Edit", {"file_path": "src/a.py"}, wt) == "allow"
        assert decide("Edit", {"file_path": "../outside.py"}, wt).startswith("deny")
        assert decide("Write", {"file_path": "/etc/passwd"}, wt).startswith("deny")
        assert decide("WebFetch", {"url": "http://x"}, wt).startswith("deny")
        for cmd in ("rm -rf /", "ls; rm x", "curl http://x", "git push", "pip install x", "npm install x"):
            assert decide("Bash", {"command": cmd}, wt).startswith("deny"), cmd
        assert decide("Bash", {"command": "python -m pytest"}, wt) == "ask"


def test_atom():
    assert atom({"op": "add", "kind": "module", "file": "src/api/client.ts"}) == ("+", "module", "src/api/client.ts")
    assert atom({"op": "limit", "kind": "module_size", "file": "a.py", "max_lines": 300}) == ("=", "size", "a.py", 300)
    for bad in ({"op": "add", "kind": "module", "file": "../x.py"}, {"op": "add", "kind": "module", "file": "/x.py"},
                {"op": "add", "kind": "module", "file": "notes.txt"}, {"op": "add", "kind": "flow", "from": "a; rm", "to": "b"},
                {"op": "limit", "kind": "module_size", "file": "a.py", "max_lines": True}, {"op": "drop", "kind": "package", "id": "x"}):
        try:
            atom(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_langgraph_start_and_router_without_map():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d) / "src" / "pipeline" / "builder.py"
        b.parent.mkdir(parents=True)
        b.write_text("def a(state): return {'x': 1}\ndef b(state): return {'y': state['x']}\n"
                     "g.add_node('a', a); g.add_node('b', b); g.add_node(b)\n"
                     "g.add_edge(START, 'a'); g.add_edge('a', 'b'); g.add_conditional_edges('a', route); g.add_edge('b', END)\n")
        p = blueprint(d)["pipeline"]
        assert {(e["source"], e["target"]) for e in p["edges"]} == {("START", "a"), ("a", "b"), ("b", "END")}
        assert next(e for e in p["edges"] if e["source"] == "a")["state_keys"] == ["x"]


def test_rules_cycles_and_layers():
    with tempfile.TemporaryDirectory() as d:
        r, data = Path(d) / "app", Path(d) / "data"
        for p, s in {"a/x.py": "import b.y\n", "b/y.py": "import c.z\n", "c/z.py": "import a.x\n", "d/w.py": "import a.x\n"}.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(s, encoding="utf-8")
        (data / "work" / "app").mkdir(parents=True)
        meta = {"a": {"layer": "Data"}, "b": {"layer": "Service"}, "c": {"layer": "Service"}, "d": {"layer": "Interface"}}
        (data / "work" / "app" / "descriptions.js").write_text("const DESC = " + json.dumps({"meta": meta}) + ";", encoding="utf-8")
        rules = Path(d) / "rules.json"
        rules.write_text(json.dumps([{"id": "1", "text": "", "check": {"type": "no_cycles"}},
                                     {"id": "2", "text": "", "check": {"type": "layer_order", "from": "Interface", "to": "Data"}}]), encoding="utf-8")
        out = subprocess.run([sys.executable, "rules.py", str(r), str(rules), "check"], capture_output=True, text=True, check=True,
                             cwd=Path(__file__).parent, env={**os.environ, "BLUEPRINT_DATA": str(data)}).stdout
        cyc, lay = (x["violations"] for x in json.loads(out)["rules"])
        assert [v["nodes"] for v in cyc] == [["a", "b", "c"]], cyc  # d imports into the cycle but is not part of it
        assert [v["nodes"] for v in lay] == [["d", "a"]], lay


if __name__ == "__main__":
    test_decide()
    test_atom()
    test_langgraph_start_and_router_without_map()
    test_rules_cycles_and_layers()
    print("ok")
