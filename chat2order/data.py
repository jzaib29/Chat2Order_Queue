"""Small pure helpers. No network requests or credentials in exports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Business, Details, QueueOrder, STATUS_LABELS


def default_business() -> Business:
    path = Path(__file__).resolve().parents[1] / "demo_data/business.json"
    return Business.model_validate_json(path.read_text())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("Use a timezone-aware clock.")
    return now.astimezone(timezone.utc).isoformat()


def local_now(business: Business, now: datetime | None = None) -> datetime:
    return (now or utc_now()).astimezone(ZoneInfo(business.timezone))


def customer_key(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def describe(details: Details, business: Business) -> str:
    product = next((p for p in business.products if p.id == details.product_id), None)
    return f"{details.quantity or '?'} × {product.name if product else 'Product needed'} · {details.pickup_date or 'Date needed'} · {details.pickup_time or 'Time needed'}"


def order_total(order: QueueOrder, business: Business) -> int | None:
    if order.unit_price is not None:
        confirmed = order.reservation or order.details
        return order.unit_price * (confirmed.quantity or 0)
    p = next((p for p in business.products if p.id == order.details.product_id), None)
    return p.price * order.details.quantity if p and order.details.quantity else None


def csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            text = str(value if value is not None else "")
            safe[key] = "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
        writer.writerow(safe)
    return out.getvalue().encode("utf-8-sig")


def order_rows(orders: list[QueueOrder], business: Business) -> list[dict]:
    products = {p.id: p for p in business.products}
    return [{"Order ID": o.id, "Customer": o.customer, "Status": STATUS_LABELS[o.status], "Product": products[o.details.product_id].name if o.details.product_id in products else "", "Quantity": o.details.quantity, "Pickup date": o.details.pickup_date, "Pickup time": o.details.pickup_time, "Currency": o.currency, "Confirmed / estimated total": order_total(o, business), "Created": o.created_at, "Updated": o.updated_at, "Pickup accepted": o.pickup_accepted_at or "", "Fulfilled": o.fulfilled_at or "", "Original request": o.original_text} for o in orders]
