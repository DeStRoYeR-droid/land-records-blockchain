"use client";

/**
 * An image with a loading placeholder and a broken-image fallback.
 *
 * Used for the parcel sketches, which the gateway draws on request -- so they
 * can be slow (placeholder) or missing when the network is down (fallback).
 */

import { useEffect, useRef, useState } from "react";

import { IconImageOff } from "./icons";

type State = "loading" | "loaded" | "failed";

export function SmartImage({
  src,
  alt,
  width,
  height,
  fallbackText = "Image unavailable",
  className = "",
}: {
  src: string;
  alt: string;
  width: number;
  height: number;
  fallbackText?: string;
  className?: string;
}) {
  const [state, setState] = useState<State>("loading");
  const ref = useRef<HTMLImageElement>(null);

  useEffect(() => {
    setState("loading");
    // an image that finished before hydration fires no load event: read its state directly
    const img = ref.current;
    if (img?.complete) setState(img.naturalWidth > 0 ? "loaded" : "failed");
  }, [src]);

  return (
    <div className={`smart-image ${className}`} data-state={state} style={{ aspectRatio: `${width} / ${height}` }}>
      {state === "loading" ? <span className="skeleton" aria-hidden="true" /> : null}
      {state === "failed" ? (
        <div className="smart-image-fallback" role="img" aria-label={`${alt} (not available)`}>
          <IconImageOff />
          <span>{fallbackText}</span>
        </div>
      ) : null}
      {/* a plain <img>: the sketch is drawn on demand by the gateway, not a static asset next/image could optimise */}
      <img
        ref={ref}
        src={src}
        alt={state === "failed" ? "" : alt}
        width={width}
        height={height}
        loading="lazy"
        decoding="async"
        onLoad={() => setState("loaded")}
        onError={() => setState("failed")}
        hidden={state === "failed"}
      />
    </div>
  );
}
