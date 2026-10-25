import type { ReactNode } from "react";

import { privateMetadata } from "@/lib/metadata";

import { Desk } from "./desk";

export const metadata = privateMetadata("Officers' desk", "Department officers' desk for LandChain land-transfer files.");

export default function OfficialsLayout({ children }: { children: ReactNode }) {
  return <Desk>{children}</Desk>;
}
