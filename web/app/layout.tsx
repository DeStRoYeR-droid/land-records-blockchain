import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { preload } from "react-dom";

import { StampInkFilter } from "@/components/ui/stamp";
import { SessionProvider } from "@/lib/session";
import { SITE_DESCRIPTION, SITE_NAME, SITE_TAGLINE, SITE_URL } from "@/lib/site";

import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: `${SITE_NAME}: ${SITE_TAGLINE}`, template: `%s | ${SITE_NAME}` },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  authors: [{ name: "LandChain project team" }],
  keywords: ["land registry", "blockchain", "Hyperledger Fabric", "Telangana", "agentic AI", "OpenPGP", "DigiLocker"],
  category: "government",
  formatDetection: { telephone: false, address: false, email: false },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    locale: "en_IN",
    title: `${SITE_NAME}: ${SITE_TAGLINE}`,
    description: SITE_DESCRIPTION,
  },
  twitter: { card: "summary_large_image", title: `${SITE_NAME}: ${SITE_TAGLINE}`, description: SITE_DESCRIPTION },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f5f4" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1522" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  preload("/fonts/atkinson-next-latin.woff2", { as: "font", type: "font/woff2", crossOrigin: "" });
  preload("/fonts/atkinson-mono-latin.woff2", { as: "font", type: "font/woff2", crossOrigin: "" });
  return (
    <html lang="en-IN">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <StampInkFilter />
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
