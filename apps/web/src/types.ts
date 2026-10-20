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
  /** "#rrggbb"; absent follows the default colour in the settings. */
  color?: string;
  /** The value this mark is evidence for; picking a new value replaces it. */
  field?: string;
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
  color?: string;
  field?: string;
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
  kind: DocumentKind;
  site: string | null;
  submitted_at: string | null;
}

export interface Site {
  id: number;
  name: string;
  code: string | null;
  note: string | null;
  /** 電號, digits only. */
  meters: string[];
}

/** A bill's site: chosen by hand, or the one its 電號 belongs to. */
export interface ItemSite extends Site {
  how: "manual" | "meter";
}

/** A bill's share of one calendar month (split by days). */
export interface MonthShare {
  月份: string;
  天數: number;
  用電度數: number | null;
  轉供度數: number | null;
  稅前應繳總金額: number | null;
  應繳總金額: number | null;
}

/** A Taipower bill, or another document (e.g. a landlord's allocation). */
export type DocumentKind = "taipower" | "other";

/** Where a value came from. */
export type ValueSource = "taipower" | "keyword" | "pick" | "manual" | "alternative";

export interface OcrLine {
  text: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Location {
  box: [number, number, number, number];
  exact: boolean;
  /** The page it is on, when not the item's own page. */
  page?: number | null;
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
  /** Default colours for marks without their own. */
  colors: MarkColors;
  kind: DocumentKind;
  sources: Record<string, ValueSource>;
  site: ItemSite | null;
  months: MonthShare[];
  submitted_at: string | null;
  submitted_by: string | null;
  locked_workbooks?: string[];
  archived_evidence?: string | null;
  evidence_file?: string;
}

export interface MarkColors {
  highlight: string;
  label: string;
}

export interface ViewPage {
  page: number | null;
  width: number;
  height: number;
  bill: boolean;
  /** OCR text on the page, for picking values on the image. */
  lines: OcrLine[];
}

export interface OutputInfo {
  workbooks: "shared" | "per_user";
  folder: string;
  evidence_folder: string;
}

export interface SettingsValues {
  output_dir: string;
  workbooks: "shared" | "per_user";
  ocr_backend: string;
  workbook_retry_seconds: number;
  host: string;
  port: number;
  highlight_color: string;
  label_color: string;
  company_names: string[];
  self_terms: string[];
  period_keywords: string[];
  usage_keywords: string[];
  cost_keywords: string[];
  exclude_keywords: string[];
}

export type KeywordSetting = "company_names" | "self_terms" | "period_keywords" | "usage_keywords" | "cost_keywords" | "exclude_keywords";

export interface SettingsView {
  values: SettingsValues;
  /** Fixed by environment variables. */
  locked: (keyof SettingsValues)[];
  /** Only on the computer running the server. */
  can_edit: boolean;
  restart_required: string[];
  running: { host: string; port: number } | null;
  options: { workbooks: string[]; ocr_backend: string[] };
  dpi: number;
  data_dir: string;
  keyword_defaults: Record<KeywordSetting, string[]>;
}

export interface SettingsSaved extends SettingsView {
  changed: string[];
  restart: boolean;
  locked_workbooks: string[];
}
