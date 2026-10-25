/** Line icons drawn for LandChain (24px grid, 1.75 stroke, currentColor). */

import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { title?: string };

function Svg({ title, children, ...rest }: P) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      focusable="false"
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

export const IconCheck = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m8 12.5 2.6 2.6L16.2 9.5" />
  </Svg>
);
export const IconWarn = (p: P) => (
  <Svg {...p}>
    <path d="M12 3.5 21 19.5H3z" />
    <path d="M12 9.5v4.5M12 17h.01" />
  </Svg>
);
export const IconFail = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m9 9 6 6M15 9l-6 6" />
  </Svg>
);
export const IconSkip = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" strokeDasharray="3 3" />
    <path d="M8.5 12h7" />
  </Svg>
);
export const IconInfo = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 8h.01" />
  </Svg>
);
export const IconRunning = (p: P) => (
  <Svg {...p}>
    <path d="M12 3a9 9 0 1 1-9 9" />
  </Svg>
);
export const IconCopy = (p: P) => (
  <Svg {...p}>
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" />
  </Svg>
);
export const IconDone = (p: P) => (
  <Svg {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
);
export const IconKey = (p: P) => (
  <Svg {...p}>
    <circle cx="8" cy="15" r="4" />
    <path d="m11 12 8.5-8.5M16 7l2.5 2.5M14 9l1.8 1.8" />
  </Svg>
);
export const IconDownload = (p: P) => (
  <Svg {...p}>
    <path d="M12 4v11M7 10.5l5 5 5-5M5 20h14" />
  </Svg>
);
export const IconImageOff = (p: P) => (
  <Svg {...p}>
    <path d="M4 5h16v14H4z" />
    <path d="m4 16 5-5 4 4 2-2 5 5" />
    <path d="M3 3l18 18" />
  </Svg>
);
export const IconSearch = (p: P) => (
  <Svg {...p}>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="m20 20-4.8-4.8" />
  </Svg>
);
export const IconMenu = (p: P) => (
  <Svg {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Svg>
);
export const IconTool = (p: P) => (
  <Svg {...p}>
    <path d="M4 7h11M4 12h7M4 17h9" />
    <circle cx="18" cy="12" r="3" />
  </Svg>
);
export const IconBook = (p: P) => (
  <Svg {...p}>
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z" />
  </Svg>
);
export const IconPen = (p: P) => (
  <Svg {...p}>
    <path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19z" />
    <path d="M14.5 6.5l3 3" />
  </Svg>
);
export const IconSeal = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="10" r="6" />
    <path d="m9 15.5-1.5 5L12 18l4.5 2.5-1.5-5" />
    <path d="m9.5 10 1.8 1.8 3.2-3.3" />
  </Svg>
);
export const IconAnchor = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="5.5" r="2" />
    <path d="M12 7.5V20M7 11h10M4.5 14a7.5 7.5 0 0 0 15 0" />
  </Svg>
);
export const IconChat = (p: P) => (
  <Svg {...p}>
    <path d="M4 5h16v11H9l-5 4z" />
  </Svg>
);
export const IconDecision = (p: P) => (
  <Svg {...p}>
    <path d="M12 3v4M6 9h12l-1.5 11h-9z" />
    <path d="M9 13h6" />
  </Svg>
);

/** A survey field: an irregular quadrilateral with boundary stones at the corners. */
export const IconParcel = (p: P) => (
  <Svg {...p}>
    <path d="M4 8.5 13 4l7 6.5-3.5 9.5L5.5 18z" />
    <path d="M13 4l-2 15" strokeDasharray="2.5 2.5" />
  </Svg>
);
export const IconOffice = (p: P) => (
  <Svg {...p}>
    <path d="M3 20h18M5 20V10M19 20V10M9 20v-6h6v6M2.5 10 12 4l9.5 6" />
  </Svg>
);
export const IconPerson = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="8" r="3.5" />
    <path d="M5 20a7 7 0 0 1 14 0" />
  </Svg>
);
export const IconLens = (p: P) => (
  <Svg {...p}>
    <path d="M4 6h8M4 10h6M4 14h5" />
    <circle cx="15.5" cy="13.5" r="4" />
    <path d="m18.5 16.5 2.5 2.5" />
  </Svg>
);

/** state -> mark used in check lists */
export function Mark({ state, className = "mark" }: { state: string; className?: string }) {
  const props = { className, "data-state": state } as P;
  switch (state) {
    case "ok":
    case "pass":
      return <IconCheck {...props} />;
    case "warn":
      return <IconWarn {...props} />;
    case "fail":
    case "error":
      return <IconFail {...props} />;
    case "running":
      return <IconRunning {...props} style={{ animation: "spin 0.9s linear infinite" }} />;
    case "info":
      return <IconInfo {...props} />;
    default:
      return <IconSkip {...props} />;
  }
}
