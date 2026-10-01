"""Chat2Order Queue Edition. Deploy app.py on Streamlit with Python 3.12."""
from __future__ import annotations

import html
import logging
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

os.environ.setdefault("CREWAI_TELEMETRY_DISABLED", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import streamlit as st
from pydantic import ValidationError

from chat2order.agents import BudgetExceeded, CallBudget, ProviderFailure
from chat2order.data import csv_bytes, customer_key, describe, local_now, order_rows, order_total, stable_hash, utc_now
from chat2order.models import Business, Details, Product, QueueOrder, STATUS_LABELS, TERMINAL
from chat2order.rules import acceptance_issues, alternatives_for, available_capacity, detail_issues
from chat2order.store import Conflict, Store
from chat2order.workflow import fulfillment_advice, interpret_text

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Chat2Order · Queue Edition", page_icon="💬", layout="wide", initial_sidebar_state="expanded")
st.markdown("<style>" + (ROOT / "assets/styles.css").read_text() + "</style>", unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value if value is not None else ""))


def secret(name, default=""):
    if name in os.environ:
        return os.environ[name]
    try:
        return st.secrets.get(name, default)
    except (FileNotFoundError, KeyError):
        return default


def limit_setting(name, default):
    try:
        return max(1, min(10000, int(secret(name, default))))
    except (TypeError, ValueError):
        return default


@st.cache_resource
def shared_store(path):
    return Store(path)


store = shared_store(secret("CHAT2ORDER_DB_PATH", str(ROOT / "runtime/orders.sqlite3")))
for key, value in {"nav": "User Interface", "customer_profile": st.session_state.get("customer_name", "Ayesha"), "agent_cache": {}, "session_calls": 0, "session_tokens": 0, "draft_id": "CO-" + uuid4().hex[:12].upper(), "attempt_times": {}, "advice_cache": {}, "last_traces": [], "notice": None}.items():
    if key not in st.session_state:
        st.session_state[key] = value
if st.session_state.pop("reset_clear_confirmation", False):
    st.session_state.clear_confirm = False

store.settle_due()
snapshot = store.snapshot()
business: Business = snapshot["business"]
orders: list[QueueOrder] = snapshot["orders"]
events = snapshot["events"]
active = [o for o in orders if o.status not in TERMINAL]
api_key = secret("GROQ_API_KEY")
model_id = secret("GROQ_MODEL", "openai/gpt-oss-20b")
workspace_limit = limit_setting("MAX_WORKSPACE_CALLS", 100)
session_limit = limit_setting("MAX_SESSION_CALLS", 20)
st.session_state.render_revision = snapshot["revision"]


def notice(text, kind="success"):
    st.session_state.notice = {"text": text, "kind": kind}


def act(fn, *args, success="Order updated.", **kwargs):
    try:
        result = fn(*args, **kwargs)
        notice(success if not callable(success) else success(result))
        st.rerun()
    except (ValueError, ValidationError, ProviderFailure, BudgetExceeded) as exc:
        notice(str(exc), "error")
        st.error(str(exc))
    except Exception as exc:
        logging.error("Queue action failed (%s)", type(exc).__name__)
        notice("The action could not finish. Your previous order is preserved; refresh and try again.", "error")
        st.error(st.session_state.notice["text"])


def badge(status):
    color = {"needs_clarification": "orange", "placed": "violet", "accepted": "green", "modified": "orange", "ready_for_pickup": "green", "fulfilled": "green", "rejected": "red", "cancelled": "grey"}[status]
    return f'<span class="badge {color}">{esc(STATUS_LABELS[status])}</span>'


def hero(eyebrow, title, text, items):
    cards = "".join(f'<div class="mini-order"><div class="mini-avatar">{esc(icon)}</div><div><b>{esc(label)}</b><small>{esc(sub)}</small></div></div>' for icon, label, sub in items)
    st.markdown(f'<div class="hero"><div><div class="eyebrow">{esc(eyebrow)}</div><h1>{title}</h1><p>{esc(text)}</p></div><div class="hero-art">{cards}</div></div>', unsafe_allow_html=True)


def section(title, subtitle=""):
    st.markdown(f'<div class="section-top"><h3>{esc(title)}</h3><small>{esc(subtitle)}</small></div>', unsafe_allow_html=True)


def metrics(items):
    for col, (label, value, foot) in zip(st.columns(len(items)), items):
        col.markdown(f'<div class="metric-card"><div class="metric-label">{esc(label)}</div><div class="metric-value">{esc(value)}</div><div class="metric-foot">{esc(foot)}</div></div>', unsafe_allow_html=True)


def local_timestamp(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo(business.timezone)).strftime("%d %b · %H:%M:%S") if value else "—"


def card(order):
    total = order_total(order, business)
    st.markdown(f'<div class="order-head"><div class="customer"><div class="avatar">{esc(order.customer[:1].upper())}</div><div><b>{esc(order.customer)}</b><small>{esc(order.id)}</small></div></div>{badge(order.status)}</div><div class="order-product">{esc(describe(order.details, business).split(" · ")[0])}</div><div class="order-meta">Pickup: {esc(order.details.pickup_date or "Date needed")} · {esc(order.details.pickup_time or "Time needed")}<br>{esc(order.currency)} {f"{total:,}" if total is not None else "—"} · Placed {esc(local_timestamp(order.created_at))}</div>', unsafe_allow_html=True)
    if order.question:
        st.markdown(f'<div class="issue-note">{esc(order.question)}</div>', unsafe_allow_html=True)
    if order.status == "modified":
        if order.proposal:
            st.info("Proposed: " + describe(order.proposal, business))
        st.caption(order.proposal_note)
        st.caption("The accepted reservation stays in place until this change is approved." if order.reservation else "The proposal is awaiting agreement; it does not reserve stock.")
    if order.status == "ready_for_pickup":
        if order.pickup_accepted_at:
            st.success("Pickup accepted. Completing at " + local_timestamp(order.fulfill_due_at))
        else:
            st.success("Ready for Pickup · awaiting customer acceptance.")


def timeline(order_id):
    for event in reversed([e for e in events if e.order_id == order_id][-15:]):
        st.markdown(f"**{esc(STATUS_LABELS[event.status])}** · {local_timestamp(event.at)} · {esc(event.actor.title())}")
        st.text(event.note)


def details_inputs(details, prefix, *, allow_empty=False):
    ids = [None] + [p.id for p in business.products] if allow_empty else [p.id for p in business.products]
    labels = {p.id: p.name for p in business.products}
    pid = st.selectbox("Product", ids, index=ids.index(details.product_id) if details.product_id in ids else 0, format_func=lambda x: labels.get(x, "Choose a product"), key=prefix + "_product")
    qty = st.number_input("Quantity", min_value=1, max_value=10000, value=details.quantity or 1, step=1, key=prefix + "_qty")
    c1, c2 = st.columns(2)
    initial_date = date.fromisoformat(details.pickup_date) if details.pickup_date else (None if allow_empty else local_now(business).date() + timedelta(days=3))
    initial_time = time.fromisoformat(details.pickup_time) if details.pickup_time else (None if allow_empty else time.fromisoformat(business.pickup_start))
    day = c1.date_input("Pickup date", value=initial_date, key=prefix + "_date")
    clock = c2.time_input("Pickup time", value=initial_time, step=60, key=prefix + "_time")
    return Details(product_id=pid, quantity=int(qty), pickup_date=day.isoformat() if day else None, pickup_time=clock.strftime("%H:%M") if clock else None)


def live_allowed():
    code = secret("LIVE_AI_ACCESS_CODE")
    if not api_key:
        raise ProviderFailure("Text interpretation is unavailable. Add GROQ_API_KEY to Streamlit deployment secrets, or use the structured sample workflow.")
    if code and st.session_state.get("access_code", "") != code:
        raise ValueError("Enter the demo access code in the sidebar before requesting AI.")
    if st.session_state.session_calls >= session_limit:
        raise BudgetExceeded("This browser session reached its Groq request limit. Structured controls remain available.")


def run_ai(order_id, fn, **kwargs):
    live_allowed()
    budget = CallBudget(limit=min(2, session_limit - st.session_state.session_calls), claim_hook=lambda: store.claim_request(order_id, workspace_limit))
    try:
        result = fn(api_key=api_key, model_id=model_id, budget=budget, cache=st.session_state.agent_cache, **kwargs)
        st.session_state.last_traces = [t.model_dump() for t in result.traces]
        return result
    finally:
        store.record_tokens(order_id, budget.input_tokens, budget.output_tokens)
        st.session_state.session_calls += budget.calls
        st.session_state.session_tokens += budget.input_tokens + budget.output_tokens
        st.session_state.render_revision = store.snapshot(False)["revision"]


def submit_text(text, order=None):
    customer = " ".join(st.session_state.customer_profile.split())
    if not customer or not text.strip():
        raise ValueError("Enter your name and an order message.")
    order_id = order.id if order else st.session_state.draft_id
    existing = store.get(order_id)
    if order is None and existing:
        return existing
    if order and (not existing or existing.version != order.version):
        raise Conflict("This order changed. Review its latest details before submitting an update.")
    key = stable_hash([order_id, text.strip(), order.version if order else 0, business.model_dump()])
    if key not in st.session_state.attempt_times:
        st.session_state.attempt_times[key] = utc_now().isoformat()
    submitted_at = datetime.fromisoformat(st.session_state.attempt_times[key])
    with st.spinner("Order Interpreter is reviewing your message…"):
        result = run_ai(order_id, interpret_text, text=text.strip(), business=business, order=order, submitted_at=submitted_at)
    updated = store.apply_text(order_id=order_id, customer=customer, text=text.strip(), result=result.interpretation, expected_version=order.version if order else None, now=utc_now())
    if order is None:
        st.session_state.draft_id = "CO-" + uuid4().hex[:12].upper()
        st.session_state.pending_clear_text = True
    return updated


def example_text():
    target = local_now(business).date() + timedelta(days=3)
    st.session_state.order_text = f"I'd like 4 brownies for {target.isoformat()} at 14:00, pickup please."


def remember_customer():
    # Keep the profile outside widget state, which is cleared on other views.
    st.session_state.customer_profile = st.session_state._customer_name


def load_samples():
    if any(o.status not in TERMINAL for o in store.snapshot(False)["orders"]):
        raise ValueError("Clear the active boards before loading another sample set.")
    if not {"CC", "VC", "BR", "CK"}.issubset({p.id for p in business.products}):
        raise ValueError("These samples require the supplied four-product menu.")
    day = (local_now(business).date() + timedelta(days=3)).isoformat()
    for name, pid, qty, clock in [("Ayesha", "CC", 6, "16:00"), ("Bilal", "CC", 4, "16:00"), ("Hina", "VC", 2, None), ("Dani", "BR", 4, "14:00")]:
        product = next(p for p in business.products if p.id == pid)
        text = f"I'd like {qty} {product.name.lower()} for {day}" + (f" at {clock}." if clock else ".")
        store.create(order_id="CO-" + uuid4().hex[:12].upper(), customer=name, text=text, details=Details(product_id=pid, quantity=qty, pickup_date=day, pickup_time=clock), actor="sample loader")


with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-icon">✓</div><div><div class="brand-name">Chat2Order</div><div class="brand-sub">From chat to clarity</div></div></div>', unsafe_allow_html=True)
    st.caption("YOUR WORKSPACE")
    st.markdown(f"**{esc(business.name)}**")
    page = st.radio("Navigation", ["User Interface", "Business Interface", "Order History", "Business Settings"], key="nav", label_visibility="collapsed")
    st.divider()
    st.markdown(f'<div class="sidebar-stat"><b>{len(active)}</b><span>active orders</span></div>', unsafe_allow_html=True)
    st.caption("Shared demo · customer and business views")
    st.caption("Groq connected" if api_key else "Groq key not configured")
    if secret("LIVE_AI_ACCESS_CODE"):
        st.text_input("Demo access code", type="password", key="access_code")
    st.caption("All updates appear automatically. Order history stays available after clearing the boards.")

if msg := st.session_state.notice:
    left, right = st.columns([12, 1])
    getattr(left, msg["kind"])(msg["text"])
    if right.button("✕", key="dismiss_notice", help="Dismiss notification"):
        st.session_state.notice = None
        st.rerun()

st.markdown(f'<div class="pill-line"><span class="badge">{esc(business.name)}</span><span class="badge green">Queue Edition</span><span class="badge grey">Pickup orders</span></div>', unsafe_allow_html=True)


@st.fragment(run_every="1s")
def heartbeat():
    store.settle_due()
    fresh = store.snapshot(False)
    if fresh["revision"] != st.session_state.render_revision:
        st.rerun()
    relevant = [o for o in fresh["orders"] if o.status == "ready_for_pickup" and o.fulfill_due_at and (page != "User Interface" or o.customer_key == customer_key(st.session_state.customer_profile))]
    if relevant and page in {"User Interface", "Business Interface"}:
        now = utc_now()
        text = " · ".join(f"{o.id}: {max(0, int((datetime.fromisoformat(o.fulfill_due_at) - now).total_seconds()) + 1)}s" for o in relevant[:4])
        st.caption("Pickup accepted · completing automatically · " + text)
    else:
        st.caption("● Live order updates · " + local_now(fresh["business"]).strftime("%H:%M:%S"))


heartbeat()


def user_interface():
    hero("Your next good order", 'Say it naturally.<br>Follow it <span>clearly.</span>', "Tell us what you would like. Review any suggested changes, follow the business response, and accept pickup when your order is ready.", [("1", "Write your order", "A simple message is enough"), ("2", "Stay in the loop", "Every decision, visible"), ("✓", "Accept pickup", "A clear finish to every order")])
    mine = [o for o in active if o.customer_key == customer_key(st.session_state.customer_profile)]
    metrics([("Your active orders", len(mine), "Current requests"), ("Awaiting a decision", sum(o.status in {"placed", "modified"} for o in mine), "Business or customer review"), ("Ready for pickup", sum(o.status == "ready_for_pickup" for o in mine), "Accept pickup below")])
    if "_customer_name" not in st.session_state:
        st.session_state._customer_name = st.session_state.customer_profile
    st.text_input("Customer name", key="_customer_name", on_change=remember_customer, max_chars=80, help="Demo identity: use the same name to follow your orders. Your selected name stays selected when switching views.")
    left, right = st.columns([1, 1.35], gap="large")
    with left:
        section("Place an order", "One menu product per order")
        with st.container(border=True):
            st.button("Use an example", on_click=example_text, key="example", type="secondary")
            if st.session_state.pop("pending_clear_text", False):
                st.session_state.order_text = ""
            with st.form("new_order"):
                text = st.text_area("What would you like?", key="order_text", height=145, max_chars=3000, placeholder="I'd like 6 chocolate cupcakes for Friday at 4 pm, pickup please.")
                sent = st.form_submit_button("Place order →", type="primary", width="stretch", disabled=not api_key)
            st.caption("Include the product, quantity, pickup date, and time. Missing details can be completed after submission.")
            if not api_key:
                st.info("Text ordering is unavailable until Groq is configured. Load sample orders in the Business Interface to explore the workflow.")
            if sent:
                act(submit_text, text, success=lambda o: f"{o.id} · {STATUS_LABELS[o.status]}. Your request is visible to the business.")
        section("The menu", f"{business.currency} · per item")
        for p in business.products:
            st.markdown(f'<div class="menu-row"><b>{esc(p.name)}</b><span>{business.currency} {p.price:,}</span></div>', unsafe_allow_html=True)
        st.caption(f"Pickup {business.pickup_start}–{business.pickup_end} · {business.lead_hours} hours minimum lead time.")
    with right:
        section("Your order board", "Updates appear here automatically")
        my_ids = {o.id for o in orders if o.customer_key == customer_key(st.session_state.customer_profile)}
        terminal_updates = [e for e in events if e.order_id in my_ids and e.status in TERMINAL]
        if terminal_updates:
            last = terminal_updates[-1]
            st.info(f"Latest closed order: {last.order_id} · {STATUS_LABELS[last.status]}. {last.note} Full record is in Order History.")
        if not mine:
            st.markdown('<div class="empty-state"><div>✦</div><b>A little space for your next order.</b><p>Your active orders will appear here.</p></div>', unsafe_allow_html=True)
        for o in reversed(mine):
            with st.container(border=True):
                card(o)
                if o.status != "ready_for_pickup":
                    with st.expander("Complete or update details · no AI request", expanded=o.status == "needs_clarification"):
                        if o.reservation:
                            st.caption("Changes to an accepted order go to the business for approval. Your confirmed order stays reserved until approval.")
                        elif o.status == "modified":
                            st.caption("Sending your own details replaces the pending business proposal and sends your order back for review.")
                        with st.form("customer_fields_" + o.id + str(o.version)):
                            details = details_inputs(o.proposal or o.details, "cf_" + o.id + str(o.version), allow_empty=True)
                            confirmed = st.checkbox("These details confirm my standard pickup order.")
                            save = st.form_submit_button("Send these details", width="stretch")
                        if save:
                            if not confirmed:
                                st.error("Confirm the standard pickup details before sending them.")
                            else:
                                act(store.update_details, o.id, o.version, st.session_state.customer_profile, details, success="Details sent. The business can review your update.")
                if o.status == "modified" and o.proposed_by == "business":
                    c1, c2 = st.columns(2)
                    if c1.button("Accept modification", key="accept_prop_" + o.id, type="primary", width="stretch"):
                        act(store.customer_action, o.id, o.version, st.session_state.customer_profile, "accept_proposal", success="Modification accepted. Your revised order is confirmed.")
                    if c2.button("Decline modification", key="decline_prop_" + o.id, width="stretch"):
                        act(store.customer_action, o.id, o.version, st.session_state.customer_profile, "decline_proposal", success="Modification declined. The business will see your response.")
                if o.status == "ready_for_pickup" and not o.pickup_accepted_at:
                    if st.button("Accept pickup ✓", key="pickup_" + o.id, type="primary", width="stretch"):
                        act(store.customer_action, o.id, o.version, st.session_state.customer_profile, "pickup", success="Pickup accepted. The completion countdown has started.")
                if o.status != "ready_for_pickup":
                    with st.expander("Send a message or request a change"):
                        with st.form("followup_" + o.id + str(o.version)):
                            update = st.text_area("Your message", max_chars=3000, key="follow_text_" + o.id + str(o.version), placeholder="My pickup time is 15:00.")
                            send = st.form_submit_button("Send message", disabled=not api_key)
                        st.caption("A message uses the Order Interpreter. Use the detail fields above for updates without AI. Accepted orders keep their reservation while the business reviews a change.")
                        if send:
                            act(submit_text, update, o, success=lambda changed: f"Message processed · {STATUS_LABELS[changed.status]}.")
                with st.expander("Order activity"):
                    timeline(o.id)


def business_interface():
    hero("A calmer order desk", 'Every request.<br>A <span>clear next step.</span>', "Review requests in arrival order, resolve missing details, and keep customers informed. You control every business decision.", [("↗", "One shared queue", "Clarification requests included"), ("↔", "Changes need agreement", "Original details stay visible"), ("✓", "Ready, then fulfilled", "Customer acceptance starts the timer")])
    metrics([("Active orders", len(active), "Oldest requests first"), ("Need clarification", sum(o.status == "needs_clarification" for o in active), "Review or reject"), ("Ready for Pickup", sum(o.status == "ready_for_pickup" for o in active), "Waiting for customer"), ("Groq requests", snapshot["usage"]["requests"], f"Workspace limit {workspace_limit}")])
    tools_left, tools_right = st.columns([1, 2])
    if tools_left.button("Load sample orders", disabled=bool(active), width="stretch"):
        act(load_samples, success="Four structured sample orders loaded. No Groq requests were made.")
    tools_right.caption("Samples: Ayesha and Bilal compete for chocolate capacity; Hina needs a pickup time; Dani has a complete request.")
    with st.expander("Clear active boards"):
        st.caption("Cancels all active orders in both interfaces. History and Groq usage are retained. Ready/produced quantities still count toward daily production capacity.")
        confirmed = st.checkbox("I want to cancel all active orders and clear both boards.", key="clear_confirm")
        if st.button("Clear both active boards", disabled=not confirmed or not active, key="clear_boards"):
            st.session_state.reset_clear_confirmation = True
            act(store.clear_active, success=lambda n: f"{n} active orders cancelled. Both boards are clear; history is preserved.")
    section("Business queue", "FIFO review · select any request")
    labels = {"All active": None, **{STATUS_LABELS[s]: s for s in ["needs_clarification", "placed", "accepted", "modified", "ready_for_pickup"]}}
    chosen_filter = st.radio("Queue filter", list(labels), horizontal=True, label_visibility="collapsed")
    queue = [o for o in active if labels[chosen_filter] is None or o.status == labels[chosen_filter]]
    if not queue:
        st.markdown('<div class="empty-state"><div>✓</div><b>This queue is clear.</b><p>New customer requests will appear here.</p></div>', unsafe_allow_html=True)
        return
    left, right = st.columns([1, 1.8], gap="large")
    with left:
        ids = [o.id for o in queue]
        lookup = {o.id: o for o in queue}
        if st.session_state.get("selected_business_order") not in ids:
            st.session_state.selected_business_order = ids[0]
        selected = st.selectbox("Review order", ids, format_func=lambda oid: f"{lookup[oid].customer} · {STATUS_LABELS[lookup[oid].status]} · {oid}", key="selected_business_order")
        for index, item in enumerate(queue[:8], 1):
            with st.container(border=True):
                st.caption(f"Queue position {index}")
                card(item)
        if len(queue) > 8:
            st.caption("Use the selector to review the remaining orders.")
    o = lookup[selected]
    with right:
        with st.container(border=True):
            card(o)
            manage, assist, activity = st.tabs(["Manage order", "Fulfillment advice", "Activity"])
            with manage:
                if o.status == "needs_clarification":
                    st.warning("The customer can complete these details. You can also propose a complete alternative or reject this request.")
                if o.status == "placed":
                    issues = acceptance_issues(o.details, business, orders, o.id, utc_now())
                    for issue in issues:
                        st.warning(issue)
                    if st.button("Accept order", type="primary", key="owner_accept_" + o.id, disabled=bool(issues) or bool(o.question), width="stretch"):
                        act(store.business_action, o.id, o.version, "accept", success="Order accepted. The customer sees the confirmation.")
                if o.status == "accepted":
                    if st.button("Mark Ready for Pickup", type="primary", key="owner_ready_" + o.id, width="stretch"):
                        act(store.business_action, o.id, o.version, "ready", success="Ready for Pickup. The customer can now accept pickup.")
                if o.status == "modified" and o.proposed_by == "customer":
                    c1, c2 = st.columns(2)
                    if c1.button("Approve cancellation" if o.cancellation_requested else "Approve customer change", key="approve_change_" + o.id, type="primary", width="stretch"):
                        act(store.business_action, o.id, o.version, "approve_change", success="Customer request approved.")
                    if c2.button("Keep original order", key="decline_change_" + o.id, width="stretch"):
                        act(store.business_action, o.id, o.version, "decline_change", success="Original accepted order retained. The customer sees the response.")
                if o.status == "modified" and o.proposed_by == "business":
                    st.info("Awaiting the customer's acceptance or rejection of your proposal.")
                if o.status == "ready_for_pickup":
                    st.caption("The completion timer starts when the customer accepts pickup. The configured delay is frozen for that order at acceptance.")
                if o.status != "ready_for_pickup":
                    with st.expander("Suggest a modification"):
                        options = alternatives_for(o, business, orders, utc_now())
                        option_map = {a["id"]: a for a in options}
                        selected_option = st.selectbox("Start from a validated alternative", [None] + list(option_map), format_func=lambda aid: "Edit details manually" if aid is None else describe(Details(**option_map[aid]["details"]), business), key="proposal_option_" + o.id + str(o.version))
                        base = Details(**option_map[selected_option]["details"]) if selected_option else (o.proposal or o.details)
                        prefix = "bp_" + o.id + str(o.version) + (selected_option or "manual")
                        with st.form(prefix):
                            proposed = details_inputs(base, prefix)
                            reason = st.text_area("Message to customer", max_chars=1200, placeholder="We can offer this alternative. Would it work for you?", key=prefix + "_reason")
                            offered = st.form_submit_button("Send modification proposal", width="stretch")
                        if offered:
                            act(store.business_action, o.id, o.version, "propose", details=proposed, reason=reason, success="Proposal sent. Customer agreement is required before acceptance.")
                    with st.expander("Reject this order"):
                        with st.form("reject_" + o.id + str(o.version)):
                            reason = st.text_area("Rejection reason", max_chars=1200, key="reject_reason_" + o.id + str(o.version))
                            rejected = st.form_submit_button("Reject and move to history", width="stretch")
                        if rejected:
                            act(store.business_action, o.id, o.version, "reject", reason=reason, success="Order rejected. The customer sees the reason and the record stays in history.")
            with assist:
                issues = acceptance_issues(o.details, business, orders, o.id, utc_now()) if not o.reservation else []
                if o.question:
                    issues = [o.question] + issues
                options = alternatives_for(o, business, orders, utc_now())
                st.caption("Availability and alternatives are calculated without AI. Request the Fulfillment Agent only when contextual advice would help.")
                if options:
                    st.dataframe([{"Option": a["id"], "Details": describe(Details(**a["details"]), business), "Total": f"{business.currency} {a['total']:,}"} for a in options], hide_index=True, width="stretch")
                else:
                    st.info("No validated alternative is available yet. Complete the missing details or review the business rules.")
                cache_key = stable_hash([o.model_dump(), options, issues, business.model_dump()])
                if st.button("Get AI fulfillment advice", key="advice_" + o.id, disabled=not api_key or o.status == "ready_for_pickup", width="stretch"):
                    try:
                        with st.spinner("Fulfillment Agent is reviewing validated options…"):
                            result = run_ai(o.id, fulfillment_advice, order=o, business=business, alternatives=options, issues=issues)
                        st.session_state.advice_cache[cache_key] = result.advice.model_dump()
                        notice("Fulfillment advice prepared. Review it before proposing any change.")
                    except (ValueError, ProviderFailure, BudgetExceeded, ValidationError) as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        logging.error("Advice failed (%s)", type(exc).__name__)
                        st.error("Advice could not finish. Use the validated options or try again manually.")
                if advice := st.session_state.advice_cache.get(cache_key):
                    st.success(advice["explanation"])
                    if advice["alternative_id"]:
                        st.caption("Suggested option: " + advice["alternative_id"])
                    st.markdown("**Draft for customer**")
                    st.text(advice["customer_message"])
                    st.caption("The order stays unchanged until you send a proposal and the customer agrees.")
            with activity:
                st.markdown("**Original request**")
                st.text(o.original_text)
                timeline(o.id)
    with st.expander("Agent activity and usage"):
        st.caption(f"Workspace: {snapshot['usage']['requests']} requests · {snapshot['usage']['input_tokens'] + snapshot['usage']['output_tokens']} reported tokens. This browser: {st.session_state.session_calls}/{session_limit} requests.")
        for trace in st.session_state.last_traces:
            st.write(f"{trace['agent']} · {trace['requests']} requests · {trace['input_tokens'] + trace['output_tokens']} tokens" + (" · cached" if trace["cached"] else ""))
        st.caption("No AI calls on status changes, refreshes, countdowns, exports, or clearing boards. Automatic retries, planning, delegation, embeddings, and tool loops are disabled.")


def order_history():
    hero("Every order has a story", 'The full picture.<br><span>Always in view.</span>', "Placed, clarified, revised, rejected, collected. Browse the current status and the decisions that brought each order here.", [("≡", "Every status retained", "Active and closed orders"), ("↔", "Every change recorded", "Customer, business, and system"), ("↓", "Take your records with you", "CSV history and a JSON backup")])
    metrics([("All orders", len(orders), "Across every status"), ("Fulfilled", sum(o.status == "fulfilled" for o in orders), "Pickup workflow completed"), ("History events", len(events), "An audit trail for every action")])
    c1, c2 = st.columns([2, 1])
    selected_statuses = c1.multiselect("Status", list(STATUS_LABELS), default=list(STATUS_LABELS), format_func=lambda s: STATUS_LABELS[s])
    search = c2.text_input("Find customer or order", max_chars=80).strip().casefold()
    filtered = [o for o in reversed(orders) if o.status in selected_statuses and (not search or search in o.customer.casefold() or search in o.id.casefold())]
    rows = order_rows(filtered, business)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("No orders match this view.")
    d1, d2, d3 = st.columns(3)
    d1.download_button("Download order history CSV", data=csv_bytes(rows), file_name="chat2order_history.csv", mime="text/csv", disabled=not rows, width="stretch")
    event_rows = [{"Order ID": e.order_id, "Time (UTC)": e.at, "Actor": e.actor, "Action": e.action, "Status": STATUS_LABELS[e.status], "Note": e.note} for e in events]
    d2.download_button("Download activity CSV", data=csv_bytes(event_rows), file_name="chat2order_activity.csv", mime="text/csv", disabled=not event_rows, width="stretch")
    d3.download_button("Download workspace backup", data=store.backup(), file_name="chat2order_backup.json", mime="application/json", width="stretch")
    if filtered:
        section("Order timeline", "Original request and recorded changes")
        lookup = {o.id: o for o in filtered}
        oid = st.selectbox("Inspect history", list(lookup), format_func=lambda x: f"{lookup[x].customer} · {x} · {STATUS_LABELS[lookup[x].status]}")
        with st.container(border=True):
            st.text(lookup[oid].original_text)
            timeline(oid)


def business_settings():
    hero("Make it your business", 'Your rules.<br>Your <span>order rhythm.</span>', "Set pickup hours, production capacity, and the delay after customer pickup acceptance. Existing countdown deadlines remain unchanged.", [("◷", "Pickup completion", f"Default {business.completion_delay_seconds} seconds"), ("≡", "Capacity stays authoritative", "Checked again on acceptance"), ("↗", "AI where it helps", "Two focused specialists")])
    with st.form("business_settings"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Business name", value=business.name, max_chars=80)
        delay = c2.number_input("Completion delay after pickup acceptance (seconds)", min_value=1, max_value=3600, value=business.completion_delay_seconds, step=1)
        c1, c2, c3 = st.columns(3)
        lead = c1.number_input("Minimum lead time (hours)", min_value=0, max_value=720, value=business.lead_hours)
        start = c2.time_input("Pickup opens", value=time.fromisoformat(business.pickup_start), step=60)
        end = c3.time_input("Pickup closes", value=time.fromisoformat(business.pickup_end), step=60)
        calls = st.number_input("Maximum Groq requests per order", min_value=1, max_value=100, value=business.max_order_calls)
        section("Menu and daily production capacity", business.currency)
        updated_products = []
        for p in business.products:
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.markdown(f"**{esc(p.name)}**")
            price = c2.number_input("Unit price", min_value=0, max_value=1_000_000, value=p.price, key="price_" + p.id)
            capacity = c3.number_input("Daily capacity", min_value=0, max_value=10000, value=p.daily_capacity, key="capacity_" + p.id)
            updated_products.append(Product(**(p.model_dump() | {"price": int(price), "daily_capacity": int(capacity)})))
        policy = st.text_area("Business policy", value=business.policy_text, height=210, max_chars=12000)
        saved = st.form_submit_button("Save business settings", type="primary", width="stretch")
    if saved:
        try:
            updated = Business.model_validate(business.model_dump() | {"name": name.strip(), "completion_delay_seconds": int(delay), "lead_hours": int(lead), "pickup_start": start.strftime("%H:%M"), "pickup_end": end.strftime("%H:%M"), "max_order_calls": int(calls), "products": [p.model_dump() for p in updated_products], "policy_text": policy})
            act(store.save_business, updated, success="Business settings saved. Existing countdowns keep their original deadlines.")
        except (ValueError, ValidationError) as exc:
            st.error(str(exc))
    with st.expander("Groq configuration and limits"):
        st.write("Model: " + model_id)
        st.caption(f"Key {'configured' if api_key else 'missing'} · workspace limit {workspace_limit} · browser limit {session_limit} · at most two requests per action.")
        st.caption("Change GROQ_API_KEY, GROQ_MODEL, MAX_WORKSPACE_CALLS, and MAX_SESSION_CALLS in Streamlit App settings → Secrets. Never put a real key in GitHub.")
    with st.expander("Restore a workspace backup"):
        st.caption("Replaces orders, history, and business settings with a version 2 backup. Groq usage counters are preserved. Download a current backup first.")
        uploaded = st.file_uploader("Workspace JSON", type=["json"])
        confirmed = st.checkbox("Replace the current workspace with this backup.")
        if st.button("Restore backup", disabled=uploaded is None or not confirmed):
            act(store.restore, uploaded.getvalue(), success="Workspace restored. All views now use the restored orders.")
    st.caption("Demo workspace shared by this app server. Export a backup before redeployment or server restart; hosted local storage is not a permanent database. Demo customer names and role switching are not authentication.")


{"User Interface": user_interface, "Business Interface": business_interface, "Order History": order_history, "Business Settings": business_settings}[page]()
st.markdown('<div class="footer-note">Chat2Order · From a customer message to a fulfilled order · Built for a clear, considered demo</div>', unsafe_allow_html=True)
