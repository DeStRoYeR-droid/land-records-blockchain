/**
 * A cadastral-style village map drawn from the ledger's parcels.
 *
 * Each village becomes a region and every parcel on the ledger a field inside
 * it (a Voronoi cell, relaxed so the fields look surveyed rather than random).
 * Land that is not on the digital register yet is drawn as blank fields.
 * Everything is derived from the village names and survey numbers, so the
 * same ledger always draws the same map. It is a picture of the register,
 * not a survey -- parcel shapes are illustrative.
 */

export type Pt = [number, number];
export type Poly = Pt[];

export interface MapParcel {
  surveyNo: string;
  village: string;
  district: string;
  areaSqm: number;
  status: "ACTIVE" | "TRANSFER_PENDING";
  lastTransferAt?: string | null;
}

export interface MapCell {
  poly: Poly;
  centroid: Pt;
  area: number;
  parcel: MapParcel | null;
  label: string | null;
}

export interface MapVillage {
  name: string;
  poly: Poly;
  cells: MapCell[];
  labelAt: Pt;
}

export interface VillageMapData {
  width: number;
  height: number;
  villages: MapVillage[];
}

// ---- deterministic randomness ------------------------------------------------------

function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function rng(seed: number): () => number {
  let a = seed || 1;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---- geometry ----------------------------------------------------------------------

export function area(poly: Poly): number {
  let s = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % poly.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}

export function centroid(poly: Poly): Pt {
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % poly.length];
    const f = x1 * y2 - x2 * y1;
    a += f;
    cx += (x1 + x2) * f;
    cy += (y1 + y2) * f;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length || 1;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cy / (3 * a)];
}

/** Keep the part of `poly` where a*x + b*y <= c (Sutherland-Hodgman, one edge). */
function clip(poly: Poly, a: number, b: number, c: number): Poly {
  const out: Poly = [];
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i];
    const q = poly[(i + 1) % poly.length];
    const fp = a * p[0] + b * p[1] - c;
    const fq = a * q[0] + b * q[1] - c;
    if (fp <= 0) out.push(p);
    if ((fp < 0 && fq > 0) || (fp > 0 && fq < 0)) {
      const t = fp / (fp - fq);
      out.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]);
    }
  }
  return out;
}

/** Voronoi cells of `sites`, clipped to the convex polygon `bounds`. */
function voronoi(sites: Pt[], bounds: Poly): Poly[] {
  return sites.map((s, i) => {
    let cell = bounds;
    for (let j = 0; j < sites.length && cell.length; j++) {
      if (j === i) continue;
      const t = sites[j];
      cell = clip(cell, t[0] - s[0], t[1] - s[1], (t[0] ** 2 + t[1] ** 2 - s[0] ** 2 - s[1] ** 2) / 2);
    }
    return cell;
  });
}

/** Lloyd relaxation: move each site to its cell's centroid, a few times. */
function relax(sites: Pt[], bounds: Poly, rounds: number): { sites: Pt[]; cells: Poly[] } {
  let cells = voronoi(sites, bounds);
  for (let r = 0; r < rounds; r++) {
    sites = cells.map((c, i) => (c.length >= 3 ? centroid(c) : sites[i]));
    cells = voronoi(sites, bounds);
  }
  return { sites, cells };
}

function inside(poly: Poly, [x, y]: Pt): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

function bbox(poly: Poly) {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}

/** Shrink a convex polygon towards its centroid (a gap between villages for the road). */
function inset(poly: Poly, by: number): Poly {
  const c = centroid(poly);
  return poly.map(([x, y]) => {
    const dx = x - c[0];
    const dy = y - c[1];
    const d = Math.hypot(dx, dy) || 1;
    const k = Math.max(0, d - by) / d;
    return [c[0] + dx * k, c[1] + dy * k];
  });
}

export function shortSurvey(surveyNo: string): string {
  const parts = surveyNo.split("-");
  return parts.length > 1 ? parts[parts.length - 1] : surveyNo;
}

// ---- the map -----------------------------------------------------------------------

export function buildVillageMap(parcels: MapParcel[], width = 760, height = 520): VillageMapData {
  const margin = 14;
  const frame: Poly = [
    [margin, margin],
    [width - margin, margin],
    [width - margin, height - margin],
    [margin, height - margin],
  ];
  const byVillage = new Map<string, MapParcel[]>();
  for (const p of [...parcels].sort((a, b) => a.surveyNo.localeCompare(b.surveyNo))) {
    const key = p.village || "Unnamed village";
    if (!byVillage.has(key)) byVillage.set(key, []);
    byVillage.get(key)!.push(p);
  }
  let names = [...byVillage.keys()].sort();
  if (names.length > 7) {
    // keep the map legible: the seven busiest villages, the rest together
    names = [...names].sort((a, b) => byVillage.get(b)!.length - byVillage.get(a)!.length);
    const rest = names.slice(6).flatMap((n) => byVillage.get(n)!);
    names = names.slice(0, 6).sort();
    byVillage.set("Other villages", rest);
    names.push("Other villages");
  }
  if (!names.length) names = [""]; // an empty ledger still draws one blank village

  // village regions
  const rv = rng(hash(names.join("|")));
  const cols = Math.ceil(Math.sqrt(names.length * (width / height)));
  const rows = Math.ceil(names.length / cols);
  const vSites: Pt[] = names.map((_, i) => {
    const c = i % cols;
    const r = Math.floor(i / cols);
    return [
      margin + ((c + 0.5 + (rv() - 0.5) * 0.5) / cols) * (width - 2 * margin),
      margin + ((r + 0.5 + (rv() - 0.5) * 0.5) / rows) * (height - 2 * margin),
    ];
  });
  const { cells: vCells } = relax(vSites, frame, names.length > 1 ? 5 : 0);

  const villages: MapVillage[] = names.map((name, vi) => {
    const poly = inset(vCells[vi], 6);
    const list = byVillage.get(name) ?? [];
    const target = Math.max(list.length + 5, Math.round(10 + (area(poly) / (width * height)) * 26));
    const r = rng(hash(`village:${name}`));
    const box = bbox(poly);
    const sites: Pt[] = [];
    let guard = 0;
    while (sites.length < target && guard++ < target * 60) {
      const pt: Pt = [box.x0 + r() * (box.x1 - box.x0), box.y0 + r() * (box.y1 - box.y0)];
      if (inside(poly, pt)) sites.push(pt);
    }
    const relaxed = relax(sites, poly, 3);
    const cells: MapCell[] = relaxed.cells
      .filter((c) => c.length >= 3)
      .map((c) => ({ poly: c, centroid: centroid(c), area: area(c), parcel: null, label: null }));

    // bigger parcels get bigger fields; the blank field nearest the centre carries the village name
    const bySize = [...cells].sort((a, b) => b.area - a.area);
    const parcelsBySize = [...list].sort((a, b) => b.areaSqm - a.areaSqm || a.surveyNo.localeCompare(b.surveyNo));
    const vc = centroid(poly);
    const reserved = [...bySize].sort(
      (a, b) => Math.hypot(a.centroid[0] - vc[0], a.centroid[1] - vc[1]) - Math.hypot(b.centroid[0] - vc[0], b.centroid[1] - vc[1]),
    )[0];
    let k = 0;
    for (const cell of bySize) {
      if (cell === reserved) continue;
      const parcel = parcelsBySize[k++];
      if (!parcel) break;
      cell.parcel = parcel;
      cell.label = cell.area > 900 ? shortSurvey(parcel.surveyNo) : null;
    }
    return { name, poly, cells, labelAt: reserved ? reserved.centroid : vc };
  });

  return { width, height, villages };
}

export function pathOf(poly: Poly): string {
  return poly.length ? `M${poly.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join("L")}Z` : "";
}
