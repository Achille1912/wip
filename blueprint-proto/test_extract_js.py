"""JS/TS and Dart imports become component links: relative paths, folder index imports, the @src alias, package:<app>/; packages are ignored."""
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



def test_dart_flutter():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d).resolve()
        files = {
            "pubspec.yaml": "name: shop\ndependencies:\n  flutter:\n    sdk: flutter\n",
            "lib/main.dart": "import 'package:flutter/material.dart';\nimport 'views/home.dart';\n",
            "lib/views/home.dart": "import '../models/item.dart';\nimport 'dart:io';\nclass Home {}\n",
            "lib/models/item.dart": "class Item {}\n",
            "test/item_test.dart": "import 'package:shop/models/item.dart';\n",
            "android/settings.gradle.kts": 'plugins { id("dev.flutter.flutter-plugin-loader") }\n',
        }
        for p, t in files.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(t, encoding="utf-8")
        nodes, edges = flat_structure(r)
        assert sorted(n["id"] for n in nodes) == ["(root)", "android", "main", "models", "test", "views"], nodes
        links = {(e["source"], e["target"], e["kind"]) for e in edges}
        assert {("main", "views", "import"), ("views", "models", "import"), ("test", "models", "import"), ("android", "main", "mention")} <= links, links


if __name__ == "__main__":
    test_js_imports()
    test_dart_flutter()
    print("ok")
