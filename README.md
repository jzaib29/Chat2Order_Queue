# Chat2Order — Queue Edition

A customer-to-business order workflow built with Streamlit, CrewAI, Groq,
Pydantic, and Python's built-in SQLite. The existing lavender interface now has
four views: **User Interface, Business Interface, Order History, Business Settings**.

## Deploy through GitHub — no local development needed

1. Unzip the download. Upload its **contents** to the root of your GitHub
   repository. `app.py` and `requirements.txt` should be at the repository root.
   Include the `.streamlit` folder (GitHub drag-and-drop can omit hidden folders;
   use **Add file → Create new file** for `.streamlit/config.toml` if needed).
2. Open [Streamlit Community Cloud](https://share.streamlit.io/), choose your
   repository and branch, and set **Main file path** to `app.py`.
3. In advanced deployment settings, select **Python 3.12**. Dependencies use
   the baseline's pinned lock, not unbounded latest versions.
4. In Streamlit **App settings → Secrets**, paste:

   ```toml
   GROQ_API_KEY = "your-groq-api-key"
   GROQ_MODEL = "openai/gpt-oss-20b"
   MAX_SESSION_CALLS = "20"
   MAX_WORKSPACE_CALLS = "100"
   ```

5. Deploy or reboot the app after saving secrets. Keep real API keys in
   Streamlit secrets; never upload a real `secrets.toml` to GitHub.

The workflow remains **ZIP → GitHub → Streamlit + Groq key**. No database account,
paid messaging service, or local installation is needed. Optional browser-only
development is available through the included GitHub Codespaces configuration.

## How the queue works

| Status | Next step |
| --- | --- |
| Needs clarification | Visible to customer, business, and history. Customer completes details; business can propose details or reject. |
| Placed | FIFO business review; no stock is reserved yet. |
| Accepted | Business has accepted and capacity is reserved. |
| Modified | A proposed revision is awaiting the other party's approval. |
| Ready for Pickup | Business marked ready; customer must click **Accept pickup**. |
| Fulfilled | The pickup acceptance countdown expired; visible in history only. |
| Rejected | Business rejection and its reason stay in history; customer sees a notification. |
| Cancelled | Cancellation or **Clear active boards**; retained in history. |

There is one product per order and multiple independent orders per customer.
Missing information and unsupported requests never receive automatic acceptance.
The demo customer name identifies a profile; enter the same name to see its orders.
Business queues include incomplete requests, so they can be rejected even if the
customer never responds.

Business proposals require explicit customer acceptance. Customer changes to an
accepted order require owner approval. The original reservation stays in place
until the replacement is approved. Acceptance rechecks capacity inside a SQLite
transaction; stale or duplicated actions cannot overwrite a newer order version.
Pending proposals do not reserve stock. Fulfilled and already-produced quantities
still count toward production capacity for their pickup date.

**Clear active boards** cancels unfinished orders in both active views. It does
not erase history, mark orders fulfilled, reset usage, or change business settings.
Ready/produced quantities remain counted. There is no silent delete-history action.

## Pickup countdown

Default delay: **30 seconds**, configurable in Business Settings (1–3600 seconds).
The timer starts after customer pickup acceptance. The saved deadline is frozen;
subsequent settings changes affect only future countdowns.

A Streamlit fragment checks once per second while a browser session is active.
There is no blocking 30-second sleep and no model call. Overdue orders are settled
on the next active refresh or app visit. This demo does not promise an always-on
background worker when the host has suspended the app.

## Where AI is used

- **Order Interpreter:** one bounded request per submitted plain-text order or
  follow-up, producing validated partial details. No automatic acceptance.
- **Fulfillment Agent:** one optional request when the owner clicks **Get AI
  fulfillment advice**. It consumes the interpreted order, fresh Python checks,
  validated alternatives, and lexically retrieved policy. It returns advice and
  a customer draft; it cannot mutate orders.
- **Python:** stock, prices, required-field checks, status transitions, questions
  for standard missing fields, notifications, countdown, history, and exports.

CrewAI Flow routes the explicit action to the relevant specialist; stored order
data is their handoff. Ordinary orders need **one request across the lifecycle**.
Repeated free-text amendments can use additional requests; structured forms use
zero. There is no separate reply-writing call, manager agent, planning loop,
delegation, automatic retries, embedding service, or LLM call on page refresh.

Groq strict structured outputs constrain JSON shape; Python still checks business
meaning. Unknown products, invalid dates, unsupported alternatives, and invented
policy references are rejected. AI explanations remain drafts for owner review.

## Usage controls

- Hard ceiling: two requests per explicit action; current action routes use one.
- Default browser session ceiling: 20, set by `MAX_SESSION_CALLS`.
- Default shared workspace ceiling: 100, set by `MAX_WORKSPACE_CALLS`.
- Default per-order ceiling: 6, configurable in Business Settings.
- Successful results are cached within a browser session. Relative dates are
  anchored to the captured submission timestamp, including manual retries.
- Failed request attempts count. Token totals are recorded when Groq reports them.
- Clearing boards and restoring a backup preserve the existing workspace counters.
  Browser counters reset on a new browser session, while the shared SQLite ceiling
  continues until the runtime database is replaced.
- Optional `LIVE_AI_ACCESS_CODE` in secrets gates AI actions for a public demo.

## Shared storage and demo boundaries

One SQLite database is shared by sessions on the **same app server**, so customer,
business, and history views also synchronize across browsers. Reopening a browser
does not by itself erase the server database. Local hosted storage can be lost
when the server is replaced or the app is redeployed; it is not permanent storage.
Download a JSON backup from Order History and restore it in Business Settings.
Backup restore replaces orders, history, and settings after an explicit UI confirmation.

This is an intentionally open role-switching demo. Names are profiles, not secure
logins. It does not send WhatsApp/SMS/email messages, take payment, verify physical
collection, or support several server replicas. Customer pickup acceptance is a
demo acknowledgment, not proof of delivery. Production would need authentication,
a durable shared database, and an independent background worker.

## Project structure

- `app.py` — four UI views, forms, live refresh, and explicit action handling.
- `chat2order/models.py` — schemas and lifecycle invariants.
- `chat2order/store.py` — transactional shared queue, audit, quotas, backups.
- `chat2order/rules.py` — stock, lead time, pickup window, feasible alternatives.
- `chat2order/workflow.py` — conditional CrewAI Flow and two specialists.
- `chat2order/agents.py` — bounded Groq SDK bridge, strict JSON, caching.
- `chat2order/retrieval.py` — small policy retrieval without embeddings.
- `assets/styles.css` — retained design plus queue presentation.
- `requirements.lock.txt` — compatible dependency set for Python 3.12/Linux.
- `QUICK_DEMO.md` — guided presentation, including an entirely zero-call sample flow.
- `scripts/smoke_check.py` and `scripts/ui_check.py` — focused offline checks.

## References checked

- [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)
- [CrewAI custom LLM interface](https://docs.crewai.com/en/learn/custom-llm)
- [Groq structured outputs and supported models](https://console.groq.com/docs/structured-outputs)
- [Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)
- [Streamlit deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
- [Streamlit secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

See `DEPENDENCIES.md` and `VERIFICATION.md` for the verified environment and actual
checks. No real Groq request was made to verify this package.
