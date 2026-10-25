import Link from "next/link";

/**
 * The mark: one survey field whose four boundary stones are the three
 * departments (blue Registration, green Revenue, violet Collectorate) and the
 * citizen (ink). A parcel changes hands only when all four agree.
 */
export function Mark({ size = 30 }: { size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false">
      <path d="M5.5 12 18.5 5.5l8 11L12.5 26.5z" fill="none" stroke="var(--ink)" strokeWidth="2.4" strokeLinejoin="round" />
      <circle cx="5.5" cy="12" r="3.1" fill="var(--reg)" stroke="var(--sheet)" strokeWidth="1.3" />
      <circle cx="18.5" cy="5.5" r="3.1" fill="var(--rev)" stroke="var(--sheet)" strokeWidth="1.3" />
      <circle cx="26.5" cy="16.5" r="3.1" fill="var(--col)" stroke="var(--sheet)" strokeWidth="1.3" />
      <circle cx="12.5" cy="26.5" r="3.1" fill="var(--ink)" stroke="var(--sheet)" strokeWidth="1.3" />
    </svg>
  );
}

export function Wordmark({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="wordmark" aria-label="LandChain, home">
      <Mark />
      <span className="wordmark-name">LandChain</span>
    </Link>
  );
}
