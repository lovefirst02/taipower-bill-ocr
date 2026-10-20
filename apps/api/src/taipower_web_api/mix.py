"""Grey and green power per month and meter: what the dashboard and the EnergyMix sheet show.

* Grey kWh are what Taipower bills: the bill's 計費度數 (Taipower charges
  usage after the transferred kWh are taken off). Documents without them,
  such as a landlord's allocation, give their 用電度數.
* Green kWh come from Taipower's transfer fee list (the reference), else the
  retailer's statement, else the bill's own 轉供度數. Each is put under an
  energy (太陽能, 風力, ...): a statement line says which; a service of
  Taipower's list or a purchase notice takes it from Settings → Green power
  services, else from a statement for the same meters and month.
* Costs: grey is the bill's 稅前應繳總金額; green is the retailer's amounts
  (a statement's lines, a notice's untaxed total shared among the service's
  meters by their kWh in Taipower's list).

Bills over several months are split by days, as in the Monthly sheet.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable, Mapping

from taipower_bill_ocr.green_statement import is_green
from taipower_bill_ocr.periods import split_by_month

from . import documents, green
from .store import Store, meter_number

SOLAR, WIND, UNCLASSIFIED = "太陽能", "風力", "未分類"
_BILLED_KWH = [f"計費度數_{label}" for label in ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")]
MIX_COLUMNS = [
    "月份", "據點", "據點代碼", "電號", "灰電度數", "太陽能度數", "風力度數", "其他綠電度數", "未分類綠電度數",
    "綠電度數合計", "總用電度數", "綠電占比", "灰電費用（台電稅前）", "綠電費用（售電業者未稅）", "綠電度數來源",
]
SOURCE_NAMES = {"transfer_list": "台電轉供清單", "statement": "售電業者明細", "bill": "台電帳單轉供度數"}


def energy_name(text: Any) -> str:
    """太陽能 / 風力 / the name as written; 未分類 when there is none."""
    value = str(text or "").strip()
    if not value:
        return UNCLASSIFIED
    if re.search(r"太陽|光電|solar|pv", value, re.I):
        return SOLAR
    if re.search(r"風|wind", value, re.I):
        return WIND
    return value


def parse_services(lines: Iterable[str]) -> dict[str, str]:
    """Settings lines "1-11210-08-00210 太陽能" as {service: energy}."""
    services = {}
    for line in lines:
        parts = str(line).split(None, 1)
        if len(parts) == 2:
            services[_service_key(parts[0])] = energy_name(parts[1])
    return services


def _service_key(service: Any) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(service or ""))


def _number(value: Any) -> Decimal | None:
    return Decimal(str(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _total(row: Mapping[str, Any], columns: list[str]) -> Any:
    values = [row.get(column) for column in columns if isinstance(row.get(column), (int, float))]
    return sum(values) if values else None


def _plain(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(round(value, 2))


def energy_mix(pages: Iterable[Mapping[str, Any]], store: Store, service_energy: Mapping[str, str]) -> dict[str, Any]:
    """Per month and meter: grey and green kWh (by energy) and their costs."""
    pages = [page for page in pages if not page.get("hidden")]
    ours = green.company_meters(store)
    # The same document uploaded twice (or by two people) counts once.
    seen_documents: dict[Any, Mapping[str, Any]] = {}
    duplicates: dict[int, Mapping[str, Any]] = {}
    for page in sorted(pages, key=lambda item: item["id"]):
        key = _fingerprint(page)
        if key in seen_documents:
            duplicates[page["id"]] = seen_documents[key]
        else:
            seen_documents[key] = page
    ledger = [_ledger_entry(page, ours, duplicates.get(page["id"])) for page in sorted(pages, key=lambda item: item["id"])]
    pages = [page for page in pages if page["id"] not in duplicates]
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    notes: list[str] = []

    def entry(month: str, meter: str, page: Mapping[str, Any] | None = None) -> dict[str, Any]:
        key = (month, meter)
        if key not in entries:
            site = store.site_of(dict(page) if page is not None else {"row": {"Custom No. 電號": meter}}) or {}
            entries[key] = {
                "month": month, "meter": meter, "site": site.get("name"), "site_code": site.get("code"),
                "grey_kwh": None, "bill_green_kwh": None, "grey_cost": None,
                "list": defaultdict(Decimal), "statement": defaultdict(Decimal), "green_cost": None,
            }
        return entries[key]

    def add(target: dict[str, Any], name: str, value: Decimal | None) -> None:
        if value is not None:
            target[name] = (target[name] or Decimal(0)) + value

    # Taipower bills and other documents: grey kWh and cost per month.
    for page in pages:
        if page.get("kind") == green.GREEN:
            continue
        row = page.get("row") or {}
        billed = _total(row, _BILLED_KWH)
        amounts = {
            "grey": billed if billed is not None else row.get(documents.USAGE),
            "green": green.transferred_kwh(row),
            "cost": row.get("稅前應繳總金額"),
        }
        shares = split_by_month(row.get("Billing Period 計費期間"), amounts)
        if not shares:
            notes.append(f"{page.get('filename')}: 計費期間無法解析，未列入")
            continue
        # Without a 電號 (a landlord's allocation), the document stands on its own.
        meter = meter_number(row.get("Custom No. 電號")) or f"#{page['id']}"
        for share in shares:
            target = entry(share.month, meter, page)
            add(target, "grey_kwh", _number(share.amounts["grey"]))
            add(target, "bill_green_kwh", _number(share.amounts["green"]))
            add(target, "grey_cost", _number(share.amounts["cost"]))

    # Green power documents.
    greens = [(page, page.get("green") or {}) for page in pages if page.get("kind") == green.GREEN]
    # A statement says which energy each meter got in a month.
    statement_energy: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, statement in greens:
        month = green._month(statement.get("period"))
        if month and green.type_of(statement) == green.STATEMENT:
            for line in statement.get("lines") or []:
                if line.get("meter") and is_green(line):
                    statement_energy[(month, line["meter"])].add(energy_name(line.get("energy")))

    def service_energy_of(month: str, service: Any, meters: Iterable[str]) -> str:
        known = service_energy.get(_service_key(service))
        if known:
            return known
        found = set().union(*(statement_energy.get((month, meter), set()) for meter in meters))
        return found.pop() if len(found) == 1 else UNCLASSIFIED

    unclassified: set[tuple[str, str]] = set()
    list_kwh: dict[tuple[str, Any], dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for page, statement in greens:
        month = green._month(statement.get("period"))
        if month is None:
            notes.append(f"{page.get('filename')}: 沒有期間，未列入")
            continue
        kind = green.type_of(statement)
        if kind == green.TRANSFER_LIST:
            rows = [row for row in statement.get("rows") or [] if row.get("user_meter") and (not ours or row["user_meter"] in ours)]
            energy = service_energy_of(month, statement.get("service"), {row["user_meter"] for row in rows})
            if energy == UNCLASSIFIED and rows:
                unclassified.add((month, statement.get("service") or ""))
            seen: set[tuple[str, Any]] = set()
            for row in rows:
                # Every fee table lists the same kWh: count each generator once.
                pair = (row["user_meter"], row.get("generator"))
                kwh = _number(row.get("kwh"))
                if pair in seen or kwh is None:
                    continue
                seen.add(pair)
                entry(month, row["user_meter"])["list"][energy] += kwh
                list_kwh[(month, statement.get("service"))][row["user_meter"]] += kwh
        elif kind == green.STATEMENT:
            for line in statement.get("lines") or []:
                meter = line.get("meter")
                if not meter or (ours and meter not in ours):
                    continue
                target = entry(month, meter)
                if is_green(line) and _number(line.get("kwh")) is not None:
                    target["statement"][energy_name(line.get("energy"))] += _number(line["kwh"])
                add(target, "green_cost", _number(line.get("cost")))

    # A notice's untaxed total, shared among its service's meters by kWh.
    for page, statement in greens:
        month = green._month(statement.get("period"))
        if month is None or green.type_of(statement) != green.NOTICE:
            continue
        cost = _number((statement.get("values") or {}).get("untaxed"))
        meters = list_kwh.get((month, statement.get("service")))
        if cost is None:
            continue
        if not meters or not sum(meters.values()):
            notes.append(f"{page.get('filename')}: 找不到同月同服務編號的台電轉供清單，綠電費用無法分到電號")
            add(entry(month, f"服務 {statement.get('service') or '?'}"), "green_cost", cost)
            continue
        whole = sum(meters.values())
        shares = {meter: (cost * kwh / whole).quantize(Decimal("0.01")) for meter, kwh in meters.items()}
        # Rounding leftovers go to the largest share so the parts add up.
        largest = max(shares, key=lambda meter: meters[meter])
        shares[largest] += cost - sum(shares.values())
        for meter, share in shares.items():
            add(entry(month, meter), "green_cost", share)

    rows = []
    for (month, meter), item in sorted(entries.items()):
        if item["list"]:
            by_energy, source = dict(item["list"]), "transfer_list"
        elif item["statement"]:
            by_energy, source = dict(item["statement"]), "statement"
        elif item["bill_green_kwh"]:
            by_energy, source = {UNCLASSIFIED: item["bill_green_kwh"]}, "bill"
        else:
            by_energy, source = {}, None
        green_kwh = sum(by_energy.values(), Decimal(0))
        total = item["grey_kwh"] + green_kwh if item["grey_kwh"] is not None else None
        rows.append({
            # A document without a 電號 (or a notice without Taipower's list) is named instead.
            "month": month, "meter": None if meter.startswith(("#", "服務")) else meter,
            "document": meter if meter.startswith("服務") else next(
                (page.get("filename") for page in pages if f"#{page.get('id')}" == meter), None),
            "site": item["site"], "site_code": item["site_code"],
            "grey_kwh": _plain(item["grey_kwh"]),
            "green_kwh": {energy: _plain(kwh) for energy, kwh in sorted(by_energy.items()) if kwh},
            "green_total": _plain(green_kwh),
            "total_kwh": _plain(total),
            "green_share": float(round(green_kwh / total, 4)) if total else None,
            "grey_cost": _plain(item["grey_cost"]),
            "green_cost": _plain(item["green_cost"]),
            "green_source": source,
        })
    return {
        "rows": rows,
        "documents": ledger,
        "duplicates": len(duplicates),
        "unclassified_services": sorted({service for _, service in unclassified}),
        "notes": notes,
        "sites_set": bool(ours),
    }


def _fingerprint(page: Mapping[str, Any]) -> Any:
    """What makes two items the same document: its type, period and contents."""
    if page.get("kind") == green.GREEN:
        statement = page.get("green") or {}
        kind = green.type_of(statement)
        if kind == green.STATEMENT:
            body = tuple(sorted((str(line.get("meter")), str(line.get("kwh")), str(line.get("cost"))) for line in statement.get("lines") or []))
        elif kind == green.TRANSFER_LIST:
            body = tuple(sorted((str(row.get("section")), str(row.get("user_meter")), str(row.get("generator")), str(row.get("kwh")), str(row.get("fee")))
                                for row in statement.get("rows") or []))
        else:
            values = statement.get("values") or {}
            body = (str(values.get("kwh")), str(values.get("untaxed")))
        return (kind, statement.get("period"), statement.get("service"), body)
    row = page.get("row") or {}
    return ("bill", meter_number(row.get("Custom No. 電號")) or page.get("upload_id"), row.get("Billing Period 計費期間"),
            row.get("稅前應繳總金額"), row.get("應繳總金額"), _total(row, _BILLED_KWH), row.get(documents.USAGE))


def _sum(values: Iterable[Any]) -> int | float | None:
    numbers = [_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return _plain(sum(numbers, Decimal(0))) if numbers else None


def _ledger_entry(page: Mapping[str, Any], ours: set[str], duplicate_of: Mapping[str, Any] | None) -> dict[str, Any]:
    """What one document adds: its kWh and cost as the dashboard counts them."""
    entry: dict[str, Any] = {"id": page["id"], "filename": page.get("filename"), "page": page.get("page_number"), "user": page.get("user"),
                             "status": page.get("status"),
                             "duplicate_of": None if duplicate_of is None else f"{duplicate_of.get('filename')} (#{duplicate_of['id']})"}
    if page.get("kind") == green.GREEN:
        statement = page.get("green") or {}
        kind = green.type_of(statement)
        entry.update(type=kind, period=statement.get("period"), service=statement.get("service"))
        if kind == green.STATEMENT:
            lines = [line for line in statement.get("lines") or [] if line.get("meter") and (not ours or line["meter"] in ours)]
            entry.update(kwh=_sum(line.get("kwh") for line in lines if is_green(line)), cost=_sum(line.get("cost") for line in lines),
                         surcharge=_sum(line.get("cost") for line in lines if not is_green(line)), counted="cost")
        elif kind == green.TRANSFER_LIST:
            rows = [row for row in statement.get("rows") or [] if row.get("user_meter") and (not ours or row["user_meter"] in ours)]
            pairs = {(row["user_meter"], row.get("generator")): row.get("kwh") for row in rows}
            entry.update(kwh=_sum(pairs.values()), cost=_sum(row.get("fee") for row in rows), surcharge=None, counted="kwh")
        else:
            values = statement.get("values") or {}
            entry.update(kwh=values.get("kwh"), cost=values.get("untaxed"), surcharge=values.get("surplus_charge"), counted="cost")
    else:
        row = page.get("row") or {}
        billed = _total(row, _BILLED_KWH)
        entry.update(type="bill", period=row.get("Billing Period 計費期間"), service=meter_number(row.get("Custom No. 電號")),
                     kwh=billed if billed is not None else row.get(documents.USAGE), cost=row.get("稅前應繳總金額"), surcharge=None, counted="both")
    return entry


def mix_rows(pages: Iterable[Mapping[str, Any]], store: Store, service_energy: Mapping[str, str]) -> list[list[Any]]:
    """The EnergyMix sheet."""
    sheet = []
    for row in energy_mix(pages, store, service_energy)["rows"]:
        kwh = row["green_kwh"]
        other = sum(value for energy, value in kwh.items() if energy not in (SOLAR, WIND, UNCLASSIFIED))
        sheet.append([
            row["month"], row["site"], row["site_code"], row["meter"] or row["document"], row["grey_kwh"], kwh.get(SOLAR), kwh.get(WIND),
            other or None, kwh.get(UNCLASSIFIED), row["green_total"], row["total_kwh"], row["green_share"],
            row["grey_cost"], row["green_cost"], SOURCE_NAMES.get(row["green_source"] or ""),
        ])
    return sheet

