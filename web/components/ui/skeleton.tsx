/** Loading placeholders shaped like what is coming, so the page does not jump. */

export function SkeletonLines({ lines = 3, widths }: { lines?: number; widths?: string[] }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton skeleton-line" style={{ width: widths?.[i] ?? `${92 - ((i * 17) % 35)}%` }} />
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="panel panel-flush" aria-hidden="true">
      <div style={{ padding: "var(--s-4) var(--s-5)" }}>
        <span className="skeleton skeleton-title" />
        {Array.from({ length: rows }, (_, r) => (
          <div key={r} style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: "var(--s-4)", padding: "0.4rem 0" }}>
            {Array.from({ length: cols }, (_, c) => (
              <span key={c} className="skeleton skeleton-line" style={{ width: `${60 + ((r * 7 + c * 13) % 40)}%` }} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function SkeletonPanel({ height = "8rem" }: { height?: string }) {
  return (
    <div className="panel" aria-hidden="true">
      <span className="skeleton skeleton-title" />
      <span className="skeleton skeleton-block" style={{ height }} />
    </div>
  );
}

/** Announced once to screen readers; the skeletons themselves are hidden from them. */
export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <p className="visually-hidden" role="status">
      {label}…
    </p>
  );
}
