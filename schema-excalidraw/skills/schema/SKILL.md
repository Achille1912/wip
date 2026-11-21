---
name: schema
description: Draws and edits a conceptual Excalidraw schema of the project (a .excalidraw file in docs/). Use to create a schema, change one (look, notes that explain a block or a link, new blocks or links), refresh it after the code changed, or clear its highlights ("/schema ok").
---

The schema is a `.excalidraw` file the user also edits by hand in an Excalidraw editor. You change it only through `schema.mjs`, in this skill's base directory: never write the JSON yourself, and never touch the project's code from here.

```
node <base dir>/schema.mjs show  <file>              # the schema in a few lines, and its rev
node <base dir>/schema.mjs apply <file> --rev <rev>  # operations as a JSON array on stdin (no --rev for a new file)
node <base dir>/schema.mjs ok    <file> --rev <rev>  # clears the highlights
```

Every `apply` prints the schema back with its new `rev`. If it says the file *changed since you read it*, the user edited it: ask them to save in the editor, run `show`, and redo your operations on what is there now.

## Pick the branch

- **No schema yet** (no `docs/schema*.excalidraw`, or the user asks for a new one): *create*.
- **A request about the schema** (colours, wording, an explanation, a block or link to add or remove): *change*.
- **"aggiorna" / "update" / the code changed**: *refresh*.
- **"ok"**: run `show`, then `ok` with that rev. Done.

Several schemas can exist (`docs/schema.excalidraw`, `docs/schema-<topic>.excalidraw`); when the request does not say which, ask.

### Create

1. Ask the user, and wait for the answer: what the schema should focus on (a flow, a part of the system, how some data moves) and how much detail (low ≈ 5-8 blocks, medium ≈ 8-15, high ≈ 15-25). The file is `docs/schema.excalidraw`, or `docs/schema-<topic>.excalidraw` when one already exists.
2. Read the code that the focus covers until every block you plan to draw maps to something real: a module, a service, a store, an external system, a person. A block is a concept the user would name, not a file listing.
3. One `apply` with every block, link, the few notes worth writing, and the `legend`. The script lays it out.
4. Tell the user the path, to open it with the Excalidraw editor (Orca: the `orca-excalidraw` plugin; VS Code: the Excalidraw extension), and to add `*.excalidraw.prev` to `.gitignore`.

### Change

1. `show` the file. Map the request onto the ids it lists: boxes the user drew by hand appear as `drawn by hand` with their Excalidraw id, and you can style, retitle, note or link them like your own.
2. One `apply` with the operations. Positions are the user's: the script only places what is new.
3. Say in one or two lines what changed. The schema already shows it: green = new, orange = changed, grey dashed = removed, until "ok".

### Refresh

1. `show` the file: the `focus` and `detail` line is what the schema is about.
2. Read the code again under that focus and compare it with the blocks and links the schema has.
3. One `apply` with only the differences (new blocks or links, removed ones, titles or notes that are no longer true), then report as in *change*.

## Operations

```json
[
  {"op": "node",   "id": "api", "title": "API server", "kind": "component"},
  {"op": "edge",   "from": "web", "to": "api", "label": "HTTP"},
  {"op": "note",   "on": "api", "text": "Checks every order before saving it"},
  {"op": "style",  "id": "db", "stroke": "#e03131", "fill": "#ffc9c9", "line": "dashed", "width": 2},
  {"op": "remove", "id": "cache"},
  {"op": "legend", "text": "Focus: order flow\nDetail: medium", "focus": "order flow", "detail": "medium",
   "labels": {"component": "Component", "data": "Data", "external": "External", "person": "Person"}}
]
```

- `node`: creates the block, or changes the title or kind of an existing one. `kind` is `component` (blue box), `data` (violet ellipse), `external` (grey dashed box: a system outside the code) or `person` (teal ellipse). Ids are short and ASCII (letters, digits, `. _ : -`).
- `edge`: an arrow `from` → `to`; its id is `from>to`. The label is two or three words saying what travels or happens; `"label": ""` removes it.
- `note`: a sticky note joined to a block or an arrow (`"on": "a>b"`) by a dashed line. Write one where a block or link needs explaining beyond its title, at most two short sentences; `"text": ""` removes it. Notes are how explanations live in this schema.
- `style`: any element listed by `show`. Colours are hex. `line` is solid, dashed or dotted; `width` is 1 to 4.
- `remove`: a block also takes its arrows and notes with it.
- `legend`: the focus and detail the user gave, written in their words, plus the kind names. It is how a later refresh knows what the schema is about.

Write every title, label and note in the language the user wrote the focus in; names of files, classes and services stay as they are.
