"""Validated records for the queue, audit history, and bounded agent outputs."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Product(Record):
    id: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    price: int = Field(ge=0, le=1_000_000)
    daily_capacity: int = Field(ge=0, le=10_000)
    aliases: list[str] = Field(default_factory=list)


class Business(Record):
    name: str = Field(default="Sunday & Sugar", min_length=1, max_length=80)
    currency: str = "PKR"
    timezone: str = "Asia/Karachi"
    lead_hours: int = Field(default=12, ge=0, le=720)
    pickup_start: str = "10:00"
    pickup_end: str = "18:00"
    completion_delay_seconds: int = Field(default=30, ge=1, le=3600)
    max_order_calls: int = Field(default=6, ge=1, le=100)
    products: list[Product] = Field(min_length=1, max_length=30)
    policy_text: str = Field(max_length=12000)

    @field_validator("pickup_start", "pickup_end")
    @classmethod
    def valid_time(cls, value):
        datetime.strptime(value, "%H:%M")
        return value

    @model_validator(mode="after")
    def valid_configuration(self):
        ZoneInfo(self.timezone)
        if self.pickup_start >= self.pickup_end:
            raise ValueError("Pickup closing time must be later than opening time.")
        ids = [p.id for p in self.products]
        if len(set(ids)) != len(ids):
            raise ValueError("Product IDs must be unique.")
        return self


class Details(Record):
    product_id: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=10000)
    pickup_date: str | None = None
    pickup_time: str | None = None

    @field_validator("pickup_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("Use YYYY-MM-DD for pickup dates.")
        return value

    @field_validator("pickup_time")
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            parsed = datetime.strptime(value, "%H:%M")
            if parsed.strftime("%H:%M") != value:
                raise ValueError("Use HH:MM for pickup times.")
        return value


Status = Literal["needs_clarification", "placed", "accepted", "modified", "ready_for_pickup", "fulfilled", "rejected", "cancelled"]
TERMINAL = {"fulfilled", "rejected", "cancelled"}
STATUS_LABELS = {"needs_clarification": "Needs clarification", "placed": "Placed", "accepted": "Accepted", "modified": "Modified", "ready_for_pickup": "Ready for Pickup", "fulfilled": "Fulfilled", "rejected": "Rejected", "cancelled": "Cancelled"}


class ChatMessage(Record):
    text: str = Field(min_length=1, max_length=3000)
    at: str


class QueueOrder(Record):
    id: str = Field(min_length=1, max_length=80)
    customer: str = Field(min_length=1, max_length=80)
    customer_key: str
    original_text: str = Field(max_length=3000)
    details: Details = Field(default_factory=Details)
    status: Status = "needs_clarification"
    question: str = Field(default="", max_length=1200)
    messages: list[ChatMessage] = Field(default_factory=list, max_length=100)
    proposal: Details | None = None
    proposed_by: Literal["business", "customer"] | None = None
    proposal_note: str = Field(default="", max_length=1200)
    previous_status: Status | None = None
    cancellation_requested: bool = False
    reservation: Details | None = None
    unit_price: int | None = Field(default=None, ge=0)
    currency: str = "PKR"
    created_at: str
    updated_at: str
    accepted_at: str | None = None
    ready_at: str | None = None
    pickup_accepted_at: str | None = None
    fulfill_due_at: str | None = None
    fulfilled_at: str | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def valid_lifecycle(self):
        for value in [self.created_at, self.updated_at, self.accepted_at, self.ready_at, self.pickup_accepted_at, self.fulfill_due_at, self.fulfilled_at]:
            if value and datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("Order timestamps must include a timezone.")
        if self.reservation and (not all(self.reservation.model_dump().values()) or self.unit_price is None):
            raise ValueError("Confirmed reservations need complete details and a unit price.")
        if self.status in {"accepted", "ready_for_pickup", "fulfilled"} and not self.reservation:
            raise ValueError("Confirmed orders need a reservation.")
        if self.status == "ready_for_pickup" and not self.ready_at:
            raise ValueError("Ready orders need their readiness timestamp.")
        if self.status == "fulfilled" and not all([self.fulfilled_at, self.pickup_accepted_at, self.fulfill_due_at]):
            raise ValueError("Fulfilled orders need pickup acceptance and completion timestamps.")
        if self.fulfill_due_at and not self.pickup_accepted_at:
            raise ValueError("The countdown needs customer pickup acceptance.")
        if self.status == "modified" and (not self.proposed_by or not self.previous_status or (not self.proposal and not self.cancellation_requested)):
            raise ValueError("Modified orders need a proposal or cancellation request and its origin.")
        return self


class AuditEvent(Record):
    id: str
    order_id: str
    at: str
    actor: str
    action: str
    status: Status
    note: str = Field(max_length=5000)
    details: dict[str, Any] = Field(default_factory=dict)


class InterpreterResult(Record):
    """Required nullable fields satisfy Groq strict structured-output mode."""
    intent: Literal["order", "update", "clarify", "unsupported", "cancel", "ignore"]
    product_id: str | None
    quantity: int | None
    pickup_date: str | None
    pickup_time: str | None
    question: str | None
    summary: str


class Advice(Record):
    explanation: str
    alternative_id: str | None
    customer_message: str
    policy_ids: list[str]


class Trace(Record):
    stage: str
    agent: str
    detail: str
    duration_ms: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


class Backup(Record):
    format_version: Literal[2] = 2
    business: Business
    orders: list[QueueOrder] = Field(max_length=2000)
    events: list[AuditEvent] = Field(max_length=30000)
