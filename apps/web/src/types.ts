export type Row = Record<string, string | number | null>;

export interface Column {
  key: string;
  label: string;
  editable: boolean;
  numeric: boolean;
}

export interface Highlight {
  id: string;
  type: "highlight";
  x: number;
  y: number;
  w: number;
  h: number;
  group?: string;
  /** Another page of the PDF (the bill's back page); absent for the bill page. */
  page?: number;
}

export interface Label {
  id: string;
  type: "label";
  x: number;
  y: number;
  text: string;
  fontSize: number;
  group?: string;
  page?: number;
}

export type Mark = Highlight | Label;

export interface Issue {
  zh: string;
  en: string;
  columns: string[];
}

export interface Upload {
  id: number;
  filename: string;
  status: "queued" | "processing" | "paused" | "done" | "failed";
  error: string | null;
  page_count: number | null;
  pages_done: number;
  created_at: string;
}

export interface ItemSummary {
  id: number;
  upload_id: number;
  filename: string;
  page: number | null;
  status: "draft" | "submitted";
  needs_review: boolean;
  issue_count: number;
  billing_period: string | null;
  customer_number: string | null;
  total: number | null;
  submitted_at: string | null;
}

export interface Location {
  box: [number, number, number, number];
  exact: boolean;
}

export interface Item {
  id: number;
  user: string;
  filename: string;
  page: number | null;
  status: "draft" | "submitted";
  row: Row;
  ocr_row: Row;
  issues: Issue[];
  alternatives: Record<string, string | number>;
  locations: Record<string, Location>;
  marks: Mark[];
  mark_notes: string[];
  image: { width: number; height: number; dpi: number };
  /** The bill page first, then its back pages, which can hold marks too. */
  pages: ViewPage[];
  submitted_at: string | null;
  submitted_by: string | null;
  locked_workbooks?: string[];
  archived_evidence?: string | null;
  evidence_file?: string;
}

export interface ViewPage {
  page: number | null;
  width: number;
  height: number;
  bill: boolean;
}

export interface OutputInfo {
  workbooks: "shared" | "per_user";
  folder: string;
  evidence_folder: string;
}
