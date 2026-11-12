"""The trust boundaries: what the coding agent may do on its own, which plan items an LLM can make us accept, and a modern LangGraph builder."""
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


if __name__ == "__main__":
    test_decide()
    test_atom()
    test_langgraph_start_and_router_without_map()
    print("ok")
