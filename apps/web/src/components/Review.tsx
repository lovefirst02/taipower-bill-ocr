import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { isOnPage, newHighlight, newLabel } from "../marks";
import type { Column, Item, Mark, MarkColors, Row } from "../types";
import { FieldForm } from "./FieldForm";
import { MarksPanel } from "./MarksPanel";
import { PageViewer } from "./PageViewer";

interface Props {
  itemId: number;
  user: string;
  columns: Column[];
  onChanged: () => void;
  onNext: () => void;
  /** Messages that must outlive this view, e.g. after moving to the next bill. */
  onNotify: (text: string) => void;
  /** Current default mark colours (they can change on the settings page). */
  colors?: MarkColors | null;
}

const number = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** Same formula as the server: total − basic − energy − power factor − prepaid. */
function withOtherAdjustment(row: Row): Row {
  const total = number(row["應繳總金額"]);
  const basic = number(row["基本電費"]);
  const energy = number(row["流動電費"]);
  const other =
    total === null || basic === null || energy === null
      ? null
      : Math.round((total - basic - energy - (number(row["功率因數調整費"]) ?? 0) - (number(row["預繳費用"]) ?? 0)) * 100) / 100;
  return { ...row, 其他調整費用: other };
}

export function Review({ itemId, user, columns, onChanged, onNext, onNotify, colors: defaultColors }: Props) {
  const [item, setItem] = useState<Item | null>(null);
  const [row, setRow] = useState<Row>({});
  const [marks, setMarks] = useState<Mark[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "warn" | "error"; text: string } | null>(null);
  const [preview, setPreview] = useState<number | null>(null);
  const [pageIndex, setPageIndex] = useState(0);

  const load = useCallback((loaded: Item) => {
    setItem(loaded);
    setRow(loaded.row);
    setMarks(loaded.marks);
    setDirty(false);
  }, []);

  useEffect(() => {
    setItem(null);
    setPageIndex(0);
    setSelectedId(null);
    setFocused(null);
    setMessage(null);
    api.item(itemId).then(load).catch((error: Error) => setMessage({ kind: "error", text: error.message }));
  }, [itemId, load]);

  const editable = item?.status === "draft";

  const run = async (action: () => Promise<Item>, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await action();
      load(result);
      const locked = result.locked_workbooks ?? [];
      setMessage(
        locked.length
          ? { kind: "warn", text: `${done}, but ${locked.join(" and ")} is open in Excel — it is updated automatically once the file is closed.` }
          : { kind: "ok", text: done },
      );
      onChanged();
      return result;
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
      return null;
    } finally {
      setBusy(false);
    }
  };

  const save = () => run(() => api.save(itemId, user, row, marks), "Draft saved");
  const submit = async () => {
    const result = await run(() => api.submit(itemId, user, row, marks), "Submitted — saved to the Excel files with its evidence PDF");
    if (result && !(result.locked_workbooks ?? []).length) {
      onNotify(`${item?.filename ?? "Bill"} submitted — saved to the Excel files with its evidence PDF.`);
      onNext();
    }
  };
  const reopen = () =>
    run(
      () => api.reopen(itemId, user),
      "Reopened for editing — removed from the Excel files; the old evidence PDF was moved to evidence/_reopened",
    );
  // Only the marks go back to the OCR positions; unsaved field edits stay.
  const reset = async () => {
    setBusy(true);
    try {
      const result = await api.resetMarks(itemId);
      setItem((current) => (current ? { ...current, marks: result.marks, mark_notes: result.mark_notes } : current));
      setMarks(result.marks);
      setSelectedId(null);
      setMessage({ kind: "ok", text: "Marks reset to the OCR positions" });
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const showPreview = async () => {
    if (dirty && editable && !(await save())) return;
    setPreview(Date.now());
  };

  const changeMarks = (next: Mark[]) => {
    setMarks(next);
    setDirty(true);
  };

  // Delete removes the selected mark; Ctrl+S saves.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = (event.target as HTMLElement).tagName === "INPUT";
      if ((event.key === "Delete" || event.key === "Backspace") && selectedId && editable && !typing) {
        changeMarks(marks.filter((mark) => mark.id !== selectedId));
        setSelectedId(null);
      }
      if (event.key === "s" && (event.ctrlKey || event.metaKey) && editable) {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!item) return <div className="review empty">{message ? message.text : "Loading…"}</div>;

  const pages = item.pages?.length ? item.pages : [{ page: item.page, width: item.image.width, height: item.image.height, bill: true }];
  const shown = pages[Math.min(pageIndex, pages.length - 1)];
  const page = { width: shown.width, height: shown.height };
  const pageMarks = marks.filter((mark) => isOnPage(mark, shown));
  // Edits from the viewer and panel cover this page only; marks elsewhere stay.
  const changePageMarks = (next: Mark[]) => changeMarks([...marks.filter((mark) => !isOnPage(mark, shown)), ...next]);
  const onShown = <T extends Mark>(mark: T): T => (shown.bill ? mark : { ...mark, page: shown.page ?? undefined });
  // Field positions are on the bill page.
  const focusBox = focused && shown.bill ? item.locations[focused]?.box ?? null : null;
  const pageLabel = item.page ? ` · page ${item.page}` : "";

  return (
    <div className="review">
      <header className="review-header">
        <div>
          <h2>{item.filename}{pageLabel}</h2>
          <span className={`badge ${item.status}`}>{item.status === "submitted" ? `Submitted by ${item.submitted_by}` : "Draft"}</span>
          {dirty && <span className="badge unsaved">Unsaved changes</span>}
        </div>
        <div className="actions">
          <button onClick={showPreview} disabled={busy}>Preview evidence</button>
          <a className="button" href={api.evidencePdfUrl(itemId)} onClick={(event) => { if (dirty) { event.preventDefault(); setMessage({ kind: "warn", text: "Save first so the PDF has your latest marks." }); } }}>
            Download PDF
          </a>
          {editable ? (
            <>
              <button onClick={save} disabled={busy || !dirty}>Save draft</button>
              <button className="primary" onClick={submit} disabled={busy}>Submit</button>
            </>
          ) : (
            <button onClick={reopen} disabled={busy}>Reopen</button>
          )}
        </div>
      </header>
      {message && <div className={`message ${message.kind}`}>{message.text}</div>}
      {item.issues.length > 0 && editable && (
        <ul className="issues">
          {item.issues.map((issue) => <li key={issue.en}>{issue.en}</li>)}
        </ul>
      )}
      <div className="review-body">
        <div className="viewer-column">
          {pages.length > 1 && (
            <div className="page-tabs" role="tablist">
              {pages.map((view, index) => {
                const count = marks.filter((mark) => isOnPage(mark, view) && mark.type === "label").length;
                return (
                  <button
                    key={view.page ?? "bill"}
                    role="tab"
                    aria-selected={index === pageIndex}
                    className={index === pageIndex ? "active" : ""}
                    onClick={() => { setPageIndex(index); setSelectedId(null); }}
                  >
                    {view.page ? `Page ${view.page}` : "Page"}{view.bill ? " · bill" : ""}
                    {count > 0 && <span>{count} {count === 1 ? "label" : "labels"}</span>}
                  </button>
                );
              })}
            </div>
          )}
          <PageViewer
            key={shown.page ?? "bill"}
            imageUrl={api.imageUrl(itemId, shown.bill ? null : shown.page)}
            width={page.width}
            height={page.height}
            marks={pageMarks}
            colors={defaultColors ?? item.colors}
            selectedId={selectedId}
            editable={editable}
            focusBox={focusBox}
            onSelect={setSelectedId}
            onChange={changePageMarks}
          />
        </div>
        <aside className="side">
          <MarksPanel
            marks={pageMarks}
            colors={defaultColors ?? item.colors}
            notes={item.mark_notes}
            selectedId={selectedId}
            editable={editable}
            onSelect={setSelectedId}
            onChange={changePageMarks}
            onAddHighlight={() => { const mark = onShown(newHighlight(page)); changeMarks([...marks, mark]); setSelectedId(mark.id); }}
            onAddLabel={(text) => { const mark = onShown(newLabel(page, text)); changeMarks([...marks, mark]); setSelectedId(mark.id); }}
            onReset={reset}
          />
          <FieldForm
            columns={columns}
            row={row}
            ocrRow={item.ocr_row}
            issues={item.issues}
            alternatives={item.alternatives}
            editable={editable}
            focused={focused}
            onFocus={setFocused}
            onChange={(column, value) => { setRow((current) => withOtherAdjustment({ ...current, [column]: value })); setDirty(true); }}
          />
        </aside>
      </div>
      {preview !== null && (
        <div className="modal" onClick={() => setPreview(null)}>
          <div className="modal-body" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <h3>Evidence preview{pages.length > 1 ? ` (${shown.page ? `page ${shown.page}` : "bill page"})` : ""} — exactly what the PDF/PNG will show</h3>
              <button onClick={() => setPreview(null)}>Close</button>
            </div>
            <img src={api.evidencePngUrl(itemId, preview, shown.bill ? null : shown.page)} alt="Evidence preview" />
          </div>
        </div>
      )}
    </div>
  );
}
