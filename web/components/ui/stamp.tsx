/**
 * The rubber stamp: used only where something final happened -- the
 * Collector's decision, or a signature that checked out. Its uneven ink comes
 * from the SVG filter below, included once in the root layout.
 */

import type { ReactNode } from "react";

export function Stamp({ tone, children, sub }: { tone?: "ok" | "bad" | "warn"; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stamp" data-tone={tone}>
      <span>
        {children}
        {sub ? <small>{sub}</small> : null}
      </span>
    </div>
  );
}

export function StampInkFilter() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
      <filter id="stamp-ink">
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise" />
        <feColorMatrix in="noise" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -1.6 1.25" result="speckle" />
        <feComposite in="SourceGraphic" in2="speckle" operator="in" />
      </filter>
    </svg>
  );
}
