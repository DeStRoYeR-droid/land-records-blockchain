import { Loading, SkeletonLines } from "@/components/ui/skeleton";

/** The first paint while a page's data is on its way. */
export default function RootLoading() {
  return (
    <div className="boot">
      <Loading label="Loading LandChain" />
      <div className="page" aria-hidden="true" style={{ paddingBlock: "var(--s-6)" }}>
        <div className="hero" style={{ paddingTop: 0 }}>
          <div>
            <span className="skeleton" style={{ height: "3.2rem", width: "90%", marginBottom: "var(--s-3)" }} />
            <span className="skeleton" style={{ height: "3.2rem", width: "70%", marginBottom: "var(--s-5)" }} />
            <SkeletonLines lines={3} />
          </div>
          <span className="skeleton" style={{ aspectRatio: "760 / 520", borderRadius: "var(--r-l)" }} />
        </div>
      </div>
    </div>
  );
}
