import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { isOnPage, labelSize, markBox, newHighlight, newId, newLabel } from "../marks";
import { lineBoxes, linesAt, linesBox, PICK_LABELS, placeLabel, valueFor } from "../pick";
import type { Box } from "../pick";
import type { Column, Issue, Item, Mark, MarkColors, Row, Site, ValueSource, ViewPage } from "../types";
import { FieldForm } from "./FieldForm";
import { draftOf, GreenTable } from "./GreenTable";
import type { GreenDraft } from "./GreenTable";
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
  /** Changes when the settings dialog closes (sites may have changed). */
  settingsVersion?: number;
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

/**
 * A highlight over a picked value and, when the field has one, its red label
 * where it hides the least printed text or other marks.
 */
function pickMarks(field: string | undefined, area: Box, page: ViewPage, label: string | undefined, others: Mark[]): Mark[] {
  const where = { ...(page.bill ? {} : { page: page.page ?? undefined }), ...(field ? {} : { group: newId() }) };
  const pad = 4;
  const highlight: Mark = {
    id: newId(), type: "highlight", field, ...where,
    x: Math.max(0, area.x - pad), y: Math.max(0, area.y - pad), w: area.w + pad * 2, h: area.h + pad * 2,
  };
  if (!label) return [highlight];
  const fontSize = Math.max(28, Math.min(area.h * 1.1, 60));
  const size = labelSize({ text: label, fontSize });
  const obstacles = [...lineBoxes(page.lines ?? []), ...others.map(markBox)];
  const spot = placeLabel(highlight as Box, size, page, obstacles);
  const text: Mark = { id: newId(), type: "label", field, ...where, text: label, fontSize, x: spot.x, y: spot.y };
  return [highlight, text];
}

export function Review({ itemId, user, columns, onChanged, onNext, onNotify, colors: defaultColors, settingsVersion = 0 }: Props) {
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
  const [sources, setSources] = useState<Record<string, ValueSource>>({});
  // The field whose value is being picked on the image.
  const [picking, setPicking] = useState<string | null>(null);
  // The tag being placed on the image (a highlight and its red label).
  const [tagging, setTagging] = useState<string | null>(null);
  const [tags, setTags] = useState<{ mark_tags: string[]; defaults: string[] }>({ mark_tags: [], defaults: [] });
  // Warnings still shown; the reviewer can clear them.
  const [issues, setIssues] = useState<Issue[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  // The site chosen by hand: undefined = unchanged, null = by 電號.
  const [siteChoice, setSiteChoice] = useState<number | null | undefined>(undefined);
  // A green power statement being edited (kind "green").
  const [greenDraft, setGreenDraft] = useState<GreenDraft | null>(null);
  const [greenEdited, setGreenEdited] = useState(false);

  useEffect(() => {
    api.tags(user).then(setTags).catch(() => undefined);
  }, [user]);

  // The site list, again after the settings close; the bill's own site and
  // months are refreshed too, unless there are unsaved edits to keep.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    api.sites().then(setSites).catch(() => undefined);
    if (settingsVersion === 0 || dirtyRef.current) return;
    api.item(itemId)
      .then((loaded) => setItem((current) => (current ? { ...current, site: loaded.site, months: loaded.months } : current)))
      .catch(() => undefined);
  }, [settingsVersion, itemId]);

  const load = useCallback((loaded: Item) => {
    setItem(loaded);
    setRow(loaded.row);
    setMarks(loaded.marks);
    setSources(loaded.sources ?? {});
    setIssues(loaded.issues ?? []);
    setNotes(loaded.mark_notes ?? []);
    setSiteChoice(undefined);
    setGreenDraft(loaded.green ? draftOf(loaded.green) : null);
    setGreenEdited(false);
    setPicking(null);
    setTagging(null);
    setDirty(false);
  }, []);

  useEffect(() => {
    setItem(null);
    setPageIndex(0);
    setSelectedId(null);
    setFocused(null);
    setMessage(null);
    api.item(itemId)
      .then((loaded) => {
        load(loaded);
        // Pages are in document order; open on the bill page itself.
        setPageIndex(Math.max(0, (loaded.pages ?? []).findIndex((view) => view.bill)));
      })
      .catch((error: Error) => setMessage({ kind: "error", text: error.message }));
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

  const kept = {
    issues,
    mark_notes: notes,
    ...(siteChoice !== undefined ? { site_id: siteChoice } : {}),
    ...(greenEdited && greenDraft ? { green: greenDraft } : {}),
  };
  const save = () => run(() => api.save(itemId, user, row, marks, sources, kept), "Draft saved");
  const redetect = () => run(() => api.redetect(itemId), "Looked for the values again with the current keywords");
  const submit = async () => {
    const result = await run(() => api.submit(itemId, user, row, marks, sources, kept), "Submitted — saved to the Excel files with its evidence PDF");
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
      setNotes(result.mark_notes ?? []);
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
      if (event.key === "Escape" && (picking || tagging)) {
        setPicking(null);
        setTagging(null);
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

  const pages: ViewPage[] = item.pages?.length ? item.pages : [{ page: item.page, width: item.image.width, height: item.image.height, bill: true, lines: [] }];
  const shown = pages[Math.min(pageIndex, pages.length - 1)];
  const page = { width: shown.width, height: shown.height };
  const pageMarks = marks.filter((mark) => isOnPage(mark, shown));
  // Edits from the viewer and panel cover this page only; marks elsewhere stay.
  const changePageMarks = (next: Mark[]) => changeMarks([...marks.filter((mark) => !isOnPage(mark, shown)), ...next]);
  const onShown = <T extends Mark>(mark: T): T => (shown.bill ? mark : { ...mark, page: shown.page ?? undefined });
  // A field's position, when it is on the page shown.
  const location = focused ? item.locations[focused] : undefined;
  const locationPage = location?.page ?? item.page;
  const focusBox = location && (shown.bill ? locationPage === item.page : locationPage === shown.page) ? location.box : null;
  const columnOf = (key: string) => columns.find((column) => column.key === key);

  const setValue = (column: string, value: string | number | null, source: ValueSource) => {
    setRow((current) => withOtherAdjustment({ ...current, [column]: value }));
    setSources((current) => ({ ...current, [column]: source }));
    setDirty(true);
  };

  const dismissIssue = (issue?: Issue) => {
    setIssues((current) => (issue ? current.filter((other) => other.en !== issue.en) : []));
    setDirty(true);
  };
  const dismissNote = (note?: string) => {
    setNotes((current) => (note ? current.filter((other) => other !== note) : []));
    setDirty(true);
  };
  const startPicking = (column: string | null) => {
    setTagging(null);
    setPicking(column);
  };
  const startTagging = (tag: string | null) => {
    setPicking(null);
    setTagging(tag);
  };
  const saveTags = async (next: string[] | null) => {
    try {
      setTags(await api.saveTags(user, next));
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    }
  };

  // A tag placed on the page: a highlight over what was picked and the tag beside it.
  const placeTag = (tag: string, box: Box) => {
    const picked = linesAt(shown.lines ?? [], box);
    const area = picked.length ? linesBox(picked) : box.w > 6 && box.h > 6 ? box : null;
    if (area === null) {
      setMessage({ kind: "warn", text: "No text there. Click on the value, or drag a box around it." });
      return;
    }
    // Always added, never replacing anything: a tag may appear as often as needed,
    // and its marks belong to no field, so picking a field's value leaves them.
    const added = pickMarks(undefined, area, shown, tag, marks.filter((mark) => isOnPage(mark, shown)));
    changeMarks([...marks, ...added]);
    setSelectedId(added[0].id);
    setTagging(null);
    setMessage({ kind: "ok", text: `“${tag}” added` });
  };

  // A click, or a dragged box, on the page while picking a field's value.
  const pick = (box: Box) => {
    if (tagging) return placeTag(tagging, box);
    if (!picking) return;
    const column = picking;
    const picked = linesAt(shown.lines ?? [], box);
    const value = valueFor(column, columnOf(column)?.numeric ?? false, picked, box);
    const area = picked.length ? linesBox(picked) : box.w > 6 && box.h > 6 ? box : null;
    if (area === null) {
      setMessage({ kind: "warn", text: "No text there. Click on the value, or drag a box around it." });
      return;
    }
    if (value !== null) setValue(column, value, "pick");
    const kept = marks.filter((mark) => mark.field !== column);
    const added = pickMarks(column, area, shown, PICK_LABELS[column], kept.filter((mark) => isOnPage(mark, shown)));
    changeMarks([...kept, ...added]);
    setSelectedId(added[0].id);
    setPicking(null);
    setMessage(
      value === null
        ? { kind: "warn", text: "Could not read a value there — the highlight is added; type the value in the field." }
        : { kind: "ok", text: `${columnOf(column)?.label ?? column}: ${value} (picked on the image)` },
    );
  };
  const pageLabel = item.page ? ` · page ${item.page}` : "";

  return (
    <div className="review">
      <header className="review-header">
        <div>
          <h2>{item.filename}{pageLabel}</h2>
          <span className={`badge ${item.status}`}>{item.status === "submitted" ? `Submitted by ${item.submitted_by}` : "Draft"}</span>
          <label className="site-picker" title="Where this bill belongs; by default the site of its 電號 (set in Settings → Sites)">
            Site
            <select
              value={siteChoice === undefined ? (item.site?.how === "manual" ? String(item.site.id) : "") : siteChoice === null ? "" : String(siteChoice)}
              disabled={!editable}
              onChange={(event) => {
                setSiteChoice(event.target.value ? Number(event.target.value) : null);
                setDirty(true);
              }}
            >
              <option value="">{item.site?.how === "meter" ? `${item.site.name} (by 電號)` : "— by 電號 (none found) —"}</option>
              {sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
            </select>
          </label>
          {dirty && <span className="badge unsaved">Unsaved changes</span>}
        </div>
        <div className="actions">
          <button onClick={showPreview} disabled={busy}>Preview evidence</button>
          <a className="button" href={api.evidencePdfUrl(itemId)} onClick={(event) => { if (dirty) { event.preventDefault(); setMessage({ kind: "warn", text: "Save first so the PDF has your latest marks." }); } }}>
            Download PDF
          </a>
          {editable ? (
            <>
              {item.kind === "other" && (
                <button onClick={redetect} disabled={busy} title="Look for the values again with the keywords in Settings (replaces the values and marks)">
                  Detect again
                </button>
              )}
              <button onClick={save} disabled={busy || !dirty}>Save draft</button>
              <button className="primary" onClick={submit} disabled={busy}>Submit</button>
            </>
          ) : (
            <button onClick={reopen} disabled={busy}>Reopen</button>
          )}
        </div>
      </header>
      {message && <div className={`message ${message.kind}`}>{message.text}</div>}
      {issues.length > 0 && editable && (
        <ul className="issues">
          {issues.map((issue) => (
            <li key={issue.en}>
              <span>{issue.en}</span>
              <button type="button" className="icon" aria-label="Clear this warning" title="Clear this warning" onClick={() => dismissIssue(issue)}>✕</button>
            </li>
          ))}
          {issues.length > 1 && (
            <li className="issues-actions">
              <button type="button" className="link clear-all" onClick={() => dismissIssue()}>Clear all warnings</button>
            </li>
          )}
        </ul>
      )}
      <div className={item.kind === "green" ? "review-body wide-side" : "review-body"}>
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
            picking={
              tagging
                ? `Click the value for “${tagging}” on the page, or drag a box around it · Esc to stop`
                : picking
                  ? `Click the ${columnOf(picking)?.label ?? picking} on the page, or drag a box around it · Esc to stop`
                  : null
            }
            onPick={pick}
          />
        </div>
        <aside className="side">
          <MarksPanel
            marks={pageMarks}
            colors={defaultColors ?? item.colors}
            notes={notes}
            onDismissNote={dismissNote}
            tags={tags.mark_tags}
            defaultTags={tags.defaults}
            tagging={tagging}
            onTag={startTagging}
            onSaveTags={saveTags}
            selectedId={selectedId}
            editable={editable}
            onSelect={setSelectedId}
            onChange={changePageMarks}
            onAddHighlight={() => { const mark = onShown(newHighlight(page)); changeMarks([...marks, mark]); setSelectedId(mark.id); }}
            onAddLabel={(text) => { const mark = onShown(newLabel(page, text)); changeMarks([...marks, mark]); setSelectedId(mark.id); }}
            onReset={reset}
          />
          {item.months.length > 0 && (
            <div className="months" title="This bill split by days across calendar months, as saved; the Excel Monthly sheet uses it">
              <h4>By month</h4>
              <ul>
                {item.months.map((month) => (
                  <li key={month.月份}>
                    <strong>{month.月份}</strong> · {month.天數} days
                    {month.用電度數 !== null && ` · ${month.用電度數.toLocaleString()} kWh`}
                    {month.稅前應繳總金額 !== null && ` · NT$${month.稅前應繳總金額.toLocaleString()} before tax`}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {item.green && greenDraft ? (
            <GreenTable
              view={item.green}
              draft={greenDraft}
              editable={editable}
              ourMeters={new Set(sites.flatMap((site) => site.meters))}
              onChange={(next) => {
                setGreenDraft(next);
                setGreenEdited(true);
                setDirty(true);
              }}
            />
          ) : (
          <FieldForm
            columns={columns}
            row={row}
            ocrRow={item.ocr_row}
            issues={issues}
            alternatives={item.alternatives}
            editable={editable}
            focused={focused}
            onFocus={setFocused}
            onChange={setValue}
            sources={sources}
            picking={picking}
            onPick={editable ? startPicking : undefined}
          />
          )}
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
