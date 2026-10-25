"use client";

import { useId, useState, type DragEvent } from "react";

/** A file chooser that also accepts a dropped file. The file is read here, in the browser; nothing is uploaded. */
export function FilePick({
  label,
  hint,
  accept,
  onFile,
  file,
}: {
  label: string;
  hint?: string;
  accept?: string;
  onFile: (f: File | null) => void;
  file: File | null;
}) {
  const id = useId();
  const [over, setOver] = useState(false);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  };
  return (
    <div
      className="filepick"
      data-over={over || undefined}
      data-has={file ? true : undefined}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <label htmlFor={id} className="filepick-label">
        {label}
      </label>
      {hint ? (
        <p className="hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
      <input
        id={id}
        type="file"
        accept={accept}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      {file ? (
        <p className="filepick-chosen">
          <span className="mono">{file.name}</span> <span className="faint">({file.size.toLocaleString("en-IN")} bytes)</span>
        </p>
      ) : (
        <p className="faint small">or drop the file here</p>
      )}
    </div>
  );
}
