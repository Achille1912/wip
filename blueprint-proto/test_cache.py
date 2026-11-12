"""Incremental refresh: global LLM calls depend on the graph's shape, not on reworded descriptions. No real LLM call is made."""
import hashlib
import json
import tempfile
import types
from unittest import mock
from pathlib import Path

import describe


def test_structure_keys():
    calls = []

    def fake_run(cmd, input, **kw):
        calls.append(input)
        return types.SimpleNamespace(returncode=0, stdout='{"a->b|": {"data": "x", "when": ""}}', stderr="")

    with tempfile.TemporaryDirectory() as d, mock.patch.object(describe.subprocess, "run", fake_run), \
            mock.patch.object(describe, "CACHE_FILE", Path(d) / "c.json"), mock.patch.object(describe, "_cache", {}):
        edges = [{"source": "a", "target": "b", "label": "", "state_keys": ["k"]}]
        describe.describe_edges(edges, {"a": "reads files", "b": "writes a report"})
        describe.describe_edges(edges, {"a": "loads the input files", "b": "writes a report"})  # only a description changed
        assert len(calls) == 1, calls
        describe.describe_edges([{**edges[0], "state_keys": ["k", "j"]}], {"a": "reads files", "b": "writes a report"})  # what travels changed
        assert len(calls) == 2

        # an answer saved under the old full-prompt key is reused and persisted under the new key
        describe._cache = {hashlib.sha256(b"old prompt").hexdigest(): "kept"}
        assert describe.ask("old prompt", key="shape") == "kept" and len(calls) == 2
        describe._cache = json.loads(describe.CACHE_FILE.read_text(encoding="utf-8"))
        assert describe.ask("reworded prompt", key="shape") == "kept" and len(calls) == 2


if __name__ == "__main__":
    test_structure_keys()
    print("ok")
