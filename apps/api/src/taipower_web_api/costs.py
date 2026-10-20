"""What a kWh of green power costs, item by item, and whether each item is right.

A contract fixes the energy price; the rest is charged as it falls due, so
the price per kWh moves from month to month. Each item has its own check:

* the energy price (a notice's A, a statement's 基準價) must be the contract's;
* Taipower's transfer fees passed on (a notice's E, a statement's kWh ×
  轉供費率) are held against what Taipower's list charged for the same
  meters;
* the surplus (餘電) charge: a notice's (A−C)×D per kWh should be the same
  for every company of the service (it is shared by kWh), and a statement's
  餘電加收 lines are shown;
* certificates and fees are shown as they are.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable, Mapping

from taipower_bill_ocr.green_statement import is_green

from . import green
from .budget import contract_for
from .mix import SOLAR, WIND, energy_name
from .store import Store

# A surplus charge per kWh this far from the other companies' is pointed out.
SURPLUS_TOLERANCE = Decimal("0.0005")


def _d(value: Any) -> Decimal | None:
    return Decimal(str(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _plain(value: Decimal | None, places: int = 2) -> float | int | None:
    if value is None:
        return None
    value = round(value, places)
    return int(value) if value == value.to_integral_value() else float(value)


def _item(key: str, label: str, amount: Decimal | None, kwh: Decimal, **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "amount": _plain(amount),
            "per_kwh": _plain(amount / kwh, 4) if amount is not None and kwh else None, **extra}


def _list_fees(lists: list[Mapping[str, Any]], meters: set[str]) -> dict[Any, dict[str, Any]]:
    """Per service: Taipower's kWh (each meter and generator once) and fees for these meters."""
    found: dict[Any, dict[str, Any]] = {}
    for page in lists:
        statement = page.get("green") or {}
        entry = found.setdefault(statement.get("service"), {"kwh": Decimal(0), "fees": Decimal(0), "sections": set(), "pairs": set()})
        for row in statement.get("rows") or []:
            if row.get("user_meter") not in meters:
                continue
            if row.get("section"):
                entry["sections"].add(row["section"])
            pair = (row["user_meter"], row.get("generator"))
            if pair not in entry["pairs"] and _d(row.get("kwh")) is not None:
                entry["pairs"].add(pair)
                entry["kwh"] += _d(row["kwh"])
            entry["fees"] += _d(row.get("fee")) or Decimal(0)
    return {service: entry for service, entry in found.items() if entry["kwh"]}


def breakdowns(store: Store) -> list[dict[str, Any]]:
    """Every retailer's document (statement or notice), its cost per kWh item by item."""
    documents = green._documents(store)
    contracts = store.contracts()
    meter_entity = store.meter_entities()
    ours = green.company_meters(store)
    lists_by_month: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    notices_by_service: dict[tuple[str, Any], list[Mapping[str, Any]]] = defaultdict(list)
    for page in documents:
        statement = page.get("green") or {}
        month = green._month(statement.get("period"))
        if month is None:
            continue
        if green.type_of(statement) == green.TRANSFER_LIST:
            lists_by_month[month].append(page)
        elif green.type_of(statement) == green.NOTICE:
            notices_by_service[(month, statement.get("service"))].append(page)

    result = []
    for page in documents:
        statement = page.get("green") or {}
        month = green._month(statement.get("period"))
        kind = green.type_of(statement)
        if month is None or kind == green.TRANSFER_LIST:
            continue
        owner = store.entity_of(dict(page))
        if kind == green.NOTICE:
            entry = _notice(page, statement, month, owner, contracts, meter_entity, ours, lists_by_month[month],
                            notices_by_service[(month, statement.get("service"))], store)
        else:
            entry = _statement(page, statement, month, owner, contracts, meter_entity, ours, lists_by_month[month])
        if entry is not None:
            result.append(entry)
    return sorted(result, key=lambda entry: (entry["month"], entry["energy"] or "", entry["filename"] or ""))


def _contract_check(contracts: list, entity_id: Any, energy: str, month: str, prices: set[Decimal]) -> dict[str, Any]:
    contract = contract_for(contracts, entity_id, energy, month)
    if contract is None:
        return {"status": "none", "reference": None, "note": "No contract in Settings → Green power contracts"}
    agreed = Decimal(str(contract["price"]))
    status = "ok" if prices == {agreed} else "check"
    return {"status": status, "reference": _plain(agreed, 4),
            "note": None if status == "ok" else f"contract {agreed}, charged {'、'.join(str(price) for price in sorted(prices))}"}


def _notice(page, statement, month, owner, contracts, meter_entity, ours, lists, peers, store) -> dict[str, Any] | None:
    values = statement.get("values") or {}
    bought = _d(values.get("kwh"))
    if not bought:
        return None
    price = _d(values.get("purchase_rate"))
    energy = SOLAR
    supply, certificate, surplus, untaxed = (_d(values.get(name)) for name in ("supply_fees", "certificate_fee", "surplus_charge", "untaxed"))
    contract = _contract_check(contracts, owner["id"] if owner else None, energy, month, {price} if price is not None else set())

    # Taipower's fees for the company's meters on the same service's list.
    meters = {meter for meter, entity in meter_entity.items() if owner and entity["id"] == owner["id"]} if owner else set(ours)
    listed = _list_fees([doc for doc in lists if (doc.get("green") or {}).get("service") == statement.get("service")], meters).get(statement.get("service"))
    taipower: dict[str, Any] = {"status": "none", "reference": None, "note": "No Taipower list for this service and month"}
    if listed is not None and supply is not None:
        difference = supply - listed["fees"]
        taipower = {"status": "ok" if abs(difference) <= 1 else "check", "reference": _plain(listed["fees"]),
                    "reference_per_kwh": _plain(listed["fees"] / listed["kwh"], 4) if listed["kwh"] else None,
                    "difference": _plain(difference), "sections": sorted(listed["sections"]),
                    "note": None if abs(difference) <= 1 else f"E {_plain(supply)} vs Taipower's fees {_plain(listed['fees'])} ({'、'.join(sorted(listed['sections']))})"}

    # The surplus per kWh against the other companies' notices for the service.
    shares = []
    for other in peers:
        other_values = (other.get("green") or {}).get("values") or {}
        other_kwh, other_surplus = _d(other_values.get("kwh")), _d(other_values.get("surplus_charge"))
        if other_kwh and other_surplus is not None:
            other_owner = store.entity_of(dict(other))
            shares.append({"entity": other_owner.get("name") if other_owner else other.get("filename"),
                           "per_kwh": _plain(other_surplus / other_kwh, 4), "self": other["id"] == page["id"],
                           "_value": other_surplus / other_kwh})
    surplus_check: dict[str, Any] = {"status": "none", "peers": [], "note": None}
    if surplus is not None and len(shares) > 1:
        values_sorted = sorted(share["_value"] for share in shares)
        middle = values_sorted[len(values_sorted) // 2]
        mine = surplus / bought
        consistent = abs(mine - middle) <= SURPLUS_TOLERANCE
        surplus_check = {"status": "ok" if consistent else "check", "note": None if consistent else "shared differently from the other companies"}
    surplus_check["peers"] = [{key: value for key, value in share.items() if key != "_value"} for share in shares]

    items = [
        _item("contract", "合約電價 A", _d(values.get("purchase_charge")) or (price * bought if price is not None else None), bought, check=contract),
        _item("taipower", "台電轉供費用 E", supply, bought, check=taipower),
        _item("certificate", "憑證審查費 F", certificate, bought),
        _item("surplus", "餘電電費 (A−C)×D", surplus, bought, check=surplus_check),
    ]
    return {"id": page["id"], "filename": page.get("filename"), "type": green.NOTICE, "month": month, "energy": energy,
            "entity": owner.get("name") if owner else None, "service": statement.get("service"),
            "kwh": _plain(bought), "items": items, "total": _item("total", "合計（未稅）", untaxed, bought),
            "ours": owner is not None or len(peers) == 1}


def _statement(page, statement, month, owner, contracts, meter_entity, ours, lists) -> dict[str, Any] | None:
    lines = [line for line in statement.get("lines") or [] if line.get("meter") and (not ours or line["meter"] in ours)]
    greens = [line for line in lines if is_green(line) and _d(line.get("kwh")) is not None]
    kwh = sum((_d(line["kwh"]) for line in greens), Decimal(0))
    if not kwh:
        return None
    energy = energy_name(greens[0].get("energy")) if greens else WIND
    charge = lambda rate: sum((_d(line["kwh"]) * (_d(line.get(rate)) or Decimal(0)) for line in greens), Decimal(0))  # noqa: E731
    energy_cost, transfer, certificate = charge("base"), charge("transfer_rate"), charge("certificate_rate")
    green_cost = sum((_d(line.get("cost")) or Decimal(0) for line in greens), Decimal(0))
    surplus = sum((_d(line.get("cost")) or Decimal(0) for line in lines if not is_green(line)), Decimal(0))
    fee = _d((statement.get("charges") or {}).get("fee")) or Decimal(0)
    entity = owner or next((meter_entity[line["meter"]] for line in greens if line["meter"] in meter_entity), None)
    prices = {_d(line.get("base")) for line in greens if _d(line.get("base")) is not None}
    contract = _contract_check(contracts, entity["id"] if entity else None, energy, month, prices)

    # Taipower's fees for these meters, on the service whose kWh is nearest.
    meters = {line["meter"] for line in greens}
    by_service = _list_fees(lists, meters)
    taipower: dict[str, Any] = {"status": "none", "reference": None, "note": "No Taipower list with these meters this month"}
    if by_service:
        service, listed = min(by_service.items(), key=lambda item: abs(item[1]["kwh"] - kwh))
        difference = transfer - listed["fees"]
        # The retailer's rate is fixed by contract; Taipower's fees follow its own rates.
        taipower = {"status": "ok" if difference <= 1 else "over", "reference": _plain(listed["fees"]),
                    "reference_per_kwh": _plain(listed["fees"] / listed["kwh"], 4), "difference": _plain(difference),
                    "service": service, "sections": sorted(listed["sections"]),
                    "note": None if difference <= 1 else
                    f"charged {_plain(transfer)} at the retailer's rate; Taipower's fees were {_plain(listed['fees'])} ({'、'.join(sorted(listed['sections']))})"}
    rounding = green_cost - energy_cost - transfer - certificate
    items = [
        _item("contract", "合約電價（基準價）", energy_cost, kwh, check=contract),
        _item("taipower", "電能轉供費", transfer, kwh, check=taipower),
        _item("certificate", "憑證費", certificate, kwh),
        _item("surplus", "餘電加收", surplus, kwh, check={"status": "none", "peers": [], "note": None}),
        _item("fee", "交易手續費", fee, kwh),
    ]
    if abs(rounding) >= Decimal("0.5"):
        items.append(_item("rounding", "逐項進位差", rounding, kwh))
    return {"id": page["id"], "filename": page.get("filename"), "type": green.STATEMENT, "month": month, "energy": energy,
            "entity": entity.get("name") if entity else None, "service": taipower.get("service"),
            "kwh": _plain(kwh), "items": items, "total": _item("total", "合計（未稅）", green_cost + surplus + fee, kwh), "ours": True}


def for_page(store: Store, page_id: int) -> dict[str, Any] | None:
    return next((entry for entry in breakdowns(store) if entry["id"] == page_id), None)


__all__: Iterable[str] = ["breakdowns", "for_page"]
