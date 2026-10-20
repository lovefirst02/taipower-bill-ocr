"""A retailer's purchase notice for green power (企業購電電費通知單).

A form rather than a table: the purchase rate (A), the purchased kWh (B),
Taipower's feed-in rate (C), the surplus kWh (D), the power supply fees (E,
Taipower's transfer fees passed on), the certificate review fee (F), tax and
total, with Chinese and English labels. Values are found to the right of
their label; the notice's own formula is then checked:

    untaxed = A×B + (A−C)×D + E + F,  tax = untaxed × 5%,  total = untaxed + tax

No OCR happens here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable

from . import tables
from .keyword_extract import Line, parse_period

# Each value and its labels (Chinese, English), most specific wins.
FIELDS: dict[str, tuple[str, ...]] = {
    "purchase_rate": ("購售費率", "Purchase Rate"),
    "kwh": ("實際購售電量(度)", "Actual Purchase Volume (kWh)", "Actual Purchase Volume(kWh)"),
    "purchase_charge": ("實際購售電量電費", "Volume Charges"),
    "fit_rate": ("躉購費率", "FiT Rate"),
    "surplus_kwh": ("餘電電量", "餘電量", "Surplus Power Volume"),
    "surplus_charge": ("餘電電費", "Surplus Power Fees"),
    "supply_fees": ("電能供應費用", "Power Supply Fees"),
    "certificate_fee": ("憑證審查費", "T-REC Review Fee"),
    "certificates": ("憑證張數", "T-REC (PCS)", "T-REC(PCS)"),
    "untaxed": ("未稅金額合計", "Exclusive of Tax", "Exculsive of Tax"),
    "tax": ("應付稅金", "付稅金", "Tax"),
    "total": ("本期應付總額", "應付總額", "Installment Price (NT)", "Installment Price(NT)"),
}
WHOLE = ("kwh", "purchase_charge", "surplus_kwh", "surplus_charge", "supply_fees", "certificate_fee", "certificates", "untaxed", "tax", "total")
TAX_RATE = Decimal("0.05")
_SERVICE = re.compile(r"服務編號\s*[:：]?\s*([0-9][0-9\-\s]{6,})")


@dataclass
class PurchaseNotice:
    period: str | None
    service: str | None
    values: dict[str, Any] = field(default_factory=dict)
    # Where each value is on the page.
    boxes: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    page: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {"period": self.period, "service": self.service, "values": dict(self.values),
                "boxes": {name: list(box) for name, box in self.boxes.items()}, "page": self.page}


def read_notice(lines: Iterable[Line]) -> PurchaseNotice | None:
    """The notice's values, or None when the page is not a purchase notice."""
    lines = [line for line in lines if line.text.strip()]
    labels: dict[str, list[Line]] = {}
    for line in lines:
        name = tables.match(line.text, FIELDS)
        if name:
            labels.setdefault(name, []).append(line)
    if not {"kwh", "supply_fees"} <= labels.keys() or len(labels) < 5:
        return None
    label_lines = {id(line) for found in labels.values() for line in found}
    numbers = [
        line for line in lines
        if id(line) not in label_lines and tables.number(line.text) is not None and not re.search(r"[A-Za-z]", line.text)
    ]
    notice = PurchaseNotice(period=None, service=None, page=next(iter(labels.values()))[0].page)
    for name, found in labels.items():
        best: tuple[float, Line] | None = None
        for label in found:
            # The value sits right of its label, about level with it.
            for line in numbers:
                if line.page != label.page or line.x <= label.x + label.width * 0.3:
                    continue
                if abs(line.middle - label.middle) > max(label.height, line.height) * 2.2:
                    continue
                distance = line.x - label.x + abs(line.middle - label.middle) * 3
                if best is None or distance < best[0]:
                    best = (distance, line)
        if best is not None:
            value = tables.number(best[1].text, integer=name in WHOLE)
            if value is not None:
                notice.values[name] = value
                notice.boxes[name] = best[1].box
    for line in lines:
        if notice.period is None and (period := parse_period(line.text)) is not None:
            notice.period = period
        if notice.service is None and (match := _SERVICE.search(tables.normal(line.text))) is not None:
            notice.service = re.sub(r"\s", "", match.group(1))
    return notice if "kwh" in notice.values else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def check_notice(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Each line of the notice's formula: what it should be and what it says."""
    get = {name: _decimal(values.get(name)) for name in FIELDS}
    round_ = lambda value: value.quantize(Decimal(1), rounding=ROUND_HALF_UP)  # noqa: E731
    checks = []

    def add(name: str, label: str, expected: Decimal | None) -> None:
        printed = get.get(name)
        if expected is None or printed is None:
            checks.append({"field": name, "label": label, "expected": None, "printed": values.get(name), "difference": None, "status": "incomplete"})
            return
        difference = printed - expected
        checks.append({"field": name, "label": label, "expected": int(expected), "printed": values.get(name),
                       "difference": int(difference), "status": "ok" if difference == 0 else "check"})

    a, b, c, d, e, f = (get[name] for name in ("purchase_rate", "kwh", "fit_rate", "surplus_kwh", "supply_fees", "certificate_fee"))
    purchase = round_(a * b) if a is not None and b is not None else None
    surplus = round_((a - c) * d) if a is not None and c is not None and d is not None else None
    add("purchase_charge", "A×B", purchase)
    add("surplus_charge", "(A−C)×D", surplus)
    parts = [get["purchase_charge"], get["surplus_charge"], e, f]
    untaxed = sum(parts, Decimal(0)) if all(part is not None for part in parts) else None
    add("untaxed", "A×B + (A−C)×D + E + F", untaxed)
    tax = round_(get["untaxed"] * TAX_RATE) if get["untaxed"] is not None else None
    add("tax", "untaxed × 5%", tax)
    total = get["untaxed"] + get["tax"] if get["untaxed"] is not None and get["tax"] is not None else None
    add("total", "untaxed + tax", total)
    return checks
