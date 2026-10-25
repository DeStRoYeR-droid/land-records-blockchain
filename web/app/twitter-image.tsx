import { SHARE_SIZE, shareImage } from "@/lib/share-image";

export const alt = "LandChain: a land register kept by Registration, Revenue and the Collectorate on one ledger";
export const size = SHARE_SIZE;
export const contentType = "image/png";

export default async function TwitterImage() {
  return shareImage({
    title: "Land changes hands only when every office signs.",
    subtitle: "Hanamkonda district land register, with an AI validator's signed check on every sale.",
  });
}
