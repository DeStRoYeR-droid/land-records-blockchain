"use client";

/** The generic application error state (for errors outside a section's own boundary). */

import { ErrorPanel } from "@/components/site/error-panel";

export default function AppError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main" className="site-main">
      <ErrorPanel {...props} />
    </main>
  );
}
