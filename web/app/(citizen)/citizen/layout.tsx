import type { ReactNode } from "react";

import { privateMetadata } from "@/lib/metadata";

import { Portal } from "./portal";

export const metadata = privateMetadata("Your land", "Your parcels, documents and transfers on LandChain.");

export default function CitizenLayout({ children }: { children: ReactNode }) {
  return <Portal>{children}</Portal>;
}
