"""JS/TS imports become component links: relative paths, folder index imports, the @src alias; packages are ignored."""
import tempfile
from pathlib import Path

from extract import flat_structure


def test_js_imports():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d).resolve()
        files = {
            "src/main.tsx": "import App from './App'\nimport { createRoot } from 'react-dom/client'\n",
            "src/App.tsx": "import { Header } from './components'\nimport type { Job } from '@src/api/types'\n",
            "src/components/index.ts": "export * from './Header'\n",
            "src/components/Header.tsx": "import { fetchJobs } from '../api/client'\nconst x = await import('../api/client')\n",
            "src/api/client.ts": "export const fetchJobs = () => fetch('/run')\n",
            "src/api/types.ts": "export interface Job { id: string }\n",
            "server.js": "const api = require('./mock-api/routes')\n",
            "mock-api/routes.js": "module.exports = {}\n",
        }
        for p, t in files.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(t, encoding="utf-8")
        nodes, edges = flat_structure(r)
        ids = sorted(n["id"] for n in nodes)
        links = {(e["source"], e["target"]) for e in edges if e["kind"] == "import"}
        assert ids == ["(root)", "api", "components", "main", "mock-api"], ids
        assert links == {("main", "components"), ("main", "api"), ("components", "api"), ("(root)", "mock-api")}, links


if __name__ == "__main__":
    test_js_imports()
    print("ok")
