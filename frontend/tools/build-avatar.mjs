// Builds a compact PBR avatar GLB per theme from the source OBJ + 4K texture maps.
//   node tools/build-avatar.mjs            -> public/models/<theme>/avatar.raw.glb
// then `npm run build:avatar` runs gltf-transform optimize (meshopt + webp) on it.
import fs from 'node:fs';
import path from 'node:path';
import sharp from 'sharp';
import { Document, NodeIO } from '@gltf-transform/core';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const THEMES = {
  // symKeep: mirror the head from its +z half (both ears end up tall and equal; the source's are uneven).
  dark: { obj: 'assets/dark/model.obj', tex: 'assets/dark', prefix: 'futuristic-robot-head-3d-model-oorbmzoygnj_Material_01_', symKeep: 1 },
  light: { obj: 'assets/light/model.obj', tex: 'assets/light', prefix: 'futuristic-robot-head-3d-model-aoomgkoejcj_Material_01_', symKeep: 1 },
};
const BASE_SIZE = 2048;
const ORM_SIZE = 1024;

// The source OBJ is a showcase scene with TWO copies of the robot side by side (split along x).
// We keep the +x copy: the one the visor overlay and 3/4 facing angle were tuned for.
function keepRightCopy(v, faces) {
  const cx = faces.map((f) => f.reduce((s, c) => s + v[parseInt(c.split('/')[0], 10) - 1][0], 0) / f.length);
  const sorted = [...cx].sort((a, b) => a - b);
  let gap = 0, split = 0;
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] - sorted[i - 1] > gap) { gap = sorted[i] - sorted[i - 1]; split = (sorted[i] + sorted[i - 1]) / 2; }
  }
  return faces.filter((_, i) => cx[i] > split);
}

function parseObj(text) {
  const v = [], vt = [], vn = [];
  const pos = [], uv = [], nrm = [], idx = [];
  const cache = new Map();
  const faces = [];
  for (const line of text.split('\n')) {
    const p = line.trim().split(/\s+/);
    if (p[0] === 'v') v.push([+p[1], +p[2], +p[3]]);
    else if (p[0] === 'vt') vt.push([+p[1], +p[2]]);
    else if (p[0] === 'vn') vn.push([+p[1], +p[2], +p[3]]);
    else if (p[0] === 'f') faces.push(p.slice(1));
  }
  for (const face of keepRightCopy(v, faces)) {
    const corners = face.map((c) => {
      let i = cache.get(c);
      if (i === undefined) {
        const [a, b, n] = c.split('/').map((s) => (s ? parseInt(s, 10) - 1 : -1));
        i = pos.length / 3;
        pos.push(...v[a]);
        const t = b >= 0 ? vt[b] : [0, 0];
        uv.push(t[0], 1 - t[1]); // OBJ origin bottom-left, glTF top-left
        nrm.push(...(n >= 0 ? vn[n] : [0, 1, 0]));
        cache.set(c, i);
      }
      return i;
    });
    for (let k = 1; k < corners.length - 1; k++) idx.push(corners[0], corners[k], corners[k + 1]);
  }
  return { pos: new Float32Array(pos), uv: new Float32Array(uv), nrm: new Float32Array(nrm), idx: new Uint32Array(idx) };
}

async function readMap(dir, prefix, name, size) {
  return sharp(path.join(ROOT, dir, `${prefix}${name}.png`)).resize(size, size);
}

/**
 * Make the head mirror-symmetric (the source model's ears/antennae are uneven). The head (above HEAD_MIN_Y) is
 * cut exactly along its centre plane z = c - triangles crossing the plane are clipped, not dropped - the kept
 * half is copied mirrored onto the other side, so both halves meet on identical seam vertices (no crack).
 * keep: +1 keeps the z > c half, -1 the z < c half. The body is left untouched.
 */
const HEAD_MIN_Y = -0.155;

function symmetrizeHead(mesh, keep) {
  const P = mesh.pos, N = mesh.nrm, T = mesh.uv, I = mesh.idx;
  // Centre plane: middle of the helmet shell below the ears.
  let zmin = Infinity, zmax = -Infinity;
  for (let v = 0; v < P.length / 3; v++) {
    const y = P[v * 3 + 1];
    if (y > -0.1 && y < 0.25) { zmin = Math.min(zmin, P[v * 3 + 2]); zmax = Math.max(zmax, P[v * 3 + 2]); }
  }
  const c = (zmin + zmax) / 2;
  const pos = [], nrm = [], uv = [], idx = [];
  const vert = (v) => ({ p: [P[v * 3], P[v * 3 + 1], P[v * 3 + 2]], n: [N[v * 3], N[v * 3 + 1], N[v * 3 + 2]], t: [T[v * 2], T[v * 2 + 1]] });
  const lerp = (a, b, s) => ({
    p: a.p.map((x, k) => x + (b.p[k] - x) * s), n: a.n.map((x, k) => x + (b.n[k] - x) * s), t: a.t.map((x, k) => x + (b.t[k] - x) * s),
  });
  const emit = (poly) => {
    const base = pos.length / 3;
    for (const q of poly) { pos.push(...q.p); nrm.push(...q.n); uv.push(...q.t); }
    for (let k = 1; k < poly.length - 1; k++) idx.push(base, base + k, base + k + 1);
  };
  const mirror = (poly) => poly.map((q) => ({ p: [q.p[0], q.p[1], 2 * c - q.p[2]], n: [q.n[0], q.n[1], -q.n[2]], t: q.t })).reverse();
  for (let f = 0; f < I.length; f += 3) {
    const tri = [vert(I[f]), vert(I[f + 1]), vert(I[f + 2])];
    const isHead = (tri[0].p[1] + tri[1].p[1] + tri[2].p[1]) / 3 > HEAD_MIN_Y;
    if (!isHead) { emit(tri); continue; }
    // Sutherland-Hodgman clip against the kept half-space  keep * (z - c) >= 0.
    const side = (q) => keep * (q.p[2] - c);
    const out = [];
    for (let k = 0; k < 3; k++) {
      const a = tri[k], b = tri[(k + 1) % 3];
      const sa = side(a), sb = side(b);
      if (sa >= 0) out.push(a);
      if ((sa >= 0) !== (sb >= 0)) {
        const q = lerp(a, b, sa / (sa - sb));
        q.p[2] = c;   // exactly on the plane, so both halves share the seam
        out.push(q);
      }
    }
    if (out.length >= 3) { emit(out); emit(mirror(out)); }
  }
  return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), uv: new Float32Array(uv), idx: new Uint32Array(idx), plane: c };
}

async function buildTheme(theme, cfg) {
  let mesh = parseObj(fs.readFileSync(path.join(ROOT, cfg.obj), 'utf8'));
  const keep = Number(process.env[`SYM_${theme.toUpperCase()}`] ?? cfg.symKeep ?? 0);
  if (keep) {
    mesh = symmetrizeHead(mesh, keep);
    console.log(`${theme}: head mirrored (kept ${keep > 0 ? '+z' : '-z'} half, plane z=${mesh.plane.toFixed(3)})`);
  }
  const doc = new Document();
  const buffer = doc.createBuffer();

  const base = await (await readMap(cfg.tex, cfg.prefix, 'BaseColor', BASE_SIZE)).removeAlpha().png().toBuffer();
  const normal = await (await readMap(cfg.tex, cfg.prefix, 'Normal', BASE_SIZE)).removeAlpha().png().toBuffer();
  // glTF packs occlusion/roughness/metallic into R/G/B of one texture.
  const rough = await (await readMap(cfg.tex, cfg.prefix, 'Roughness', ORM_SIZE)).greyscale().raw().toBuffer();
  const metal = await (await readMap(cfg.tex, cfg.prefix, 'Metallic', ORM_SIZE)).greyscale().raw().toBuffer();
  const orm = Buffer.alloc(ORM_SIZE * ORM_SIZE * 3);
  for (let i = 0; i < ORM_SIZE * ORM_SIZE; i++) {
    orm[i * 3] = 255;
    orm[i * 3 + 1] = rough[i];
    orm[i * 3 + 2] = metal[i];
  }
  const ormPng = await sharp(orm, { raw: { width: ORM_SIZE, height: ORM_SIZE, channels: 3 } }).png().toBuffer();

  const tex = (name, img) => doc.createTexture(name).setImage(img).setMimeType('image/png');
  const material = doc.createMaterial(`robot_${theme}`)
    .setBaseColorTexture(tex('base', base))
    .setNormalTexture(tex('normal', normal))
    .setMetallicRoughnessTexture(tex('orm', ormPng))
    .setMetallicFactor(1)
    .setRoughnessFactor(1);

  const acc = (arr, type) => doc.createAccessor().setArray(arr).setType(type).setBuffer(buffer);
  const prim = doc.createPrimitive()
    .setAttribute('POSITION', acc(mesh.pos, 'VEC3'))
    .setAttribute('NORMAL', acc(mesh.nrm, 'VEC3'))
    .setAttribute('TEXCOORD_0', acc(mesh.uv, 'VEC2'))
    .setIndices(acc(mesh.idx, 'SCALAR'))
    .setMaterial(material);
  const node = doc.createNode('Robot').setMesh(doc.createMesh('Robot').addPrimitive(prim));
  doc.createScene('Scene').addChild(node);

  const out = path.join(ROOT, 'frontend/public/models', theme, 'avatar.raw.glb');
  await new NodeIO().write(out, doc);
  console.log(`${theme}: ${mesh.idx.length / 3} tris, ${mesh.pos.length / 3} verts -> ${out}`);
}

for (const [theme, cfg] of Object.entries(THEMES)) await buildTheme(theme, cfg);
