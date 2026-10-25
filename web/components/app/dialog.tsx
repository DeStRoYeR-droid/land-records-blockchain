"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * A modal built on the native <dialog>: focus is trapped, Escape closes it,
 * and the page behind is inert -- all supplied by the browser.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className="dialog"
      data-wide={wide || undefined}
      aria-labelledby="dialog-title"
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      {open ? (
        <div className="dialog-body">
          <header className="panel-head">
            <h2 id="dialog-title">{title}</h2>
            <button type="button" className="btn btn-small btn-quiet" onClick={onClose}>
              Close
            </button>
          </header>
          {children}
        </div>
      ) : null}
    </dialog>
  );
}
