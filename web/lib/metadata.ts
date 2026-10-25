import type { Metadata } from "next";

import { SITE_NAME } from "./site";

/**
 * Per-page metadata: title, description, canonical URL, Open Graph and X card.
 * Next.js merges metadata shallowly, so a page that sets openGraph replaces the
 * whole object -- this helper always sets the complete set together.
 */
export function pageMetadata({
  title,
  description,
  path,
  noindex = false,
  image,
}: {
  title: string;
  description: string;
  path: string;
  noindex?: boolean;
  /** a page-specific share image; otherwise the site's opengraph-image applies */
  image?: { url: string; alt: string };
}): Metadata {
  const images = image ? [{ url: image.url, width: 1200, height: 630, alt: image.alt }] : undefined;
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      locale: "en_IN",
      url: path,
      title,
      description,
      ...(images ? { images } : {}),
    },
    twitter: { card: "summary_large_image", title, description, ...(images ? { images } : {}) },
    ...(noindex ? { robots: { index: false, follow: false } } : {}),
  };
}

/** For the signed-in dashboards: titled, never indexed. */
export function privateMetadata(title: string, description: string): Metadata {
  return { title, description, robots: { index: false, follow: false } };
}
