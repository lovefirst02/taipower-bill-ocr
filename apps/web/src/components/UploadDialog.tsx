import { useRef, useState } from "react";
import type { DragEvent } from "react";
import type { Site } from "../types";

export type UploadTarget = number | "green" | null;

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp";
const size = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

interface Props {
  sites: Site[];
  /** Files dropped on the sidebar before the dialog opened. */
  initialFiles: File[];
  initialTarget: UploadTarget;
  onCancel: () => void;
  onUpload: (files: File[], target: UploadTarget) => void;
}

/** Choose what the files are (and their site), gather them, then send them all at once. */
export function UploadDialog({ sites, initialFiles, initialTarget, onCancel, onUpload }: Props) {
  const [files, setFiles] = useState<File[]>(initialFiles);
  const [target, setTarget] = useState<UploadTarget>(initialTarget);
  // The site chosen in the list, kept when switching to another kind and back.
  const [siteId, setSiteId] = useState<number | null>(typeof initialTarget === "number" ? initialTarget : sites[0]?.id ?? null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const add = (more: File[]) =>
    setFiles((current) => [...current, ...more.filter((file) => !current.some((other) => other.name === file.name && other.size === file.size))]);
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    add(Array.from(event.dataTransfer.files));
  };
  const kind = target === "green" ? "green" : typeof target === "number" ? "site" : "bill";
  // A site may have been removed since it was last chosen.
  const chosenSite = sites.some((site) => site.id === siteId) ? siteId : sites[0]?.id ?? null;
  const choose = (next: "bill" | "site" | "green") => setTarget(next === "green" ? "green" : next === "site" ? chosenSite : null);
  const choices: { value: "bill" | "site" | "green"; title: string; hint: string }[] = [
    { value: "bill", title: "台電帳單", hint: "Each bill finds its site by its 電號 (Settings → Sites)." },
    ...(sites.length ? [{ value: "site" as const, title: "指定 Site", hint: "Every page goes to the site chosen below, whatever its 電號 — e.g. a landlord's allocation." }] : []),
    { value: "green", title: "綠電單據", hint: "Taipower's transfer fee lists, retailers' statements and purchase notices — they cover several sites; never read as Taipower bills." },
  ];

  return (
    <div className="modal" onClick={onCancel}>
      <div className="modal-body upload-dialog" onClick={(event) => event.stopPropagation()}>
        <h2>Upload files</h2>
        <h4>1. What are these files?</h4>
        <div className="upload-choices" role="radiogroup">
          {choices.map((choice) => (
            <label key={choice.value} className={`upload-choice${kind === choice.value ? " chosen" : ""}`}>
              <input type="radio" name="upload-target" checked={kind === choice.value} onChange={() => choose(choice.value)} />
              <span>
                <strong>{choice.title}</strong>
                <small>{choice.hint}</small>
                {choice.value === "site" && (
                  <select value={chosenSite ?? ""} onClick={(event) => { event.stopPropagation(); if (kind !== "site") choose("site"); }}
                    onChange={(event) => { setSiteId(Number(event.target.value)); setTarget(Number(event.target.value)); }}
                    onFocus={() => choose("site")}>
                    {sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
                  </select>
                )}
              </span>
            </label>
          ))}
        </div>

        <h4>2. Files</h4>
        <div
          className={`dropzone${over ? " over" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && input.current?.click()}
          onDragOver={(event) => { event.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={drop}
        >
          <strong>Drop files here</strong>
          <span>or click to choose · PDF, PNG, JPG · many at once</span>
          <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(event) => {
            add(Array.from(event.target.files ?? []));
            event.target.value = "";
          }} />
        </div>
        {files.length > 0 && (
          <ul className="upload-files">
            {files.map((file, index) => (
              <li key={`${file.name}-${index}`}>
                <span className="name">{file.name}</span>
                <span className="meta">{size(file.size)}</span>
                <button type="button" className="icon" aria-label={`Remove ${file.name}`} onClick={() => setFiles((current) => current.filter((_, at) => at !== index))}>✕</button>
              </li>
            ))}
          </ul>
        )}

        <div className="modal-footer">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="button" className="primary" disabled={!files.length} onClick={() => onUpload(files, target)}>
            Upload {files.length || ""} file{files.length === 1 ? "" : "s"}
          </button>
        </div>
      </div>
    </div>
  );
}
