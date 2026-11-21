# excalidraw-schema

A Claude Code plugin: Claude draws a conceptual schema of your project as an `.excalidraw` file, you edit it by hand in any Excalidraw editor (Orca's `orca-excalidraw` plugin, the VS Code Excalidraw extension, excalidraw.com), and you ask Claude for changes from the chat. After each change the schema shows what is new (green), changed (orange) and removed (grey dashed) until you say `ok`.

## Install

```
claude plugin marketplace add Achille1912/wip
claude plugin install excalidraw-schema@excalidraw-schema
```

After a push, `claude plugin marketplace update excalidraw-schema` brings the new version (raise `version` in `.claude-plugin/plugin.json` if it does not show up).

Needs Node 18 or newer. Nothing else to install.

## Use

- `/schema`: Claude asks what to focus on and how much detail, then writes `docs/schema.excalidraw`.
- `/schema make the database red and explain the login flow`: changes the schema and highlights the changes.
- `/schema update`: reads the code again under the same focus and shows what differs.
- `/schema ok`: clears the highlights.

Save the file in the editor before asking Claude for a change: the script refuses to write over edits it has not seen.

## How it works

`skills/schema/SKILL.md` tells Claude what to do; `skills/schema/schema.mjs` is the only thing that writes the file. It takes small operations (`node`, `edge`, `note`, `style`, `remove`, `legend`), lays out the first version with ELK, places later blocks next to what they link to without moving anything you placed, and computes the highlights against `<file>.prev`, the version at the last `ok`.

Tests: `node --test test/schema.test.mjs`
