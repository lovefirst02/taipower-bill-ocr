"""Green power (轉供) documents and how they agree (kind "green").

Three documents, told apart by ``type``:

* ``statement`` — a retailer's statement table (e.g. wind): a line per meter
  and energy type with rates, transferred kWh and the charge;
* ``notice`` — a retailer's purchase notice (e.g. solar): one form for a
  transfer service (服務編號) with A–F, tax and total;
* ``transfer_list`` — Taipower's transfer fee list (費用清單) for a service and
  month: each fee table's rows per user meter (用戶電號) and generator.

Each is checked on its own (a statement line within the tolerance, a
notice's formula, Taipower's fees exactly). Then, per month and meter of the
company's sites, the transferred kWh in Taipower's list — the reference —
is compared with the retailers' documents and the Taipower bills, and a
notice's power supply fees (E) with Taipower's fees for those meters.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from taipower_bill_ocr.excel_export import COLUMNS
from taipower_bill_ocr.green_statement import check_line, is_green, read_statement, statement_period
from taipower_bill_ocr.keyword_extract import lines_from_dataset
from taipower_bill_ocr.periods import parse_period, split_by_month
from taipower_bill_ocr.purchase_notice import FIELDS as NOTICE_FIELDS
from taipower_bill_ocr.purchase_notice import WHOLE as NOTICE_WHOLE
from taipower_bill_ocr.purchase_notice import check_notice, read_notice
from taipower_bill_ocr.transfer_list import check_row, read_transfer_list

from .evidence import _highlight_and_label
from .settings import Settings
from .store import Store, meter_number

GREEN = "green"
STATEMENT, NOTICE, TRANSFER_LIST = "statement", "notice", "transfer_list"
TYPE_NAMES = {STATEMENT: "售電業者明細", NOTICE: "售電業者購電通知單", TRANSFER_LIST: "台電轉供費用清單"}
EDITABLE_FIELDS = ("meter", "energy", "base", "transfer_rate", "certificate_rate", "unit_price", "kwh", "cost")
ROW_FIELDS = ("section", "user_meter", "user_device", "generator", "generator_device", "kwh", "rate", "fee")
TRANSFERRED_KWH = [f"轉供度數_{label}" for label in ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")]
KWH_LABEL, COST_LABEL, FEE_LABEL = "Green Electricity (kWh)", "Green Electricity Cost", "Transfer Fees"


def type_of(statement: Mapping[str, Any] | None) -> str:
    return (statement or {}).get("type") or STATEMENT


# ------------------------------------------------------------------ reading
def page_type(lines: list) -> str | None:
    """Which green power document a page is (on its own), or None."""
    if read_transfer_list(lines) is not None:
        return TRANSFER_LIST
    if read_notice(lines) is not None:
        return NOTICE
    if read_statement(lines) is not None:
        return STATEMENT
    return None


def group_pages(pages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Green power documents among an upload's pages, in order: each one's pages.

    A transfer fee list or a retailer's statement runs over pages while its
    table goes on (a new 服務編號 or month starts another list). Other
    pages end a document.
    """
    readers = {TRANSFER_LIST: read_transfer_list, STATEMENT: read_statement}
    size = lambda read: len(getattr(read, "rows", None) or getattr(read, "lines", None) or [])
    lines_of = lambda group: [line for other in group for line in lines_from_dataset(other["dataset"], other["page_number"])]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    kind: str | None = None
    read: Any = None
    for page in pages:
        lines = lines_from_dataset(page["dataset"], page["page_number"])
        own = page_type(lines)
        if current is not None and kind in readers and own in (None, kind):
            alone = readers[kind](lines) if own else None
            # Taipower's next list (another service or month) starts anew.
            same = kind != TRANSFER_LIST or alone is None or (
                alone.service in (None, read.service) and alone.period in (None, read.period))
            together = readers[kind](lines_of([*current, page])) if same else None
            if together is not None and size(together) > size(read):
                current.append(page)
                read = together
                continue
        if own is not None:
            current, kind = [page], own
            read = readers[own](lines) if own in readers else None
            groups.append(current)
        else:
            current, kind, read = None, None, None
    return groups


def detect(store: Store, first: dict[str, Any], settings: Settings) -> dict[str, Any] | None:
    """Page columns for a green power document, or None when the upload is not one."""
    pages = store.item_pages(first)
    all_lines = [line for page in pages for line in lines_from_dataset(page["dataset"], page["page_number"])]
    for at, page in enumerate(pages):
        lines = lines_from_dataset(page["dataset"], page["page_number"])
        if page_type(lines) is None:
            continue
        # A transfer fee list may run onto the next pages.
        group = group_pages(pages[at:])[0]
        found = _read(lines, all_lines, page, group)
        if found is None:
            continue
        statement, marks = found
        row: dict[str, Any] = {column: None for column in COLUMNS}
        row.update({"Billing Period 計費期間": statement.get("period"), "來源檔案": first["filename"], "頁碼": first["page_number"]})
        for mark in marks:
            if "page" not in mark and page["id"] != first["id"]:
                mark["page"] = page["page_number"]
        return {
            "kind": GREEN, "is_bill": True, "row": row, "ocr_row": dict(row), "green": statement,
            "issues": issues_of(statement, settings), "marks": marks, "mark_notes": [],
            "sources": {}, "alternatives": {}, "locations": {},
        }
    return None


def blank(kind: str, first: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """An empty green power document of a type, to fill in by hand (OCR found none)."""
    statement: dict[str, Any] = {"type": kind, "period": None}
    if kind in (NOTICE, TRANSFER_LIST):
        statement["service"] = None
    statement.update({STATEMENT: {"lines": []}, NOTICE: {"values": {}, "boxes": {}}, TRANSFER_LIST: {"rows": []}}[kind])
    row: dict[str, Any] = {column: None for column in COLUMNS}
    row.update({"來源檔案": first["filename"], "頁碼": first["page_number"]})
    return {
        "kind": GREEN, "is_bill": True, "row": row, "ocr_row": dict(row), "green": statement,
        "issues": issues_of(statement, settings), "marks": [], "mark_notes": [],
        "sources": {}, "alternatives": {}, "locations": {},
    }


def detect_as(store: Store, first: dict[str, Any], settings: Settings, kind: str) -> dict[str, Any]:
    """The document read as the type the reviewer chose; empty when OCR finds no such table."""
    found = detect(store, first, settings)
    return found if found is not None and type_of(found["green"]) == kind else blank(kind, first, settings)


def _read(lines: list, all_lines: list, page: dict[str, Any], group: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    size = (page["image_width"], page["image_height"])
    taken = [(line["x"], line["y"], line["width"], line["height"]) for line in page["dataset"].get("ocr_lines") or []]
    group = group or [page]
    listed = read_transfer_list([line for other in group for line in lines_from_dataset(other["dataset"], other["page_number"])])
    if listed is not None:
        rows = [row.as_json() for row in listed.rows]
        statement = {"type": TRANSFER_LIST, "service": listed.service, "period": listed.period, "rows": rows}
        # A highlight over the rows on each page.
        marks = []
        for other in group:
            on_page = [row["box"] for row in rows if row.get("box") and row.get("page") == other["page_number"]]
            other_taken = [(line["x"], line["y"], line["width"], line["height"]) for line in other["dataset"].get("ocr_lines") or []]
            for mark in _column_marks(on_page, KWH_LABEL, (other["image_width"], other["image_height"]), other_taken):
                if other["id"] != page["id"]:
                    mark["page"] = other["page_number"]
                marks.append(mark)
        return statement, marks
    notice = read_notice(lines)
    if notice is not None:
        statement = {"type": NOTICE, "service": notice.service, "period": notice.period, "values": notice.values,
                     "boxes": {name: list(box) for name, box in notice.boxes.items()}}
        marks: list[dict[str, Any]] = []
        for name, label in (("kwh", KWH_LABEL), ("untaxed", COST_LABEL)):
            if name in notice.boxes:
                marks += _tag(_highlight_and_label(label, _pad(notice.boxes[name]), size, taken, []), f"green.{name}")
        return statement, marks
    table = read_statement([line for other in group for line in lines_from_dataset(other["dataset"], other["page_number"])])
    if table is not None:
        statement = {"type": STATEMENT, "period": statement_period(all_lines), "lines": [line.as_json() for line in table.lines]}
        # The kWh and charge columns marked on each page of the table.
        marks = []
        for other in group:
            other_taken = [(line["x"], line["y"], line["width"], line["height"]) for line in other["dataset"].get("ocr_lines") or []]
            for column, label in (("kwh", KWH_LABEL), ("cost", COST_LABEL)):
                boxes = [line.cells[column] for line in table.lines if column in line.cells and line.page == other["page_number"]]
                if not boxes:
                    continue
                for mark in _tag(_highlight_and_label(label, _pad(_span(boxes)), (other["image_width"], other["image_height"]), other_taken, []), f"green.{column}"):
                    if other["id"] != page["id"]:
                        mark["page"] = other["page_number"]
                    marks.append(mark)
        return statement, marks
    return None


def _span(boxes: list) -> tuple[float, float, float, float]:
    left, top = min(box[0] for box in boxes), min(box[1] for box in boxes)
    right, bottom = max(box[0] + box[2] for box in boxes), max(box[1] + box[3] for box in boxes)
    return (left, top, right - left, bottom - top)


def _pad(box, pad: float = 6) -> tuple[float, float, float, float]:
    return (box[0] - pad, box[1] - pad, box[2] + pad * 2, box[3] + pad * 2)


def _tag(marks: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    for mark in marks:
        mark["field"] = field
    return marks


def _column_marks(row_boxes: list, label: str, size, taken) -> list[dict[str, Any]]:
    """For Taipower's list: one highlight over the rows, labelled."""
    if not row_boxes:
        return []
    return _tag(_highlight_and_label(label, _pad(_span(row_boxes), 4), size, taken, []), "green.kwh")


# ------------------------------------------------------------------- checks
def checked_lines(statement: Mapping[str, Any], tolerance: float) -> list[dict[str, Any]]:
    return [{**line, "check": check_line(line, tolerance), "green": is_green(line)} for line in statement.get("lines") or []]


def checked_rows(statement: Mapping[str, Any], ours: set[str]) -> list[dict[str, Any]]:
    return [{**row, "check": check_row(row), "ours": not ours or row.get("user_meter") in ours} for row in statement.get("rows") or []]


def issues_of(statement: Mapping[str, Any], settings: Settings) -> list[dict[str, Any]]:
    kind = type_of(statement)
    name = TYPE_NAMES[kind]
    issues = []
    if not statement.get("period"):
        issues.append({"zh": f"找不到{name}的期間，請填寫", "en": "Period not found — enter it", "columns": ["Billing Period 計費期間"]})
    if kind == STATEMENT:
        for index, line in enumerate(checked_lines(statement, settings.green_tolerance), start=1):
            check = line["check"]
            if check["status"] == "check":
                issues.append({"zh": f"第 {index} 列（電號 {line.get('meter')}）本月電費與度數×費率相差 {check['difference']} 元",
                               "en": f"Line {index} (meter {line.get('meter')}): charge differs from kWh × rates by {check['difference']} NT$", "columns": []})
            elif check["status"] == "incomplete":
                issues.append({"zh": f"第 {index} 列（電號 {line.get('meter')}）缺少度數、電費或費率",
                               "en": f"Line {index} (meter {line.get('meter')}): kWh, charge or rates missing", "columns": []})
    elif kind == TRANSFER_LIST:
        if not statement.get("service"):
            issues.append({"zh": "找不到服務編號", "en": "Service number not found — enter it", "columns": []})
        for index, row in enumerate(statement.get("rows") or [], start=1):
            check = check_row(row)
            if check["status"] != "ok":
                issues.append({"zh": f"第 {index} 列（{row.get('section')}，電號 {row.get('user_meter')}）費用與度數×費率不符",
                               "en": f"Row {index} ({row.get('section')}, meter {row.get('user_meter')}): fee is not kWh × rate", "columns": []})
            if row.get("note"):
                issues.append({"zh": f"第 {index} 列：{row['note']}", "en": f"Row {index}: {row['note']}", "columns": []})
        if not statement.get("rows"):
            issues.append({"zh": "沒有讀到任何列，請手動新增", "en": "No rows read — add them by hand", "columns": []})
    else:
        if not statement.get("service"):
            issues.append({"zh": "找不到服務編號", "en": "Service number not found — enter it", "columns": []})
        missing = [field for field in ("purchase_rate", "kwh", "supply_fees", "untaxed", "total") if (statement.get("values") or {}).get(field) is None]
        if missing:
            issues.append({"zh": f"通知單缺少：{'、'.join(missing)}", "en": f"Notice values missing: {', '.join(missing)}", "columns": []})
        for check in check_notice(statement.get("values") or {}):
            if check["status"] == "check":
                issues.append({"zh": f"通知單 {check['label']} 計算不符，相差 {check['difference']} 元",
                               "en": f"Notice {check['label']} does not add up: {check['difference']} NT$ off", "columns": []})
    return issues


# ----------------------------------------------------------------- editing
def _number(value: Any, whole: bool = False) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"not a number: {value}") from None
    return int(number) if whole and number == number.to_integral_value() else float(number)


def update(page: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """The document as reviewed: only known fields, numbers as numbers."""
    current = dict(page.get("green") or {})
    kind = type_of(current)
    result: dict[str, Any] = {"type": kind, "period": (str(payload.get("period") or "").strip()) or None}
    if kind in (NOTICE, TRANSFER_LIST):
        service = payload.get("service") if "service" in payload else current.get("service")
        result["service"] = str(service).strip() or None if service else None
    if kind == STATEMENT:
        lines = []
        for line in payload.get("lines") or []:
            item = {"meter": meter_number(line.get("meter")), "energy": (str(line.get("energy") or "").strip() or None)}
            for name in ("base", "transfer_rate", "certificate_rate", "unit_price"):
                item[name] = _number(line.get(name))
            for name in ("kwh", "cost"):
                item[name] = _number(line.get(name), whole=True)
            for kept in ("page", "box", "cells"):
                if line.get(kept) is not None:
                    item[kept] = line[kept]
            lines.append(item)
        result["lines"] = lines
    elif kind == TRANSFER_LIST:
        rows = []
        for row in payload.get("rows") or []:
            item = {name: (str(row.get(name) or "").strip() or None) for name in ("section", "user_device", "generator_device")}
            item["user_meter"], item["generator"] = meter_number(row.get("user_meter")), meter_number(row.get("generator"))
            item["kwh"], item["fee"] = _number(row.get("kwh"), whole=True), _number(row.get("fee"), whole=True)
            item["rate"] = _number(row.get("rate"))
            for kept in ("page", "box", "note"):
                if row.get(kept) is not None:
                    item[kept] = row[kept]
            rows.append(item)
        result["rows"] = rows
    else:
        given = payload.get("values") or {}
        result["values"] = {name: _number(given.get(name), whole=name in NOTICE_WHOLE) for name in NOTICE_FIELDS if given.get(name) not in (None, "")}
        result["boxes"] = current.get("boxes") or {}
    return result


# ------------------------------------------------------------- comparing
def _month(period: str | None) -> str | None:
    parsed = parse_period(period)
    return f"{parsed[0].year:04d}-{parsed[0].month:02d}" if parsed else None


def transferred_kwh(row: Mapping[str, Any]) -> Any:
    values = [row.get(column) for column in TRANSFERRED_KWH if isinstance(row.get(column), (int, float))]
    return sum(values) if values else None


def _documents(store: Store) -> list[dict[str, Any]]:
    return [page for page in store.pages(bills_only=True) if page.get("kind") == GREEN and not page.get("hidden")]


def _bill_kwh(store: Store, meter: str, period: str | None) -> tuple[Any, str, list[dict[str, Any]]]:
    """The transferred kWh on the meter's Taipower bills for a period (prorated by days when needed)."""
    wanted = parse_period(period)
    if wanted is None:
        return None, "no period", []
    total, exact, used = Decimal(0), True, []
    for bill in store.pages(bills_only=True):
        if (bill.get("kind") or "taipower") != "taipower" or bill.get("hidden"):
            continue
        row = bill.get("row") or {}
        if meter_number(row.get("Custom No. 電號")) != meter:
            continue
        bill_period, kwh = parse_period(row.get("Billing Period 計費期間")), transferred_kwh(row)
        if bill_period is None or kwh is None:
            continue
        start, end = max(bill_period[0], wanted[0]), min(bill_period[1], wanted[1])
        if start > end:
            continue
        days = (bill_period[1] - bill_period[0]).days + 1
        total += Decimal(str(kwh)) * ((end - start).days + 1) / days
        exact = exact and bill_period == wanted
        used.append({"id": bill["id"], "filename": bill["filename"], "period": row.get("Billing Period 計費期間"), "kwh": kwh})
    if not used:
        return None, "none", []
    return _plain(total if exact else total.quantize(Decimal(1))), "same period" if exact else "prorated", used


def _plain(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def company_meters(store: Store) -> set[str]:
    """Meters set in the sites; empty when none are set (then every meter counts)."""
    return {meter for site in store.sites() for meter in site["meters"]}


def reconcile(store: Store) -> dict[str, Any]:
    """Every month's comparison: per meter (kWh) and per transfer service (notice vs Taipower's fees)."""
    ours = company_meters(store)
    documents = _documents(store)
    meters: dict[tuple[str, str], dict[str, Any]] = {}
    services: dict[tuple[str, str], dict[str, Any]] = {}

    def meter_entry(month: str, meter: str, period: str | None) -> dict[str, Any]:
        key = (month, meter)
        if key not in meters:
            site = store.site_of({"row": {"Custom No. 電號": meter}}) or {}
            meters[key] = {"month": month, "period": period, "meter": meter, "site": site.get("name"), "services": set(),
                           "taipower_list_kwh": None, "taipower_list_fees": None, "retailer_kwh": None, "documents": []}
        return meters[key]

    for page in documents:
        statement = page.get("green") or {}
        kind, month, period = type_of(statement), _month(statement.get("period")), statement.get("period")
        if month is None:
            continue
        reference = {"id": page["id"], "filename": page["filename"], "type": kind}
        if kind == TRANSFER_LIST:
            seen: set[tuple[str, str]] = set()
            for row in statement.get("rows") or []:
                meter = row.get("user_meter")
                if not meter or (ours and meter not in ours):
                    continue
                entry = meter_entry(month, meter, period)
                entry["services"].add(statement.get("service"))
                entry["documents"].append(reference)
                # Every fee table lists the same kWh: count each generator once.
                pair = (meter, row.get("generator"))
                if pair not in seen and row.get("kwh") is not None:
                    seen.add(pair)
                    entry["taipower_list_kwh"] = (entry["taipower_list_kwh"] or 0) + row["kwh"]
                if row.get("fee") is not None:
                    entry["taipower_list_fees"] = (entry["taipower_list_fees"] or 0) + row["fee"]
                service = services.setdefault((month, statement.get("service")), _service(month, statement.get("service")))
                if row.get("section"):
                    service["sections"].add(row["section"])
                # Per meter, so each company's notice is held against its own meters.
                per_meter = service["meters"].setdefault(meter, {"kwh": 0, "fees": 0})
                if pair in seen and row.get("kwh") is not None and row is _first(statement, pair):
                    per_meter["kwh"] += row["kwh"]
                per_meter["fees"] += row.get("fee") or 0
                if reference not in service["documents"]:
                    service["documents"].append(reference)
        elif kind == STATEMENT:
            for line in statement.get("lines") or []:
                if not line.get("meter") or not is_green(line) or line.get("kwh") is None:
                    continue
                entry = meter_entry(month, line["meter"], period)
                entry["retailer_kwh"] = (entry["retailer_kwh"] or 0) + line["kwh"]
                entry["documents"].append(reference)
        else:
            service = services.setdefault((month, statement.get("service")), _service(month, statement.get("service")))
            service["notices"].append((page, reference))

    meter_rows = []
    for entry in sorted(meters.values(), key=lambda item: (item["month"], item["meter"])):
        bill_kwh, how, bills = _bill_kwh(store, entry["meter"], entry["period"])
        reference = entry["taipower_list_kwh"]
        entry.update(
            services=sorted(service for service in entry["services"] if service),
            bill_kwh=bill_kwh, bill_how=how, bills=bills,
            retailer_difference=None if reference is None or entry["retailer_kwh"] is None else entry["retailer_kwh"] - reference,
            bill_difference=None if reference is None or bill_kwh is None else _plain(Decimal(str(bill_kwh)) - Decimal(str(reference))),
            # Without Taipower's list, the retailer's kWh can still be held against the bill.
            retailer_bill_difference=None if entry["retailer_kwh"] is None or bill_kwh is None else _plain(Decimal(str(entry["retailer_kwh"])) - Decimal(str(bill_kwh))),
        )
        meter_rows.append(entry)
    service_rows = []
    meter_entity = store.meter_entities()
    for service in sorted(services.values(), key=lambda item: (item["month"], item["service"] or "")):
        has_list = bool(service["documents"])
        # One row per retailer's notice (each company of the group has its
        # own), against that company's meters; a list without a notice alone.
        for page, reference in service["notices"] or [(None, None)]:
            owner = store.entity_of(dict(page)) if page is not None else None
            if owner is not None:
                meters_in = {meter: values for meter, values in service["meters"].items() if (meter_entity.get(meter) or {}).get("id") == owner["id"]}
            elif page is None or len(service["notices"]) == 1:
                meters_in = service["meters"]
            else:
                meters_in = None  # several notices and none told apart
            notice = ((page.get("green") or {}).get("values") or {}) if page is not None else {}
            listed_kwh = sum(values["kwh"] for values in meters_in.values()) if meters_in is not None else None
            listed_fees = sum(values["fees"] for values in meters_in.values()) if meters_in is not None else None
            compared = has_list and meters_in is not None
            service_rows.append({
                "month": service["month"], "service": service["service"], "entity": owner.get("name") if owner else None,
                "taipower_list_kwh": listed_kwh if listed_kwh is not None else 0,
                "taipower_list_fees": listed_fees if listed_fees is not None else 0,
                "notice": {"kwh": notice.get("kwh"), "supply_fees": notice.get("supply_fees")} if page is not None else None,
                "documents": [*service["documents"], *([reference] if reference else [])],
                "has_list": has_list,
                "entity_unknown": meters_in is None,
                # The fee tables read (輸電, 配電, ...): a list missing some cannot add up to the notice's E.
                "sections": sorted(service["sections"]),
                "kwh_difference": None if not compared or notice.get("kwh") is None else notice["kwh"] - listed_kwh,
                "fee_difference": None if not compared or notice.get("supply_fees") is None else notice["supply_fees"] - listed_fees,
            })
    return {"meters": meter_rows, "services": service_rows, "sites_set": bool(ours)}


def _service(month: str, service: str | None) -> dict[str, Any]:
    return {"month": month, "service": service, "meters": {}, "sections": set(), "notices": [], "documents": []}


def _first(statement: Mapping[str, Any], pair: tuple[str, str]) -> Mapping[str, Any] | None:
    for row in statement.get("rows") or []:
        if (row.get("user_meter"), row.get("generator")) == pair:
            return row
    return None


def comparisons_for(store: Store, page: Mapping[str, Any]) -> dict[str, Any]:
    """The comparisons that involve this document."""
    every = reconcile(store)
    mine = lambda entry: any(document["id"] == page["id"] for document in entry["documents"])  # noqa: E731
    return {
        "meters": [entry for entry in every["meters"] if mine(entry)],
        "services": [entry for entry in every["services"] if mine(entry)],
        "sites_set": every["sites_set"],
    }


# ------------------------------------------------------------------- views
def view(store: Store, page: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    statement = page.get("green") or {}
    kind = type_of(statement)
    result: dict[str, Any] = {"type": kind, "type_name": TYPE_NAMES[kind], "period": statement.get("period"),
                              "service": statement.get("service"), "tolerance": settings.green_tolerance}
    if kind == STATEMENT:
        lines = checked_lines(statement, settings.green_tolerance)
        result.update(lines=lines, total_kwh=sum(line.get("kwh") or 0 for line in lines if line["green"]),
                      total_cost=sum(line.get("cost") or 0 for line in lines))
    elif kind == TRANSFER_LIST:
        rows = checked_rows(statement, company_meters(store))
        result.update(rows=rows, total_fees=sum(row.get("fee") or 0 for row in rows if row["ours"]))
    else:
        values = statement.get("values") or {}
        result.update(values=values, checks=check_notice(values), total_cost=values.get("untaxed"))
    result["comparisons"] = comparisons_for(store, page)
    return result


def total_cost(page: Mapping[str, Any]) -> Any:
    statement = page.get("green") or {}
    kind = type_of(statement)
    if kind == STATEMENT:
        return sum(line.get("cost") or 0 for line in statement.get("lines") or [])
    if kind == NOTICE:
        return (statement.get("values") or {}).get("untaxed")
    return sum(row.get("fee") or 0 for row in statement.get("rows") or [])


def master_fields(statement: Mapping[str, Any]) -> Iterable[tuple[str, str, Any, Any]]:
    """(key, label, value, box) for the master's Fields."""
    kind = type_of(statement)
    if kind == STATEMENT:
        for index, line in enumerate(statement.get("lines") or [], start=1):
            for name in EDITABLE_FIELDS:
                if line.get(name) is not None:
                    yield f"line{index}.{name}", name, line[name], line.get("box")
    elif kind == TRANSFER_LIST:
        for index, row in enumerate(statement.get("rows") or [], start=1):
            for name in ROW_FIELDS:
                if row.get(name) is not None:
                    yield f"row{index}.{name}", name, row[name], row.get("box")
    else:
        boxes = statement.get("boxes") or {}
        for name, value in (statement.get("values") or {}).items():
            yield name, name, value, boxes.get(name)


# ----------------------------------------------------------------- reports
GREEN_COLUMNS = [
    "文件", "據點", "據點代碼", "電號", "服務編號", "月份", "天數", "能源別", "基準價", "轉供費率", "憑證費率", "轉供度數", "金額",
    "計算金額", "差額", "核對", "Billing Period 計費期間", "來源檔案", "狀態", "上傳者", "送出者", "證據檔",
]
LIST_COLUMNS = ["月份", "服務編號", "費用項目", "據點", "本公司", "用戶電號", "用戶表號", "發電電號", "發電表號", "轉供度數", "費率", "費用", "核對", "備註", "來源檔案"]
CHECK_COLUMNS = ["月份", "據點", "電號", "服務編號", "台電清單轉供度數", "售電業者轉供度數", "差異（售電業者−台電清單）",
                 "台電帳單轉供度數", "差異（帳單−台電清單）", "帳單比對方式", "台電清單轉供費用"]
SERVICE_COLUMNS = ["月份", "服務編號", "Entity", "台電清單轉供度數（本公司）", "通知單購電量 B", "差異（B−台電清單）",
                   "台電清單轉供費用（本公司）", "通知單電能供應費用 E", "差異（E−台電清單）", "台電清單費用項目"]
HOW = {"same period": "期間相同", "prorated": "期間不同，依天數換算", "none": "找不到台電帳單", "no period": "沒有期間"}
STATUS = {"ok": "符合", "check": "需確認", "incomplete": "資料不全"}


def green_rows(pages: Iterable[Mapping[str, Any]], store: Store, tolerance: float, common_of) -> list[list[Any]]:
    """Retailers' statement lines and purchase notices, per calendar month, with their checks."""
    rows = []
    for page in pages:
        statement = page.get("green") or {}
        kind, common = type_of(statement), common_of(page)
        entries = []
        if kind == STATEMENT:
            for line in checked_lines(statement, tolerance):
                check = line["check"]
                entries.append(({"電號": line.get("meter"), "能源別": line.get("energy"), "基準價": line.get("base"),
                                 "轉供費率": line.get("transfer_rate"), "憑證費率": line.get("certificate_rate"),
                                 "差額": check.get("difference"), "核對": STATUS[check["status"]]},
                                {"轉供度數": line.get("kwh"), "金額": line.get("cost"), "計算金額": check.get("expected")}))
        elif kind == NOTICE:
            values = statement.get("values") or {}
            checks = check_notice(values)
            worst = "check" if any(c["status"] == "check" for c in checks) else "incomplete" if any(c["status"] == "incomplete" for c in checks) else "ok"
            entries.append(({"能源別": "購電通知單（未稅）", "基準價": values.get("purchase_rate"), "核對": STATUS[worst],
                             "差額": next((c["difference"] for c in checks if c["field"] == "untaxed"), None)},
                            {"轉供度數": values.get("kwh"), "金額": values.get("untaxed"),
                             "計算金額": next((c["expected"] for c in checks if c["field"] == "untaxed"), None)}))
        else:
            continue
        for fixed, amounts in entries:
            site = store.site_of({"row": {"Custom No. 電號": fixed.get("電號")}}) or {} if fixed.get("電號") else {}
            shares = [{"月份": share.month, "天數": share.days, **share.amounts} for share in split_by_month(statement.get("period"), amounts)]
            for share in shares or [{"月份": None, "天數": None, **amounts}]:
                values = {**common, "文件": TYPE_NAMES[kind], "據點": site.get("name"), "據點代碼": site.get("code"),
                          "服務編號": statement.get("service"), "Billing Period 計費期間": statement.get("period"), **fixed, **share}
                rows.append([values.get(column) for column in GREEN_COLUMNS])
    return rows


def list_rows(pages: Iterable[Mapping[str, Any]], store: Store) -> list[list[Any]]:
    """Taipower's transfer fee lists, row by row, each checked."""
    ours = company_meters(store)
    rows = []
    for page in pages:
        statement = page.get("green") or {}
        if type_of(statement) != TRANSFER_LIST:
            continue
        for row in checked_rows(statement, ours):
            site = store.site_of({"row": {"Custom No. 電號": row.get("user_meter")}}) or {}
            rows.append([_month(statement.get("period")), statement.get("service"), row.get("section"), site.get("name"),
                         "是" if row["ours"] else "否", row.get("user_meter"), row.get("user_device"), row.get("generator"),
                         row.get("generator_device"), row.get("kwh"), row.get("rate"), row.get("fee"),
                         STATUS[row["check"]["status"]], row.get("note"), page.get("filename")])
    return rows


def check_rows(store: Store) -> tuple[list[list[Any]], list[list[Any]]]:
    """The three-way comparison: per meter, and per transfer service."""
    every = reconcile(store)
    meter_rows = [[
        entry["month"], entry["site"], entry["meter"], "、".join(entry["services"]) or None, entry["taipower_list_kwh"],
        entry["retailer_kwh"], entry["retailer_difference"], entry["bill_kwh"], entry["bill_difference"],
        HOW.get(entry["bill_how"], entry["bill_how"]), entry["taipower_list_fees"],
    ] for entry in every["meters"]]
    service_rows = [[
        entry["month"], entry["service"], entry["entity"] or ("（多張通知單，未指定 Entity）" if entry["entity_unknown"] else None),
        entry["taipower_list_kwh"] if entry["has_list"] and not entry["entity_unknown"] else None,
        (entry["notice"] or {}).get("kwh"), entry["kwh_difference"],
        entry["taipower_list_fees"] if entry["has_list"] and not entry["entity_unknown"] else None,
        (entry["notice"] or {}).get("supply_fees"), entry["fee_difference"], "、".join(entry["sections"]) or None,
    ] for entry in every["services"]]
    return meter_rows, service_rows
