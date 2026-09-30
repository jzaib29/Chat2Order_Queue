"""Authoritative checks; model suggestions never establish stock or consent."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .data import local_now, stable_hash
from .models import Business, Details, QueueOrder


def detail_issues(details: Details, business: Business, now: datetime) -> list[str]:
    issues = []
    product = next((p for p in business.products if p.id == details.product_id), None)
    if not product:
        issues.append("Choose a product from the menu.")
    if not details.quantity:
        issues.append("Confirm the quantity.")
    if not details.pickup_date:
        issues.append("Confirm the pickup date.")
    if not details.pickup_time:
        issues.append("Confirm the pickup time.")
    if details.pickup_date and details.pickup_time:
        pickup = datetime.fromisoformat(f"{details.pickup_date}T{details.pickup_time}").replace(tzinfo=ZoneInfo(business.timezone))
        if pickup < local_now(business, now) + timedelta(hours=business.lead_hours):
            issues.append(f"Pickup must allow at least {business.lead_hours} hours from now.")
        if not business.pickup_start <= details.pickup_time <= business.pickup_end:
            issues.append(f"Choose a pickup time between {business.pickup_start} and {business.pickup_end}.")
    return issues


def available_capacity(product_id: str, pickup_date: str, orders: list[QueueOrder], business: Business, exclude_id: str | None = None) -> int:
    product = next((p for p in business.products if p.id == product_id), None)
    if not product:
        return 0
    used = sum(o.reservation.quantity or 0 for o in orders if o.id != exclude_id and o.reservation and o.reservation.product_id == product_id and o.reservation.pickup_date == pickup_date)
    return max(0, product.daily_capacity - used)


def acceptance_issues(details: Details, business: Business, orders: list[QueueOrder], order_id: str, now: datetime) -> list[str]:
    issues = detail_issues(details, business, now)
    if details.product_id and details.pickup_date and details.quantity:
        available = available_capacity(details.product_id, details.pickup_date, orders, business, order_id)
        if details.quantity > available:
            issues.append(f"Only {available} units are available for this product on {details.pickup_date}. Offer a smaller quantity, a different product, or another date.")
    return issues


def alternatives_for(order: QueueOrder, business: Business, orders: list[QueueOrder], now: datetime) -> list[dict]:
    details = order.details
    if not details.quantity:
        return []
    today = local_now(business, now).date()
    earliest = local_now(business, now) + timedelta(hours=business.lead_hours, minutes=1)
    preferred = datetime.fromisoformat(details.pickup_date).date() if details.pickup_date else earliest.date()
    first = max(preferred, earliest.date(), today)
    options = []
    for offset in range(4):
        day = first + timedelta(days=offset)
        pickup_time = details.pickup_time if details.pickup_time and business.pickup_start <= details.pickup_time <= business.pickup_end else business.pickup_start
        candidate_time = datetime.fromisoformat(f"{day.isoformat()}T{pickup_time}").replace(tzinfo=ZoneInfo(business.timezone))
        if candidate_time < earliest:
            continue
        for p in business.products:
            available = available_capacity(p.id, day.isoformat(), orders, business, order.id)
            qty = min(details.quantity, available)
            if qty <= 0:
                continue
            candidate = Details(product_id=p.id, quantity=qty, pickup_date=day.isoformat(), pickup_time=pickup_time)
            if detail_issues(candidate, business, now) or candidate == details:
                continue
            options.append({"id": "ALT-" + stable_hash(candidate.model_dump())[:10], "details": candidate.model_dump(), "product_name": p.name, "unit_price": p.price, "total": p.price * qty, "available": available})
    return options[:12]
