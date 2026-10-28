"""In-memory Ledgerly account used by the example server.

Writes change this process's copy only, but they behave like the real ones:
mail goes to the outbox, payments to the ledger.
"""

from __future__ import annotations

import json
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA = json.loads((Path(__file__).parent / "seed.json").read_text(encoding="utf-8"))
GEOCODER = "https://nominatim.openstreetmap.org/search"
SESSION_TOKEN = "sess_" + secrets.token_hex(12)

OUTBOX: list[dict[str, str]] = []
PAYMENTS: list[dict[str, Any]] = []


def invoices(status: str | None = None) -> list[dict[str, Any]]:
    rows = DATA["invoices"]
    return [row for row in rows if status is None or row["status"] == status]


def invoice(invoice_id: str) -> dict[str, Any] | None:
    return next((row for row in DATA["invoices"] if row["id"] == invoice_id), None)


def customer(customer_id: str) -> dict[str, Any] | None:
    return next((row for row in DATA["customers"] if row["id"] == customer_id), None)


def send_email(to: str, subject: str, body: str) -> None:
    OUTBOX.append({"to": to, "subject": subject, "body": body})


def mark_reminded(invoice_id: str) -> None:
    row = invoice(invoice_id)
    if row is not None:
        row["reminders_sent"] = row.get("reminders_sent", 0) + 1


def create_customer(name: str, email: str) -> dict[str, Any]:
    row = {"id": f"cus_{len(DATA['customers']) + 1:03d}", "name": name, "email": email}
    DATA["customers"].append(row)
    return row


def update_customer(customer_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    row = customer(customer_id)
    if row is not None:
        row.update(fields)
    return row


def delete_customer(customer_id: str) -> bool:
    before = len(DATA["customers"])
    DATA["customers"] = [row for row in DATA["customers"] if row["id"] != customer_id]
    return len(DATA["customers"]) < before


def transfer(to_account: str, amount: float, currency: str) -> dict[str, Any]:
    payment = {
        "id": f"pay_{len(PAYMENTS) + 1:04d}",
        "to_account": to_account,
        "amount": amount,
        "currency": currency,
        "status": "sent",
    }
    PAYMENTS.append(payment)
    return payment


def geocode(query: str) -> list[dict[str, Any]]:
    url = GEOCODER + "?" + urllib.parse.urlencode({"q": query, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": "ledgerly-mcp"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            places = json.loads(response.read())
    except OSError:
        places = DATA["offline_places"]
    return [
        {
            "name": place.get("display_name"),
            "lat": place.get("lat"),
            "lon": place.get("lon"),
        }
        for place in places[:5]
    ]
