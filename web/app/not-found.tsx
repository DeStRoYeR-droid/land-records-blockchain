import Link from "next/link";

import { SiteFooter } from "@/components/site/site-footer";
import { SiteHeader } from "@/components/site/site-header";

export const metadata = {
  title: "Page not found",
  description: "There is no page at this address on LandChain.",
  robots: { index: false, follow: true },
};

/** The custom 404: a field with no survey number. */
export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="site-main">
        <div className="page not-found">
          <svg className="not-found-art" viewBox="0 0 240 170" aria-hidden="true">
            <path d="M18 52 128 14l92 66-46 78-140-18z" className="nf-field" />
            <path d="M128 14 112 140" className="nf-split" />
            {[
              [18, 52],
              [128, 14],
              [220, 80],
              [174, 158],
              [34, 140],
            ].map(([x, y]) => (
              <circle key={`${x}-${y}`} cx={x} cy={y} r="6" className="nf-stone" />
            ))}
            <text x="120" y="95" textAnchor="middle" className="nf-label">
              Sy. No. ?
            </text>
          </svg>
          <div className="stack">
            <p className="faint">Error 404</p>
            <h1>No page at this address</h1>
            <p className="lede">
              The link may be mistyped, or the page may have moved. If you were looking for a parcel, its survey number
              may not be on the register.
            </p>
            <div className="cluster">
              <Link className="btn btn-primary" href="/">
                Go to the home page
              </Link>
              <Link className="btn" href="/register">
                Search the land register
              </Link>
              <Link className="btn btn-quiet" href="/sign-in">
                Sign in
              </Link>
            </div>
          </div>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
