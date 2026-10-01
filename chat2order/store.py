"""Shared SQLite queue. Short transactions protect stock, versions, and history."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .data import customer_key, default_business, describe, stamp, utc_now
from .models import AuditEvent, Backup, Business, ChatMessage, Details, InterpreterResult, QueueOrder, TERMINAL
from .rules import acceptance_issues, detail_issues


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (order_id TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0);
            """)
            con.execute("INSERT OR IGNORE INTO meta VALUES ('business', ?)", (default_business().model_dump_json(),))
            con.execute("INSERT OR IGNORE INTO meta VALUES ('revision', '0')")
            con.commit()

    @contextmanager
    def connection(self, write=False):
        con = sqlite3.connect(self.path, timeout=10)
        try:
            con.execute("PRAGMA busy_timeout=10000")
            if write:
                con.execute("BEGIN IMMEDIATE")
            yield con
            if write:
                con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _business(con) -> Business:
        return Business.model_validate_json(con.execute("SELECT value FROM meta WHERE key='business'").fetchone()[0])

    @staticmethod
    def _orders(con) -> list[QueueOrder]:
        orders = [QueueOrder.model_validate_json(row[0]) for row in con.execute("SELECT payload FROM orders")]
        return sorted(orders, key=lambda o: (o.created_at, o.id))

    @staticmethod
    def _bump(con):
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def snapshot(self, include_events=True) -> dict:
        with self.connection() as con:
            con.execute("BEGIN")
            orders = self._orders(con)
            usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
            values = con.execute("SELECT COALESCE(SUM(requests),0), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM usage").fetchone()
            usage.update(zip(usage, values))
            return {"business": self._business(con), "orders": orders, "events": [AuditEvent.model_validate_json(r[0]) for r in con.execute("SELECT payload FROM events ORDER BY seq")] if include_events else [], "usage": usage, "revision": int(con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])}

    def get(self, order_id: str) -> QueueOrder | None:
        with self.connection() as con:
            row = con.execute("SELECT payload FROM orders WHERE id=?", (order_id,)).fetchone()
            return QueueOrder.model_validate_json(row[0]) if row else None

    def _persist(self, con, order: QueueOrder, actor: str, action: str, note: str, now: datetime):
        order.updated_at = stamp(now)
        QueueOrder.model_validate(order.model_dump())
        con.execute("INSERT INTO orders VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (order.id, order.model_dump_json()))
        event = AuditEvent(id=str(uuid4()), order_id=order.id, at=stamp(now), actor=actor, action=action, status=order.status, note=note[:5000], details={"order": order.details.model_dump(), "proposal": order.proposal.model_dump() if order.proposal else None, "version": order.version})
        con.execute("INSERT INTO events (id,payload) VALUES (?,?)", (event.id, event.model_dump_json()))
        self._bump(con)

    def _change(self, order_id, expected_version, actor, action, mutate, now=None) -> QueueOrder:
        now = now or utc_now()
        with self.connection(write=True) as con:
            business, orders = self._business(con), self._orders(con)
            order = next((o for o in orders if o.id == order_id), None)
            if order is None:
                raise Conflict("This order is no longer available. Refresh the board.")
            if order.version != expected_version:
                raise Conflict("This order changed while you were reviewing it. Review its latest version and try again.")
            note = mutate(order, business, orders, now)
            order.version += 1
            self._persist(con, order, actor, action, note, now)
            return order

    @staticmethod
    def _question(details, business, now, question=""):
        issues = detail_issues(details, business, now)
        parts = ([question.strip()] if question.strip() else []) + issues
        return " ".join(dict.fromkeys(parts))[:1200]

    def create(self, *, order_id: str, customer: str, text: str, details: Details, question="", now=None, actor="customer") -> QueueOrder:
        now = now or utc_now()
        customer = " ".join(customer.split())
        if not customer or len(customer) > 80 or not text.strip():
            raise ValueError("Enter a customer name and an order request.")
        with self.connection(write=True) as con:
            existing = con.execute("SELECT payload FROM orders WHERE id=?", (order_id,)).fetchone()
            if existing:
                return QueueOrder.model_validate_json(existing[0])
            business = self._business(con)
            q = self._question(details, business, now, question)
            order = QueueOrder(id=order_id, customer=customer, customer_key=customer_key(customer), original_text=text.strip(), details=details, question=q, status="needs_clarification" if q else "placed", messages=[ChatMessage(text=text.strip(), at=stamp(now))], currency=business.currency, created_at=stamp(now), updated_at=stamp(now))
            self._persist(con, order, actor, "submitted", q or "Order interpreted and added to the business queue.", now)
            return order

    def apply_text(self, *, order_id, customer, text, result: InterpreterResult, expected_version=None, now=None) -> QueueOrder:
        now = now or utc_now()
        patch = {k: getattr(result, k) for k in Details.model_fields if getattr(result, k) is not None}
        question = result.question or "" if result.intent in {"clarify", "unsupported", "ignore"} else ""
        if result.intent in {"unsupported", "ignore"} and not question:
            question = "Please confirm a single menu product, quantity, pickup date, and pickup time."
        if expected_version is None:
            if result.intent == "cancel":
                raise ValueError("Choose an existing order to request a cancellation.")
            return self.create(order_id=order_id, customer=customer, text=text, details=Details(**patch), question=question, now=now)

        def mutate(o, b, orders, clock):
            if o.customer_key != customer_key(customer):
                raise ValueError("Choose the customer who placed this order.")
            if o.status in TERMINAL or o.status == "ready_for_pickup":
                raise ValueError("This order can no longer be edited here.")
            o.messages.append(ChatMessage(text=text.strip(), at=stamp(clock)))
            if result.intent == "ignore":
                return "Customer message acknowledged; order details unchanged."
            if result.intent == "cancel":
                if o.reservation:
                    o.previous_status = "accepted"
                    o.status, o.proposed_by, o.proposal = "modified", "customer", None
                    o.cancellation_requested = True
                    o.proposal_note = "Customer requests cancellation. Existing reservation stays until owner approval."
                else:
                    o.status = "cancelled"
                    self._clear_proposal(o)
                return "Customer requested cancellation."
            base = o.proposal if o.status == "modified" and o.proposed_by == "customer" and o.proposal else o.details
            revised = Details(**(base.model_dump() | patch))
            q = self._question(revised, b, clock, question)
            if o.reservation:
                o.previous_status = "accepted"
                o.status, o.proposed_by, o.proposal = "modified", "customer", revised
                o.proposal_note = (q or result.summary or "Customer requested revised details.")[:1200]
                o.cancellation_requested = False
                o.question = q
            else:
                o.details, o.question = revised, q
                o.status = "needs_clarification" if q else "placed"
                self._clear_proposal(o)
            return "Customer update: " + describe(revised, b) + (" · " + q if q else "")
        return self._change(order_id, expected_version, "customer", "text_update", mutate, now)

    def update_details(self, order_id, version, customer, details: Details, now=None):
        def mutate(o, b, orders, clock):
            if o.customer_key != customer_key(customer):
                raise ValueError("Choose the customer who placed this order.")
            if o.status in TERMINAL or o.status == "ready_for_pickup":
                raise ValueError("This order can no longer be edited. Ready orders can only be picked up.")
            replaced_proposal = o.status == "modified"
            question = self._question(details, b, clock)
            if o.reservation:
                # Preserve the confirmed details and stock until owner approval.
                o.previous_status = "accepted"
                o.status, o.proposed_by = "modified", "customer"
                o.proposal = details.model_copy(deep=True)
                o.proposal_note = question or "Customer confirmed revised pickup details. Business approval is required."
                o.cancellation_requested, o.question = False, question
            else:
                o.details, o.question = details.model_copy(deep=True), question
                o.status = "needs_clarification" if question else "placed"
                self._clear_proposal(o)
            return "Customer confirmed standard pickup details: " + describe(details, b) + (" · Replaces the pending proposal." if replaced_proposal else "")
        return self._change(order_id, version, "customer", "details_completed", mutate, now)

    @staticmethod
    def _clear_proposal(o):
        o.proposal = None
        o.proposed_by = None
        o.proposal_note = ""
        o.previous_status = None
        o.cancellation_requested = False

    @staticmethod
    def _accept(o, details, business, orders, clock):
        issues = acceptance_issues(details, business, orders, o.id, clock)
        if issues:
            raise ValueError(" ".join(issues))
        product = next(p for p in business.products if p.id == details.product_id)
        o.details = details.model_copy(deep=True)
        o.reservation = details.model_copy(deep=True)
        o.unit_price, o.currency = product.price, business.currency
        o.status, o.question = "accepted", ""
        o.accepted_at = stamp(clock)
        Store._clear_proposal(o)

    def business_action(self, order_id, version, action, *, details=None, reason="", now=None):
        reason = reason.strip()[:1200]
        def mutate(o, b, orders, clock):
            if o.status in TERMINAL:
                raise ValueError("This order is already closed.")
            if action == "accept":
                if o.status != "placed" or o.question:
                    raise ValueError("Complete the clarification before accepting this order.")
                self._accept(o, o.details, b, orders, clock)
                return "Business accepted: " + describe(o.details, b)
            if action == "reject":
                if o.status == "ready_for_pickup":
                    raise ValueError("Use Clear active boards to cancel an order already marked ready.")
                if not reason:
                    raise ValueError("Enter a rejection reason for the customer.")
                o.status, o.reservation, o.question = "rejected", None, ""
                self._clear_proposal(o)
                return reason
            if action == "propose":
                if o.status == "ready_for_pickup" or details is None:
                    raise ValueError("This order cannot receive a proposal.")
                issues = acceptance_issues(details, b, orders, o.id, clock)
                if issues:
                    raise ValueError(" ".join(issues))
                if o.status != "modified":
                    o.previous_status = o.status
                o.proposal = details
                o.proposed_by, o.status = "business", "modified"
                o.proposal_note = reason or "Please review the proposed product, quantity, and pickup details."
                o.cancellation_requested = False
                return "Business proposed: " + describe(details, b) + " · " + o.proposal_note
            if action in {"approve_change", "decline_change"}:
                if o.status != "modified" or o.proposed_by != "customer":
                    raise ValueError("There is no customer change awaiting owner review.")
                if action == "decline_change":
                    o.status, o.question = "accepted", ""
                    self._clear_proposal(o)
                    return reason or "Business declined the requested change; the accepted order remains."
                if o.cancellation_requested:
                    o.status, o.reservation, o.question = "cancelled", None, ""
                    self._clear_proposal(o)
                    return "Business approved the customer's cancellation."
                if o.proposal is None or o.question:
                    raise ValueError("The customer must clarify the proposed change first.")
                self._accept(o, o.proposal, b, orders, clock)
                return "Business approved the customer's change: " + describe(o.details, b)
            if action == "ready":
                if o.status != "accepted":
                    raise ValueError("Accept the order before marking it ready.")
                o.status, o.ready_at = "ready_for_pickup", stamp(clock)
                return "Your order is ready for pickup. Accept pickup in the User Interface to start completion."
            raise ValueError("Unknown business action.")
        return self._change(order_id, version, "business", action, mutate, now)

    def customer_action(self, order_id, version, customer, action, now=None):
        def mutate(o, b, orders, clock):
            if o.customer_key != customer_key(customer):
                raise ValueError("Choose the customer who placed this order.")
            if action in {"accept_proposal", "decline_proposal"}:
                if o.status != "modified" or o.proposed_by != "business" or o.proposal is None:
                    raise ValueError("There is no business proposal awaiting your response.")
                if action == "accept_proposal":
                    self._accept(o, o.proposal, b, orders, clock)
                    return "Customer accepted the business proposal: " + describe(o.details, b)
                o.status = o.previous_status or ("accepted" if o.reservation else "placed")
                self._clear_proposal(o)
                return "Customer declined the business proposal. The original request remains for review."
            if action == "pickup":
                if o.status != "ready_for_pickup" or o.pickup_accepted_at:
                    raise ValueError("Pickup has already been accepted or the order is not ready.")
                o.pickup_accepted_at = stamp(clock)
                o.fulfill_due_at = stamp(clock + timedelta(seconds=b.completion_delay_seconds))
                return f"Customer accepted pickup. Completion scheduled in {b.completion_delay_seconds} seconds."
            raise ValueError("Unknown customer action.")
        return self._change(order_id, version, "customer", action, mutate, now)

    def settle_due(self, now=None) -> int:
        now = now or utc_now()
        count = 0
        with self.connection(write=True) as con:
            for o in self._orders(con):
                if o.status == "ready_for_pickup" and o.pickup_accepted_at and o.fulfill_due_at and datetime.fromisoformat(o.fulfill_due_at) <= now:
                    o.status, o.fulfilled_at = "fulfilled", stamp(now)
                    o.version += 1
                    self._persist(con, o, "system", "fulfilled", "Pickup acceptance countdown completed. Order retained in history.", now)
                    count += 1
        return count

    def clear_active(self, now=None) -> int:
        now = now or utc_now()
        count = 0
        with self.connection(write=True) as con:
            for o in self._orders(con):
                if o.status in TERMINAL:
                    continue
                # Ready/produced goods still consume that day's production capacity.
                if o.status != "ready_for_pickup":
                    o.reservation = None
                o.status, o.question, o.fulfill_due_at = "cancelled", "", None
                self._clear_proposal(o)
                o.version += 1
                self._persist(con, o, "business", "boards_cleared", "Cleared by business. Active order cancelled; history and usage retained.", now)
                count += 1
        return count

    def save_business(self, business: Business):
        with self.connection(write=True) as con:
            old = self._business(con)
            if {p.id for p in old.products} != {p.id for p in business.products}:
                raise ValueError("Keep the existing product IDs to preserve order history.")
            committed = {}
            for o in self._orders(con):
                if o.reservation:
                    key = (o.reservation.product_id, o.reservation.pickup_date)
                    committed[key] = committed.get(key, 0) + (o.reservation.quantity or 0)
            for p in business.products:
                if any(qty > p.daily_capacity for (pid, _), qty in committed.items() if pid == p.id):
                    raise ValueError(f"{p.name}: capacity cannot be lower than an existing committed daily quantity.")
            con.execute("UPDATE meta SET value=? WHERE key='business'", (business.model_dump_json(),))
            self._bump(con)

    def claim_request(self, order_id: str, workspace_limit: int):
        with self.connection(write=True) as con:
            total = con.execute("SELECT COALESCE(SUM(requests),0) FROM usage").fetchone()[0]
            used = con.execute("SELECT requests FROM usage WHERE order_id=?", (order_id,)).fetchone()
            if total >= workspace_limit:
                raise ValueError("The workspace Groq request limit is reached. Structured actions remain available.")
            if used and used[0] >= self._business(con).max_order_calls:
                raise ValueError("This order reached its Groq request limit. Use structured fields or business actions.")
            con.execute("INSERT INTO usage (order_id,requests) VALUES (?,1) ON CONFLICT(order_id) DO UPDATE SET requests=requests+1", (order_id,))
            self._bump(con)

    def record_tokens(self, order_id, input_tokens, output_tokens):
        if not (input_tokens or output_tokens):
            return
        with self.connection(write=True) as con:
            con.execute("UPDATE usage SET input_tokens=input_tokens+?, output_tokens=output_tokens+? WHERE order_id=?", (input_tokens, output_tokens, order_id))
            self._bump(con)

    def backup(self) -> bytes:
        s = self.snapshot()
        return Backup(business=s["business"], orders=s["orders"], events=s["events"]).model_dump_json(indent=2).encode()

    def restore(self, data: bytes):
        if len(data) > 2_000_000:
            raise ValueError("Keep backups below 2 MB.")
        backup = Backup.model_validate_json(data)
        ids = {o.id for o in backup.orders}
        if len(ids) != len(backup.orders) or len({e.id for e in backup.events}) != len(backup.events):
            raise ValueError("Duplicate IDs in backup.")
        if any(e.order_id not in ids for e in backup.events):
            raise ValueError("The backup contains history without its order.")
        product_ids = {p.id for p in backup.business.products}
        committed = {}
        for o in backup.orders:
            o.customer_key = customer_key(o.customer)
            for field in ["created_at", "updated_at", "accepted_at", "ready_at", "pickup_accepted_at", "fulfill_due_at", "fulfilled_at"]:
                if (value := getattr(o, field)) and datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError("Backup timestamps must include a timezone.")
            if o.reservation and (o.reservation.product_id not in product_ids or not all(o.reservation.model_dump().values()) or o.unit_price is None):
                raise ValueError("Invalid confirmed reservation in backup.")
            if o.status in {"accepted", "ready_for_pickup", "fulfilled"} and o.reservation is None:
                raise ValueError("A confirmed order is missing its reservation.")
            if o.reservation:
                key = (o.reservation.product_id, o.reservation.pickup_date)
                committed[key] = committed.get(key, 0) + o.reservation.quantity
        for p in backup.business.products:
            if any(q > p.daily_capacity for (pid, _), q in committed.items() if pid == p.id):
                raise ValueError("The backup exceeds its declared daily capacity.")
        with self.connection(write=True) as con:
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM events")
            con.execute("UPDATE meta SET value=? WHERE key='business'", (backup.business.model_dump_json(),))
            con.executemany("INSERT INTO orders VALUES (?,?)", [(o.id, o.model_dump_json()) for o in backup.orders])
            con.executemany("INSERT INTO events (id,payload) VALUES (?,?)", [(e.id, e.model_dump_json()) for e in backup.events])
            # Usage is intentionally not imported or reset by a restore.
            self._bump(con)
