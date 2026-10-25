"use client";

/**
 * Last-resort error page, used when the root layout itself fails. It replaces
 * the whole document, so it carries its own <html>, <body> and minimal styles.
 */

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en-IN">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#f3f5f4",
          color: "#1a2540",
          fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
          padding: "1rem",
        }}
      >
        <main style={{ maxWidth: 560, borderLeft: "4px solid #b3261e", background: "#fff", padding: "1.5rem 1.75rem", borderRadius: 10 }}>
          <h1 style={{ margin: "0 0 .5rem", fontSize: "1.6rem" }}>LandChain could not load</h1>
          <p style={{ margin: "0 0 1rem", lineHeight: 1.55 }}>
            The application failed before it could draw this page. Reload to try again; if it keeps happening, the web
            app&rsquo;s log (logs/web.log) has the details.
          </p>
          {error.digest ? (
            <p style={{ fontSize: ".85rem", color: "#48526a" }}>
              Reference <code>{error.digest}</code>
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => reset()}
            style={{ font: "inherit", fontWeight: 650, padding: ".55rem 1rem", borderRadius: 6, border: "1px solid #1a2540", background: "#1a2540", color: "#f3f5f4", cursor: "pointer" }}
          >
            Reload
          </button>
        </main>
      </body>
    </html>
  );
}
