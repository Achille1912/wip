#!/usr/bin/env node
// The only way Claude touches a .excalidraw schema: small operations instead of hand-written Excalidraw JSON.
//   node schema.mjs show  <file>                 the schema in a few lines, plus its rev
//   node schema.mjs apply <file> [--rev REV]     operations (a JSON array) on stdin; REV from the last show/apply (not for a new file)
//   node schema.mjs ok    <file> --rev REV       clears the highlights and forgets the baseline
// Highlights: every apply compares the schema with the baseline (<file>.prev, saved by the first apply after an ok) and marks
// new parts green, changed ones orange (text, colour, style, links; not moves), removed ones as grey dashed ghosts.
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import { createRequire } from "node:module";

const KINDS = {  // the look of each kind of block: shape and a soft colour pair
  component: { type: "rectangle", stroke: "#1971c2", bg: "#d0ebff", line: "solid" },
  data: { type: "ellipse", stroke: "#6741d9", bg: "#e5dbff", line: "solid" },
  external: { type: "rectangle", stroke: "#495057", bg: "#f1f3f5", line: "dashed" },
  person: { type: "ellipse", stroke: "#0c8599", bg: "#c5f6fa", line: "solid" },
};
const NOTE = { stroke: "#adb5bd", bg: "#fff9db" };
const HALO = { new: "#2f9e44", changed: "#f76707" };
const GHOST = "#868e96";
const SHAPES = new Set(["rectangle", "ellipse", "diamond"]);
const ID = /^[\w.:-]{1,80}$/;  // ">" is reserved: an edge is "<from>><to>"
const COLOR = /^(#[0-9a-fA-F]{3,8}|transparent|[a-z]{3,20})$/;
const FONT = 1;  // Virgil: the hand-drawn font every Excalidraw version reads

const die = (msg) => { process.stderr.write(msg + "\n"); process.exit(1); };
const rnd = () => crypto.randomInt(1, 2 ** 31);
const rev = (s) => crypto.createHash("sha256").update(s).digest("hex").slice(0, 12);
const role = (e) => e.customData?.role;
const temp = (e) => e.customData?.schema === "halo" || e.customData?.schema === "ghost";

function base(type, id, extra = {}) {
  return { id, type, x: 0, y: 0, width: 10, height: 10, angle: 0, strokeColor: "#1e1e1e", backgroundColor: "transparent", fillStyle: "solid",
    strokeWidth: 2, strokeStyle: "solid", roughness: 1, opacity: 100, groupIds: [], frameId: null, roundness: null, seed: rnd(), version: 1,
    versionNonce: rnd(), isDeleted: false, boundElements: null, updated: Date.now(), link: null, locked: false, ...extra };
}

function wrap(s, n) {  // word wrap at n characters, keeping the author's own line breaks
  return String(s).split("\n").map((line) => {
    const out = [""];
    for (const w of line.split(/\s+/).filter(Boolean)) {
      if (out.at(-1) && (out.at(-1) + " " + w).length > n) out.push(w);
      else out[out.length - 1] = out.at(-1) ? out.at(-1) + " " + w : w;
    }
    return out.join("\n");
  }).join("\n");
}
const measure = (s, size) => { const L = s.split("\n"); return [Math.max(...L.map((l) => l.length)) * size * 0.6, L.length * size * 1.25]; };

class Doc {
  constructor(json) {
    this.json = json || { type: "excalidraw", version: 2, source: "schema-excalidraw", elements: [], appState: { viewBackgroundColor: "#ffffff", gridSize: null }, files: {} };
    this.els = this.json.elements.filter((e) => !e.isDeleted);
    this.placed = new Set(this.els.map((e) => e.id));  // everything already in the file keeps its position
    this.touched = new Set();  // arrows to route again
  }
  get(id) { return this.els.find((e) => e.id === id); }
  textOf(id) { return this.els.find((e) => e.type === "text" && e.containerId === id); }
  add(e) { this.els.push(e); return e; }
  bind(holder, id, type) {
    holder.boundElements = [...(holder.boundElements || []).filter((b) => b.id !== id), { id, type }];
  }
  drop(ids) {
    const gone = new Set(ids);
    this.els = this.els.filter((e) => !gone.has(e.id));
    for (const e of this.els) {
      if (e.boundElements) e.boundElements = e.boundElements.filter((b) => !gone.has(b.id));
      if (e.startBinding && gone.has(e.startBinding.elementId)) e.startBinding = null;
      if (e.endBinding && gone.has(e.endBinding.elementId)) e.endBinding = null;
    }
  }
  bump(e) { e.version = (e.version || 1) + 1; e.versionNonce = rnd(); e.updated = Date.now(); }

  setText(holder, s, size, width) {
    const text = wrap(s, width);
    let t = this.textOf(holder.id);
    if (!t) {
      t = this.add(base("text", holder.id + ":t", { text: "", fontSize: size, fontFamily: FONT, textAlign: "center", verticalAlign: "middle",
        containerId: holder.id, originalText: "", lineHeight: 1.25, autoResize: true }));
      this.bind(holder, t.id, "text");
    }
    const [w, h] = measure(text, size);
    Object.assign(t, { text, originalText: text, fontSize: size, width: w, height: h, strokeColor: t.strokeColor || "#1e1e1e" });
    this.bump(t);
    if (holder.type !== "arrow") {  // the box grows to fit its text, around its own centre
      const pad = holder.type === "rectangle" ? [44, 36] : [80, 56];
      const nw = Math.max(holder.type === "rectangle" ? 170 : 190, w + pad[0]), nh = Math.max(70, h + pad[1]);
      holder.x += (holder.width - nw) / 2; holder.y += (holder.height - nh) / 2;
      holder.width = nw; holder.height = nh;
    }
    this.center(t, holder);
    return t;
  }
  center(t, holder) {
    if (holder.type === "arrow") {  // where Excalidraw puts an arrow label: the middle point, or the middle of the middle segment
      const P = holder.points, k = P.length >> 1, [a, b] = P.length % 2 ? [P[k], P[k]] : [P[k - 1], P[k]];
      t.x = holder.x + (a[0] + b[0]) / 2 - t.width / 2; t.y = holder.y + (a[1] + b[1]) / 2 - t.height / 2;
    } else { t.x = holder.x + (holder.width - t.width) / 2; t.y = holder.y + (holder.height - t.height) / 2; }
  }
  look(e, kind) {
    const k = KINDS[kind];
    Object.assign(e, { type: k.type, strokeColor: k.stroke, backgroundColor: k.bg, strokeStyle: k.line, roundness: k.type === "rectangle" ? { type: 3 } : null });
    e.customData = { ...e.customData, kind };
  }
  edgesOf(id) { return this.els.filter((e) => e.type === "arrow" && !temp(e) && (e.startBinding?.elementId === id || e.endBinding?.elementId === id)); }

  // ---- operations ----
  node({ id, title, kind }) {
    if (!ID.test(id || "")) die(`node: bad id ${JSON.stringify(id)} (letters, digits, . _ : - only)`);
    if (kind !== undefined && !KINDS[kind]) die(`node ${id}: kind must be one of ${Object.keys(KINDS).join(", ")}`);
    let e = this.get(id);
    if (e && !SHAPES.has(e.type)) die(`node ${id}: that id is a ${e.type}, not a box`);
    if (!e) { e = this.add(base("rectangle", id, { width: 170, height: 70 })); this.look(e, kind || "component"); }
    else if (kind) this.look(e, kind);
    if (typeof title === "string") this.setText(e, title, 20, 24);
    this.bump(e);
    for (const a of this.edgesOf(id)) this.touched.add(a.id);
  }
  edge({ from, to, label, id }) {
    for (const x of [from, to]) if (!SHAPES.has(this.get(x)?.type)) die(`edge: ${JSON.stringify(x)} is not a box in the schema`);
    id = id || `${from}>${to}`;
    let a = this.get(id);
    if (!a) {
      a = this.add(base("arrow", id, { points: [[0, 0], [1, 0]], startBinding: { elementId: from, focus: 0, gap: 8 }, endBinding: { elementId: to, focus: 0, gap: 8 },
        startArrowhead: null, endArrowhead: "arrow", lastCommittedPoint: null, roundness: { type: 2 }, elbowed: false }));
      this.bind(this.get(from), id, "arrow"); this.bind(this.get(to), id, "arrow");
      this.touched.add(id);
    } else if (a.type !== "arrow") die(`edge: ${id} is not an arrow`);
    if (typeof label === "string") {
      if (label) this.setText(a, label, 16, 28);
      else { const t = this.textOf(id); if (t) this.drop([t.id]); }
    }
    this.bump(a);
  }
  note({ on, text }) {
    const target = this.get(on);
    if (!target || temp(target)) die(`note: nothing called ${JSON.stringify(on)} in the schema`);
    const id = `note:${on}`, link = `${id}:l`;
    if (!text) return this.drop([id, id + ":t", link]);
    let n = this.get(id);
    if (!n) {
      n = this.add(base("rectangle", id, { width: 260, height: 70, strokeColor: NOTE.stroke, backgroundColor: NOTE.bg, roundness: { type: 3 }, customData: { role: "note", on } }));
      const l = this.add(base("arrow", link, { points: [[0, 0], [1, 0]], strokeColor: NOTE.stroke, strokeStyle: "dashed", strokeWidth: 1, startArrowhead: null,
        endArrowhead: null, startBinding: { elementId: id, focus: 0, gap: 4 }, lastCommittedPoint: null, roundness: { type: 2 }, elbowed: false,
        endBinding: SHAPES.has(target.type) ? { elementId: on, focus: 0, gap: 4 } : null, customData: { role: "link", on } }));
      this.bind(n, l.id, "arrow");
      if (SHAPES.has(target.type)) this.bind(target, l.id, "arrow");
      this.touched.add(link);
    }
    this.setText(n, text, 16, 30);
    this.bump(n);
  }
  style({ id, stroke, fill, line, width }) {
    const e = this.get(id);
    if (!e || temp(e)) die(`style: nothing called ${JSON.stringify(id)}`);
    for (const c of [stroke, fill]) if (c !== undefined && !COLOR.test(c)) die(`style ${id}: bad colour ${JSON.stringify(c)}`);
    if (line !== undefined && !["solid", "dashed", "dotted"].includes(line)) die(`style ${id}: line is solid, dashed or dotted`);
    if (width !== undefined && !(width >= 1 && width <= 4)) die(`style ${id}: width is 1 to 4`);
    if (stroke) e.strokeColor = stroke;
    if (fill) e.backgroundColor = fill;
    if (line) e.strokeStyle = line;
    if (width) e.strokeWidth = width;
    this.bump(e);
  }
  remove({ id }) {
    const e = this.get(id);
    if (!e) die(`remove: nothing called ${JSON.stringify(id)}`);
    const ids = new Set([id]);
    for (const a of SHAPES.has(e.type) ? this.edgesOf(id) : []) if (role(a) !== "link" || a.customData.on === id) ids.add(a.id);
    for (const x of [...ids]) for (const y of [`note:${x}`, `note:${x}:t`, `note:${x}:l`]) ids.add(y);
    if (role(e) === "note") ids.add(`${id}:l`);
    for (const t of this.els) if (t.type === "text" && ids.has(t.containerId)) ids.add(t.id);
    this.drop([...ids]);
  }
  legend({ text, focus, detail, labels = {} }) {
    this.drop(this.els.filter((e) => role(e) === "legend" || role(e) === "legend-item").map((e) => e.id));
    const g = ["legend"], box = this.add(base("rectangle", "legend", { strokeColor: "#ced4da", roundness: { type: 3 }, groupIds: g,
      customData: { role: "legend", focus: String(focus || ""), detail: String(detail || "") } }));
    const t = this.add(base("text", "legend:t", { text: wrap(text || focus || "", 44), fontSize: 16, fontFamily: FONT, textAlign: "left", verticalAlign: "top",
      containerId: null, lineHeight: 1.25, autoResize: true, groupIds: g, customData: { role: "legend-item" } }));
    t.originalText = t.text; [t.width, t.height] = measure(t.text, 16);
    const items = Object.keys(KINDS).filter((k) => labels[k]).map((k) => {
      const s = this.add(base(KINDS[k].type, `legend:${k}`, { width: 28, height: 20, strokeColor: KINDS[k].stroke, backgroundColor: KINDS[k].bg,
        strokeStyle: KINDS[k].line, groupIds: g, customData: { role: "legend-item" } }));
      const l = this.add(base("text", `legend:${k}:t`, { text: String(labels[k]), originalText: String(labels[k]), fontSize: 16, fontFamily: FONT, textAlign: "left",
        verticalAlign: "top", containerId: null, lineHeight: 1.25, autoResize: true, groupIds: g, customData: { role: "legend-item" } }));
      [l.width, l.height] = measure(l.text, 16);
      return [s, l];
    });
    this.legendParts = { box, t, items };
  }
}

// ---- geometry ----
const box = (e) => [e.x, e.y, e.x + e.width, e.y + e.height];
const hit = (a, b, m) => a[0] < b[2] + m && b[0] < a[2] + m && a[1] < b[3] + m && b[1] < a[3] + m;
const solid = (d) => d.els.filter((e) => e.type !== "arrow" && e.type !== "line" && !(e.type === "text" && e.containerId) && d.placed.has(e.id));
const along = (a) => a.points.slice(1).flatMap(([x, y], i) => {  // points every ~15px along an arrow, to keep new things off its path
  const [px, py] = a.points[i], n = Math.ceil(Math.hypot(x - px, y - py) / 15);
  return Array.from({ length: n + 1 }, (_, k) => [a.x + px + (x - px) * k / n, a.y + py + (y - py) * k / n]);
});
const free = (d, r, skip) => !solid(d).some((e) => e.id !== skip && hit(r, box(e), 30))
  && !d.els.some((a) => a.type === "arrow" && d.placed.has(a.id) && !temp(a) && along(a).some(([x, y]) => hit(r, [x, y, x, y], 12)));

function spot(d, e, anchor) {  // the first free place around the anchor, then further out
  const [ax, ay, bx, by] = anchor ? box(anchor) : [0, 0, 0, 0];
  for (let ring = 0; ring < 30; ring++) {
    const g = 90 + ring * 60;
    for (const [x, y] of [[bx + g, ay], [ax, by + g], [ax - g - e.width, ay], [ax, ay - g - e.height], [bx + g, by + g], [ax - g - e.width, by + g]]) {
      if (free(d, [x, y, x + e.width, y + e.height], e.id)) return [x, y];
    }
  }
  return [bx + 90, by + 90];
}

function noteSpot(d, n, at) {  // nearest free place on any side of the target, whose straight dashed line to it crosses no other box
  const cx = at.x + at.width / 2, cy = at.y + at.height / 2;
  for (let g = 40; g <= 640; g += 40) {
    for (const [x, y] of [[at.x + at.width + g, cy - n.height / 2], [at.x - g - n.width, cy - n.height / 2],
      [cx - n.width / 2, at.y - g - n.height], [cx - n.width / 2, at.y + at.height + g]]) {
      if (!free(d, [x, y, x + n.width, y + n.height], n.id)) continue;
      const mx = x + n.width / 2, my = y + n.height / 2, line = Array.from({ length: 21 }, (_, k) => [mx + (cx - mx) * k / 20, my + (cy - my) * k / 20]);
      if (!solid(d).some((e) => e.id !== n.customData.on && e.id !== n.id && !role(e)?.startsWith("legend") && line.some(([px, py]) => hit([px, py, px, py], box(e), 4)))) return [x, y];
    }
  }
}

function ortho(e, b) {  // the end of a routed arrow: straight out of the box towards its first bend, so the path stays square when the box resizes
  if (b[0] > e.x && b[0] < e.x + e.width) return [b[0], b[1] > e.y + e.height / 2 ? e.y + e.height : e.y];
  if (b[1] > e.y && b[1] < e.y + e.height) return [b[0] > e.x + e.width / 2 ? e.x + e.width : e.x, b[1]];
  return clip(e, b[0], b[1], 0);
}

function clip(e, tx, ty, gap) {  // where the line from e's centre towards (tx, ty) leaves e's outline
  const cx = e.x + e.width / 2, cy = e.y + e.height / 2, dx = tx - cx, dy = ty - cy, hw = e.width / 2 + gap, hh = e.height / 2 + gap;
  if (!dx && !dy) return [cx, cy];
  const s = e.type === "ellipse" ? 1 / Math.hypot(dx / hw, dy / hh) : e.type === "diamond" ? 1 / (Math.abs(dx) / hw + Math.abs(dy) / hh)
    : Math.min(dx ? hw / Math.abs(dx) : Infinity, dy ? hh / Math.abs(dy) : Infinity);
  return [cx + dx * s, cy + dy * s];
}

function route(d, a) {
  const s = d.get(a.startBinding?.elementId), t = d.get(a.endBinding?.elementId);
  let p, q;
  const bends = a.points.length > 2 ? a.points.slice(1, -1).map(([x, y]) => [a.x + x, a.y + y]) : [];  // a routed arrow keeps its bends
  if (s && t) { p = bends.length ? ortho(s, bends[0]) : clip(s, t.x + t.width / 2, t.y + t.height / 2, 8); q = bends.length ? ortho(t, bends.at(-1)) : clip(t, s.x + s.width / 2, s.y + s.height / 2, 8); }
  else if (s && role(a) === "link") {  // a note on an arrow: point at the arrow's middle
    const on = d.get(a.customData.on), m = [on.x + (on.points[0][0] + on.points.at(-1)[0]) / 2, on.y + (on.points[0][1] + on.points.at(-1)[1]) / 2];
    p = clip(s, m[0], m[1], 4); q = m;
  } else return;
  const pts = [p, ...bends, q];
  Object.assign(a, { x: p[0], y: p[1], points: pts.map(([x, y]) => [x - p[0], y - p[1]]),
    width: Math.max(...pts.map((v) => v[0])) - Math.min(...pts.map((v) => v[0])), height: Math.max(...pts.map((v) => v[1])) - Math.min(...pts.map((v) => v[1])) });
  const lab = d.textOf(a.id);
  if (lab) d.center(lab, a);
}

async function place(d, firstTime) {
  const fresh = (e) => !d.placed.has(e.id);
  const boxes = d.els.filter((e) => SHAPES.has(e.type) && !role(e) && !temp(e));
  const edges = d.els.filter((e) => e.type === "arrow" && !role(e) && !temp(e) && e.startBinding && e.endBinding);
  if (firstTime && boxes.length) {  // first generation: a layered layout of the whole graph
    const ELK = createRequire(import.meta.url)("./vendor/elk.bundled.js");
    const down = boxes.length > 8;
    const g = await new ELK().layout({ id: "root", layoutOptions: { "elk.algorithm": "layered", "elk.direction": down ? "DOWN" : "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL", "elk.edgeLabels.placement": "CENTER", "elk.spacing.nodeNode": "60", "elk.spacing.edgeLabel": "8",
      "elk.layered.spacing.nodeNodeBetweenLayers": "70", "elk.layered.spacing.edgeNodeBetweenLayers": "30", "elk.padding": "[top=40,left=40,bottom=40,right=40]" },
      children: boxes.map((e) => ({ id: e.id, width: e.width, height: e.height })),
      edges: edges.map((e) => {  // labels go to ELK too: it keeps room for them and routes the arrows around boxes
        const t = d.textOf(e.id);
        return { id: e.id, sources: [e.startBinding.elementId], targets: [e.endBinding.elementId], labels: t ? [{ id: t.id, width: t.width + 12, height: t.height + 6 }] : [] };
      }) });
    for (const c of g.children) Object.assign(d.get(c.id), { x: c.x, y: c.y });
    boxes.forEach((e) => d.placed.add(e.id));
    for (const ge of g.edges) {  // ELK's route (with its bends) instead of a straight line through other boxes
      const a = d.get(ge.id), sec = ge.sections?.[0];
      if (!sec) continue;
      const pts = [sec.startPoint, ...(sec.bendPoints || []), sec.endPoint].map((p) => [p.x, p.y]);
      Object.assign(a, { roundness: null, x: pts[0][0], y: pts[0][1], points: pts.map(([x, y]) => [x - pts[0][0], y - pts[0][1]]),
        width: Math.max(...pts.map((p) => p[0])) - Math.min(...pts.map((p) => p[0])), height: Math.max(...pts.map((p) => p[1])) - Math.min(...pts.map((p) => p[1])) });
      const lab = d.textOf(a.id);
      if (lab) { d.center(lab, a); d.placed.add(lab.id); }  // Excalidraw draws an arrow label at the path's middle whatever x/y say: store that
      d.touched.delete(a.id);
      d.placed.add(a.id);
    }
  }
  for (const e of boxes.filter(fresh)) {  // later: a new box goes next to a box it is linked to, never moving anything else
    const near = edges.map((a) => a.startBinding.elementId === e.id ? a.endBinding.elementId : a.endBinding.elementId === e.id ? a.startBinding.elementId : null)
      .map((id) => id && d.get(id)).find((x) => x && d.placed.has(x.id));
    const all = solid(d);
    const anchor = near || (all.length ? { x: Math.max(...all.map((x) => x.x + x.width)), y: Math.min(...all.map((x) => x.y)), width: 0, height: 0 } : null);
    [e.x, e.y] = spot(d, e, anchor);
    d.placed.add(e.id);
  }
  for (const a of edges) if (d.touched.has(a.id) || fresh(a)) route(d, a);
  for (const n of d.els.filter((e) => role(e) === "note" && fresh(e))) {
    const on = d.get(n.customData.on);
    const at = on.type === "arrow" ? { x: on.x + on.points.at(-1)[0] / 2, y: on.y + on.points.at(-1)[1] / 2, width: 0, height: 0 } : on;
    [n.x, n.y] = noteSpot(d, n, at) || spot(d, n, at);
    d.placed.add(n.id);
  }
  for (const l of d.els.filter((e) => role(e) === "link")) if (d.touched.has(l.id) || fresh(l) || fresh(d.get(l.startBinding?.elementId) || {})) route(d, l);
  for (const e of d.els) if (e.type === "text" && e.containerId && (fresh(e) || fresh(d.get(e.containerId) || {}))) d.center(e, d.get(e.containerId));
  if (d.legendParts) {  // the legend sits above the top-left corner of the drawing
    const { box: b, t, items } = d.legendParts, rest = solid(d).filter((e) => !role(e)?.startsWith("legend"));
    const x = rest.length ? Math.min(...rest.map((e) => e.x)) : 0, y = rest.length ? Math.min(...rest.map((e) => e.y)) : 0;
    const h = t.height + (items.length ? 40 : 0) + 32;
    Object.assign(b, { x, y: y - h - 50, width: Math.max(t.width, items.reduce((s, [, l]) => s + l.width + 52, 0)) + 40, height: h });
    Object.assign(t, { x: x + 20, y: b.y + 16 });
    let ix = x + 20;
    for (const [s, l] of items) { Object.assign(s, { x: ix, y: b.y + t.height + 30 }); Object.assign(l, { x: ix + 36, y: s.y }); ix += l.width + 52; }
    for (const e of [b, t, ...items.flat()]) d.placed.add(e.id);
  }
}

// ---- highlights ----
const sig = (e, d) => JSON.stringify([e.type, e.strokeColor, e.backgroundColor, e.strokeStyle, e.fillStyle, e.strokeWidth, e.customData?.kind,
  e.startBinding?.elementId, e.endBinding?.elementId, d.textOf(e.id)?.text]);
const counted = (e) => !temp(e) && !(e.type === "text" && e.containerId) && !["link", "legend", "legend-item"].includes(role(e));

function highlight(d, baseline) {
  d.els = d.els.filter((e) => !temp(e));
  if (!baseline) return;
  const old = new Doc(baseline), before = new Map(old.els.filter(counted).map((e) => [e.id, sig(e, old)]));
  const marks = new Map();
  for (const e of d.els.filter(counted)) {
    if (!before.has(e.id)) marks.set(e.id, "new");
    else if (before.get(e.id) !== sig(e, d)) marks.set(e.id, "changed");
  }
  const out = [];
  for (const e of old.els.filter((e) => counted(e) && !d.get(e.id))) {  // removed: a grey dashed ghost of the box and its text
    out.push({ ...e, id: `ghost:${e.id}`, strokeColor: GHOST, backgroundColor: "transparent", strokeStyle: "dashed", opacity: 45, locked: true, boundElements: null,
      startBinding: null, endBinding: null, groupIds: [], customData: { schema: "ghost" } });
    const t = old.textOf(e.id);
    if (t && e.type === "arrow") old.center(t, e);  // a free ghost text is drawn at its x/y, an arrow label was drawn at the path's middle
    if (t) out.push({ ...t, id: `ghost:${t.id}`, containerId: null, strokeColor: GHOST, opacity: 45, locked: true, groupIds: [], customData: { schema: "ghost" } });
  }
  for (const e of d.els) {
    const m = marks.get(e.id);
    if (m) {
      const g = e.type === "arrow" ? 0 : 9;
      out.push({ ...base(e.type === "arrow" ? "arrow" : SHAPES.has(e.type) ? e.type : "rectangle", `halo:${e.id}`), x: e.x - g, y: e.y - g, width: e.width + 2 * g, height: e.height + 2 * g,
        strokeColor: HALO[m], strokeWidth: e.type === "arrow" ? 10 : 4, opacity: e.type === "arrow" ? 35 : 80, roundness: e.roundness, locked: true,
        customData: { schema: "halo", mark: m }, ...(e.type === "arrow" ? { points: e.points, startArrowhead: null, endArrowhead: null, startBinding: null,
          endBinding: null, lastCommittedPoint: null, elbowed: false } : {}) });
    }
    out.push(e);
  }
  d.els = out;
}

// ---- show ----
function show(d, r) {
  const L = [`rev: ${r}`], lg = d.get("legend");
  if (lg) L.push(`focus: ${lg.customData.focus} | detail: ${lg.customData.detail}`);
  const label = (id) => JSON.stringify(d.textOf(id)?.text?.replace(/\n/g, " ") || "");
  const boxes = d.els.filter((e) => SHAPES.has(e.type) && !role(e) && !temp(e));
  L.push("boxes:", ...boxes.map((e) => `  ${e.id} [${e.customData?.kind || "drawn by hand"}] ${label(e.id)}`));
  const arrows = d.els.filter((e) => e.type === "arrow" && !role(e) && !temp(e));
  L.push("arrows:", ...arrows.map((e) => `  ${e.id}: ${e.startBinding?.elementId || "?"} -> ${e.endBinding?.elementId || "?"} ${label(e.id)}`));
  const notes = d.els.filter((e) => role(e) === "note");
  if (notes.length) L.push("notes:", ...notes.map((e) => `  on ${e.customData.on}: ${label(e.id)}`));
  const loose = d.els.filter((e) => e.type === "text" && !e.containerId && !role(e) && !temp(e));
  if (loose.length) L.push("loose text:", ...loose.map((e) => `  ${e.id}: ${JSON.stringify(e.text.replace(/\n/g, " "))}`));
  const h = d.els.filter(temp), n = (m) => h.filter((e) => e.customData.mark === m).length;
  if (h.length) L.push(`highlights since the last ok: ${n("new")} new, ${n("changed")} changed, ${h.filter((e) => e.customData.schema === "ghost" && !e.containerId && e.type !== "text").length} removed`);
  return L.join("\n");
}

// ---- main ----
async function main() {
  const [cmd, file, ...rest] = process.argv.slice(2);
  const flag = rest.indexOf("--rev"), want = flag >= 0 ? rest[flag + 1] : null;
  if (!["show", "apply", "ok"].includes(cmd) || !file) die("usage: schema.mjs show|apply|ok <file.excalidraw> [--rev REV]   (apply reads a JSON array of operations on stdin)");
  const exists = fs.existsSync(file), raw = exists ? fs.readFileSync(file, "utf8") : null, prev = file + ".prev";
  if (cmd === "show") { if (!exists) die(`${file} does not exist yet`); return console.log(show(new Doc(JSON.parse(raw)), rev(raw))); }
  if (exists && want !== rev(raw)) die(`${file} changed since you read it (rev ${rev(raw)}${want ? `, you passed ${want}` : ", no --rev given"}): run show again, and ask the user to save first`);
  if (!exists && cmd === "ok") die(`${file} does not exist`);
  const d = new Doc(exists ? JSON.parse(raw) : null);
  if (cmd === "ok") {
    d.els = d.els.filter((e) => !temp(e));
    fs.rmSync(prev, { force: true });
  } else {
    let ops;
    try { ops = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) { die(`stdin is not JSON: ${e.message}`); }
    if (!Array.isArray(ops)) die("stdin must be a JSON array of operations");
    const firstTime = !d.els.some((e) => SHAPES.has(e.type) && !role(e) && !temp(e));
    if (exists && !firstTime && !fs.existsSync(prev)) fs.writeFileSync(prev, raw);  // the baseline: the schema as it was at the last ok
    for (const op of ops) {
      const fn = { node: "node", edge: "edge", note: "note", style: "style", remove: "remove", legend: "legend" }[op?.op];
      if (!fn) die(`unknown operation ${JSON.stringify(op)}: use node, edge, note, style, remove, legend`);
      d[fn](op);
    }
    await place(d, firstTime);
    highlight(d, fs.existsSync(prev) ? JSON.parse(fs.readFileSync(prev, "utf8")) : null);
  }
  d.json.elements = d.els;
  const out = JSON.stringify(d.json, null, 1);
  fs.mkdirSync(path.dirname(path.resolve(file)), { recursive: true });
  fs.writeFileSync(file + ".tmp", out);
  fs.renameSync(file + ".tmp", file);  // never half a file in the editor
  console.log(show(d, rev(out)));
}

main().catch((e) => die(e.stack || String(e)));
