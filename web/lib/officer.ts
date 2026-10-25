import type { NavItem } from "@/components/shell/dashboard-shell";
import type { OfficerRole } from "./types";

/** What each role does at its desk, in the words shown on the overview. */
export const DESK_BLURB: Record<OfficerRole, string> = {
  registrar: "Register parcels, issue deeds and certificates into DigiLocker, and forward complete files to Revenue.",
  clerk: "Open a file from a transfer both parties have signed, and anchor the seller's documents on the ledger.",
  verifier: "Check the documents again with Revenue's own copy, and forward surveyed files to the Collectorate.",
  surveyor: "Record the field survey: the measured area and boundaries.",
  collector: "Approve or reject. Only an approval changes the owner on the ledger.",
};

export function deskNav(role: OfficerRole, queue: number): NavItem[] {
  const items: NavItem[] = [
    { href: "/officials", label: "Your queue", exact: true, count: queue },
    { href: "/officials/applications", label: "All applications" },
    { href: "/officials/parcels", label: "Parcels" },
    { href: "/officials/transfers", label: "Transfer requests" },
  ];
  if (role === "registrar") items.push({ href: "/officials/registry", label: "Register & issue", group: "Registrar" });
  items.push(
    { href: "/officials/ledger", label: "Ledger", group: "The network" },
    { href: "/officials/network", label: "Nodes" },
    { href: "/agent", label: "AI validator", group: "Validator" },
  );
  return items;
}
