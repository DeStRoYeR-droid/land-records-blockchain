"use client";

/**
 * The land map: parcel boundaries from the geospatial database, drawn with Leaflet
 * over OpenStreetMap or satellite imagery.
 *
 * Leaflet touches `window`, so it is imported inside an effect (never on the
 * server). The polygons are the real boundaries recorded on the ledger; if the
 * tile servers cannot be reached (an offline demo) the parcels still draw on a
 * plain background.
 */

import "leaflet/dist/leaflet.css";

import type * as Leaflet from "leaflet";
import { useEffect, useRef, useState } from "react";

import type { GeoPolygon, ParcelFeature } from "@/lib/types";

export type BBox = [number, number, number, number];

type Props = {
  features: ParcelFeature[];
  /** Parcels drawn emphasised (the one on this page, the selected one). */
  highlight?: string[];
  /** Parcels drawn as a warning (overlaps). */
  warn?: string[];
  /** Fit the view to these parcels (default: all features). */
  fitTo?: string[] | "all" | "none";
  /** A proposed boundary to preview (the registration form). */
  proposal?: GeoPolygon | null;
  proposalTone?: "ok" | "bad";
  /** A search point and radius (metres). */
  point?: [number, number] | null;
  radiusM?: number | null;
  height?: number | string;
  label: string;
  onSelect?: (surveyNo: string) => void;
  onPick?: (lng: number, lat: number) => void;
  onMoveEnd?: (bbox: BBox) => void;
};

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function LandMap({
  features, highlight = [], warn = [], fitTo = "all", proposal, proposalTone = "ok", point, radiusM, height = 420,
  label, onSelect, onPick, onMoveEnd,
}: Props) {
  const box = useRef<HTMLDivElement>(null);
  const L = useRef<typeof Leaflet | null>(null);
  const map = useRef<Leaflet.Map | null>(null);
  const layer = useRef<Leaflet.LayerGroup | null>(null);
  const fitted = useRef<string>("");
  const handlers = useRef({ onSelect, onPick, onMoveEnd });
  handlers.current = { onSelect, onPick, onMoveEnd };
  const [ready, setReady] = useState(false);
  const [tilesFailed, setTilesFailed] = useState(false);

  // create the map once
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mod = await import("leaflet");
      if (cancelled || !box.current || map.current) return;
      const lf = (mod.default ?? mod) as typeof Leaflet;
      L.current = lf;
      const m = lf.map(box.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: false, center: [18.0, 79.53], zoom: 13 });
      const streets = lf.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      });
      const imagery = lf.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        { maxZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" },
      );
      let errors = 0;
      for (const t of [streets, imagery]) t.on("tileerror", () => { errors += 1; if (errors > 6) setTilesFailed(true); });
      streets.addTo(m);
      lf.control.layers({ Map: streets, Satellite: imagery }, undefined, { position: "topright" }).addTo(m);
      lf.control.scale({ metric: true, imperial: false }).addTo(m);
      layer.current = lf.layerGroup().addTo(m);
      m.on("click", (e: Leaflet.LeafletMouseEvent) => handlers.current.onPick?.(e.latlng.lng, e.latlng.lat));
      m.on("moveend", () => {
        const b = m.getBounds();
        handlers.current.onMoveEnd?.([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
      });
      m.on("focus", () => m.scrollWheelZoom.enable());
      m.on("blur", () => m.scrollWheelZoom.disable());
      map.current = m;
      setReady(true);
    })();
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // draw
  useEffect(() => {
    const lf = L.current, m = map.current, g = layer.current;
    if (!ready || !lf || !m || !g) return;
    g.clearLayers();
    const reg = cssVar("--reg", "#1f5f8b"), ochre = cssVar("--ochre", "#8f5b08"), red = cssVar("--red", "#b3261e");
    const ink = cssVar("--ink", "#1a2540"), field = cssVar("--field", "#2c6b4f");
    const hi = new Set(highlight), bad = new Set(warn);
    const bounds: Leaflet.LatLng[] = [];
    const fitSet = fitTo === "all" ? null : fitTo === "none" ? new Set<string>() : new Set(fitTo);
    for (const f of features) {
      const ring = f.geometry.coordinates[0].map(([x, y]) => lf.latLng(y, x));
      const pending = f.properties.status === "TRANSFER_PENDING";
      const colour = bad.has(f.id) ? red : hi.has(f.id) ? ink : pending ? ochre : reg;
      const poly = lf.polygon(ring, {
        color: colour,
        weight: hi.has(f.id) || bad.has(f.id) ? 3 : 1.5,
        fillColor: bad.has(f.id) ? red : pending ? ochre : hi.has(f.id) ? ink : reg,
        fillOpacity: hi.has(f.id) ? 0.35 : 0.18,
        dashArray: bad.has(f.id) ? "6 4" : undefined,
      });
      const p = f.properties;
      poly.bindTooltip(
        `<strong>${escapeHtml(f.id)}</strong><br>${escapeHtml(p.village)} · ${Math.round(p.areaSqm).toLocaleString("en-IN")} sq.m` +
          (p.distanceM !== undefined ? `<br>${p.distanceM.toLocaleString("en-IN")} m away` : "") +
          (p.overlapSqm !== undefined ? `<br>overlaps by ${p.overlapSqm} sq.m` : ""),
        { sticky: true },
      );
      poly.on("click", (e: Leaflet.LeafletMouseEvent) => {
        lf.DomEvent.stopPropagation(e);
        handlers.current.onSelect?.(f.id);
      });
      poly.addTo(g);
      if (!fitSet || fitSet.has(f.id)) bounds.push(...ring);
    }
    if (proposal) {
      const ring = proposal.coordinates[0].map(([x, y]) => lf.latLng(y, x));
      lf.polygon(ring, { color: proposalTone === "bad" ? red : field, weight: 3, fillOpacity: 0.25, dashArray: "8 6" })
        .bindTooltip("The proposed boundary", { sticky: true })
        .addTo(g);
      bounds.push(...ring);
    }
    if (point) {
      const c = lf.latLng(point[1], point[0]);
      lf.circleMarker(c, { radius: 6, color: ink, weight: 2, fillColor: "#fff", fillOpacity: 1 }).addTo(g);
      if (radiusM) lf.circle(c, { radius: radiusM, color: ink, weight: 1, dashArray: "4 4", fill: false }).addTo(g);
    }
    // fit once per distinct set of things to show, so panning is not undone by every redraw
    const key = JSON.stringify([fitTo === "all" ? features.map((f) => f.id).sort() : fitTo, proposal?.coordinates[0][0] ?? null]);
    if (fitTo !== "none" && bounds.length && key !== fitted.current) {
      fitted.current = key;
      m.fitBounds(lf.latLngBounds(bounds), { padding: [28, 28], maxZoom: 18 });
    }
  }, [ready, features, highlight, warn, fitTo, proposal, proposalTone, point, radiusM]);

  return (
    <figure className="land-map" style={{ margin: 0 }}>
      <div ref={box} className="land-map-canvas" style={{ height }} role="region" aria-label={label} tabIndex={0} />
      <figcaption className="land-map-legend small">
        <span><i className="swatch" data-tone="reg" aria-hidden="true" /> Registered</span>
        <span><i className="swatch" data-tone="ochre" aria-hidden="true" /> Transfer under way</span>
        {highlight.length ? <span><i className="swatch" data-tone="ink" aria-hidden="true" /> This parcel</span> : null}
        {warn.length ? <span><i className="swatch" data-tone="red" aria-hidden="true" /> Overlapping</span> : null}
        {tilesFailed ? <span className="faint">Background map unavailable offline; boundaries are still exact.</span> : null}
      </figcaption>
    </figure>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}
