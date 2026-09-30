"""One focused offline lifecycle check; fixture agents make zero API requests."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chat2order.agents import CallBudget
from chat2order.data import csv_bytes
from chat2order.models import Advice, Details, InterpreterResult, TERMINAL
from chat2order.rules import alternatives_for, available_capacity
from chat2order.store import Conflict, Store
from chat2order.workflow import fulfillment_advice, interpret_text


def expect_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError("Expected a blocked invalid/stale action.")


def main():
    clock = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)
    day = "2026-10-04"
    budget = CallBudget(limit=2)
    with TemporaryDirectory(prefix="smoke-", dir=ROOT) as folder:
        store = Store(Path(folder) / "orders.sqlite3")
        b = store.snapshot()["business"]
        fixture = InterpreterResult(intent="order", product_id="CC", quantity=6, pickup_date=day, pickup_time="16:00", question=None, summary="Six chocolate cupcakes for pickup.")
        parsed = interpret_text(text=f"6 chocolate cupcakes for {day} at 16:00", business=b, order=None, submitted_at=clock, api_key="", model_id="openai/gpt-oss-20b", budget=budget, cache={}, fixture=fixture)
        assert parsed.interpretation == fixture and parsed.traces[0].requests == 0
        a = store.apply_text(order_id="CO-A", customer="Ayesha", text="Six chocolate cupcakes", result=parsed.interpretation, now=clock)
        assert a.status == "placed"
        a = store.business_action(a.id, a.version, "accept", now=clock)
        assert a.status == "accepted"
        thanks = InterpreterResult(intent="ignore", product_id=None, quantity=None, pickup_date=None, pickup_time=None, question=None, summary="Acknowledgment only.")
        a = store.apply_text(order_id=a.id, customer="Ayesha", text="Thanks!", result=thanks, expected_version=a.version, now=clock)
        assert a.status == "accepted" and a.reservation.product_id == "CC"
        bilal = store.create(order_id="CO-B", customer="Bilal", text="Four chocolate cupcakes", details=Details(product_id="CC", quantity=4, pickup_date=day, pickup_time="16:00"), now=clock)
        expect_error(store.business_action, bilal.id, bilal.version, "accept", now=clock)
        snapshot = store.snapshot()
        options = alternatives_for(bilal, b, snapshot["orders"], clock)
        vanilla = next(a for a in options if a["details"]["product_id"] == "VC" and a["details"]["pickup_date"] == day)
        advice = fulfillment_advice(order=bilal, business=b, alternatives=options, issues=["Only two chocolate cupcakes available."], api_key="", model_id="openai/gpt-oss-20b", budget=budget, cache={}, fixture=Advice(explanation="Offer available vanilla cupcakes with consent.", alternative_id=vanilla["id"], customer_message="Would four vanilla cupcakes work for you?", policy_ids=["P4"]))
        assert advice.advice.alternative_id == vanilla["id"] and advice.traces[0].requests == 0
        bilal = store.business_action(bilal.id, bilal.version, "propose", details=Details(**vanilla["details"]), now=clock)
        assert bilal.status == "modified" and bilal.reservation is None
        bilal = store.customer_action(bilal.id, bilal.version, "Bilal", "accept_proposal", now=clock)
        bilal = store.business_action(bilal.id, bilal.version, "ready", now=clock)
        assert store.settle_due(clock + timedelta(minutes=5)) == 0  # Ready alone starts no timer.
        bilal = store.customer_action(bilal.id, bilal.version, "Bilal", "pickup", now=clock)
        assert datetime.fromisoformat(bilal.fulfill_due_at) == clock + timedelta(seconds=30)
        changed_business = b.model_copy(update={"completion_delay_seconds": 5})
        store.save_business(changed_business)
        assert store.get(bilal.id).fulfill_due_at == bilal.fulfill_due_at
        expect_error(store.customer_action, bilal.id, bilal.version, "Bilal", "pickup", now=clock)
        assert store.settle_due(clock + timedelta(seconds=29)) == 0
        assert store.settle_due(clock + timedelta(seconds=30)) == 1
        assert store.get(bilal.id).status == "fulfilled"
        assert available_capacity("VC", day, store.snapshot()["orders"], b) == 12

        hina = store.create(order_id="CO-H", customer="Hina", text="Two vanilla cupcakes", details=Details(product_id="VC", quantity=2, pickup_date=day), now=clock)
        assert hina.status == "needs_clarification" and hina in store.snapshot()["orders"]
        assert hina.status not in TERMINAL  # Both active views retain clarification orders.
        hina = store.business_action(hina.id, hina.version, "reject", reason="Customer did not confirm pickup time.", now=clock)
        assert hina.status == "rejected"
        assert any(e.order_id == hina.id and e.status == "rejected" for e in store.snapshot()["events"])

        proposal = Details(product_id="VC", quantity=6, pickup_date=day, pickup_time="16:00")
        old_version = a.version
        a = store.business_action(a.id, a.version, "propose", details=proposal, now=clock)
        assert a.reservation.product_id == "CC"
        expect_error(store.business_action, a.id, old_version, "reject", reason="stale", now=clock)
        a = store.customer_action(a.id, a.version, "Ayesha", "decline_proposal", now=clock)
        assert a.status == "accepted" and a.details.product_id == "CC"
        a = store.business_action(a.id, a.version, "propose", details=proposal, now=clock)
        a = store.customer_action(a.id, a.version, "Ayesha", "accept_proposal", now=clock)
        assert a.details.product_id == "VC" and a.reservation.product_id == "VC"
        assert available_capacity("CC", day, store.snapshot()["orders"], b) == 8

        store.claim_request(a.id, 100)  # Exercise quota accounting without an HTTP request.
        store.record_tokens(a.id, 10, 5)
        count_before = len(store.snapshot()["orders"])
        assert store.clear_active(clock) == 1
        s = store.snapshot()
        assert all(o.status in TERMINAL for o in s["orders"])
        assert len(s["orders"]) == count_before and s["usage"]["requests"] == 1
        assert store.get(bilal.id).status == "fulfilled"
        assert available_capacity("VC", day, s["orders"], b) == 12
        backup = store.backup()
        store.restore(backup)
        assert len(store.snapshot()["orders"]) == count_before and store.snapshot()["usage"]["requests"] == 1
        assert "'=1+1" in csv_bytes([{"Customer": "=1+1"}]).decode("utf-8-sig")
    assert budget.calls == 0
    print("PASS: interpreter/fulfillment fixtures, clarification, capacity, consent, stale actions, frozen timer, fulfillment, clear/history, backup, CSV. Real Groq requests: 0.")


if __name__ == "__main__":
    main()
