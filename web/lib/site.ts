/** Site-wide constants used by metadata, the manifest and the share images. */

export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000").replace(/\/$/, "");
export const SITE_NAME = "LandChain";
export const SITE_TAGLINE = "Hanamkonda district land register";
export const SITE_DESCRIPTION =
  "A land register kept jointly by Registration, Revenue and the Collectorate on a permissioned blockchain. " +
  "Every sale carries both owners' signatures and an AI validator's signed check before the Collector decides.";

/** Colours the manifest and share images use (the CSS tokens in app/styles/tokens.css match). */
export const COLORS = {
  paper: "#F3F5F4",
  sheet: "#FFFFFF",
  ink: "#1A2540",
  ink2: "#48526A",
  rule: "#D5DCDF",
  field: "#2C6B4F",
  fieldWash: "#E3F0E9",
  ochre: "#9A620A",
  ochreWash: "#FBF0DA",
  violet: "#5B3FA0",
  reg: "#1F5F8B",
};
