"use client";

/** Errors on the public pages: shown inside the site header and footer. */

import { ErrorPanel } from "@/components/site/error-panel";

export default function PublicError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorPanel {...props} />;
}
