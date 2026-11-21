// node --test test/   : the script end to end, through its command line, on a temporary file
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SCRIPT = new URL("../skills/schema/schema.mjs", import.meta.url).pathname;
const run = (cmd, file, ops, rev) => execFileSync("node", [SCRIPT, cmd, file, ...(rev ? ["--rev", rev] : [])], { input: ops ? JSON.stringify(ops) : "", encoding: "utf8" });
const revOf = (out) => out.match(/^rev: (\w+)/)[1];
const els = (file) => JSON.parse(fs.readFileSync(file, "utf8")).elements;
const by = (file, id) => els(file).find((e) => e.id === id);
const overlap = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

function fresh() {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "schema-")), "docs", "schema.excalidraw");
  const out = run("apply", file, [
    { op: "node", id: "web", title: "Web app", kind: "person" },
    { op: "node", id: "api", title: "API server" },
    { op: "node", id: "db", title: "Orders database", kind: "data" },
    { op: "edge", from: "web", to: "api", label: "HTTP" },
    { op: "edge", from: "api", to: "db", label: "reads and writes" },
    { op: "note", on: "api", text: "Validates every order before saving it" },
    { op: "legend", text: "Focus: order flow\nDetail: medium", focus: "order flow", detail: "medium", labels: { component: "Component", data: "Data", person: "Person" } },
  ]);
  return { file, rev: revOf(out) };
}

test("a new schema: laid out, bound arrows, a note, a legend, no highlights", () => {
  const { file } = fresh();
  const E = els(file);
  assert.ok(!E.some((e) => e.customData?.schema), "no halos on the first generation");
  assert.ok(!fs.existsSync(file + ".prev"));
  const boxes = ["web", "api", "db", "note:api", "legend"].map((id) => by(file, id));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) assert.ok(!overlap(boxes[i], boxes[j]), `${boxes[i].id} overlaps ${boxes[j].id}`);
  const a = by(file, "api>db");
  assert.equal(a.startBinding.elementId, "api");
  assert.ok(by(file, "api").boundElements.some((b) => b.id === "api>db"), "the box knows its arrow (Excalidraw moves it with the box)");
  assert.equal(by(file, "api>db:t").containerId, "api>db");
  assert.equal(by(file, "db").type, "ellipse");
  assert.match(run("show", file), /focus: order flow \| detail: medium/);
});

test("a change: green new, orange changed, grey ghost removed, user positions kept; ok clears", () => {
  const { file, rev } = fresh();
  const doc = JSON.parse(fs.readFileSync(file, "utf8"));  // the user moves the database by hand and saves
  const db = doc.elements.find((e) => e.id === "db");
  db.x += 400; db.y += 50;
  fs.writeFileSync(file, JSON.stringify(doc));
  assert.throws(() => run("apply", file, [], rev), /changed since you read it/);
  const r2 = revOf(run("show", file));
  const out = run("apply", file, [
    { op: "node", id: "cache", title: "Cache", kind: "data" },
    { op: "edge", from: "api", to: "cache" },
    { op: "style", id: "db", fill: "#ffc9c9" },
    { op: "remove", id: "web" },
  ], r2);
  const E = els(file), mark = (id) => E.find((e) => e.id === `halo:${id}`)?.customData.mark;
  assert.equal(mark("cache"), "new");
  assert.equal(mark("api>cache"), "new");
  assert.equal(mark("db"), "changed");
  assert.equal(mark("api"), undefined, "a box that only gained an arrow is not changed");
  assert.ok(by(file, "ghost:web") && by(file, "ghost:web>api"), "removed box and its arrow stay as ghosts");
  assert.ok(!by(file, "web") && !by(file, "web>api"));
  assert.equal(by(file, "db").x, db.x, "the box the user moved stays where the user put it");
  for (const e of ["api", "db", "note:api"].map((id) => by(file, id))) assert.ok(!overlap(by(file, "cache"), e), `the new box overlaps ${e.id}`);
  assert.match(out, /highlights since the last ok: 2 new, 1 changed, 2 removed/);
  // a second change before ok: highlights still compare with the first baseline
  run("apply", file, [{ op: "node", id: "api", title: "API gateway" }], revOf(out));
  assert.equal(by(file, "halo:cache").customData.mark, "new");
  assert.equal(by(file, "halo:api").customData.mark, "changed");
  run("ok", file, null, revOf(run("show", file)));
  assert.ok(!els(file).some((e) => e.customData?.schema));
  assert.ok(!fs.existsSync(file + ".prev"));
});

test("bad operations are refused and leave the file untouched", () => {
  const { file, rev } = fresh();
  const before = fs.readFileSync(file, "utf8");
  for (const ops of [[{ op: "edge", from: "api", to: "nope" }], [{ op: "node", id: "a>b", title: "x" }], [{ op: "style", id: "api", fill: "red; x" }],
    [{ op: "explode" }], [{ op: "node", id: "x", kind: "spaceship" }]]) {
    assert.throws(() => run("apply", file, ops, rev), undefined, JSON.stringify(ops));
  }
  assert.equal(fs.readFileSync(file, "utf8"), before);
});

test("a box that grows keeps the routed arrows' bends and square ends; labels sit where Excalidraw draws them", () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "schema-")), "s.excalidraw");
  const ids = ["a", "b", "c", "d", "e", "f", "g", "h", "i"];  // more than 8 boxes: laid out top-down, with bends
  const out = run("apply", file, [...ids.map((id) => ({ op: "node", id, title: id.toUpperCase() })),
    ...[["a", "b"], ["a", "c"], ["a", "i"], ["b", "d"], ["c", "e"], ["d", "f"], ["e", "f"], ["f", "g"], ["g", "h"], ["c", "h"]].map(([from, to]) => ({ op: "edge", from, to, label: "x" }))]);
  const bent = els(file).filter((e) => e.type === "arrow" && e.points.length > 2);
  assert.ok(bent.length, "the layout produced some bends");
  run("apply", file, ids.map((id) => ({ op: "node", id, title: `${id.toUpperCase()} with a much longer title that wraps` })), revOf(out));
  for (const a of bent) {
    const now = by(file, a.id);
    assert.equal(now.points.length, a.points.length, `${a.id} lost its bends`);
    const [p0, p1] = now.points;
    assert.ok(Math.abs(p0[0] - p1[0]) < 0.5 || Math.abs(p0[1] - p1[1]) < 0.5, `${a.id} leaves its box at a slant`);
    const t = by(file, `${a.id}:t`), P = now.points, k = P.length >> 1, [m, n] = P.length % 2 ? [P[k], P[k]] : [P[k - 1], P[k]];
    assert.ok(Math.abs(t.x + t.width / 2 - (now.x + (m[0] + n[0]) / 2)) < 0.5, `${a.id}'s label is off the path's middle`);
  }
});

test("notes on arrows and removing a note", () => {
  const { file, rev } = fresh();
  const out = run("apply", file, [{ op: "note", on: "api>db", text: "One transaction per order" }, { op: "note", on: "api", text: "" }], rev);
  assert.ok(by(file, "note:api>db") && by(file, "note:api>db:l"));
  assert.ok(!by(file, "note:api") && !by(file, "note:api:l"));
  assert.match(out, /on api>db: "One transaction per order"/);
});
