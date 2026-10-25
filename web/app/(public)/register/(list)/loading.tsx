import { Loading, SkeletonTable } from "@/components/ui/skeleton";

export default function LoadingRegister() {
  return (
    <div className="page">
      <Loading label="Reading the register" />
      <div className="page-head" aria-hidden="true">
        <span className="skeleton skeleton-title" style={{ width: "30%" }} />
        <span className="skeleton skeleton-line" style={{ width: "60%" }} />
      </div>
      <span className="skeleton" style={{ height: "2.75rem", marginBottom: "var(--s-5)" }} aria-hidden="true" />
      <SkeletonTable rows={8} cols={6} />
    </div>
  );
}
