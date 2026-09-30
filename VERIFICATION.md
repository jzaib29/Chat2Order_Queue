# Queue Edition verification — 2026-10-01 (Asia/Karachi)

Environment: Python 3.12 on Linux. Retained pinned direct dependencies:
Streamlit 1.64.0, CrewAI 1.15.23, Groq 1.7.0, Pydantic 2.12.5.
The existing compatible dependency lock was reused; the queue adds no packages.
All four direct imports succeeded. Current Groq documentation lists the default
`openai/gpt-oss-20b` model as supporting strict structured outputs.

## Focused offline checks completed

1. Python source compilation.
2. `scripts/smoke_check.py`: one combined engine scenario covering both CrewAI
   agent routes using deterministic fixtures, missing details, rejection,
   competing capacity, customer consent, original reservation retention, a
   stale-version rejection, an ignored acknowledgment, pickup acceptance,
   a frozen 30-second deadline, timed fulfillment, production capacity retained
   after fulfillment, clear-board history retention, quota retention, JSON
   backup/restore, and CSV formula escaping.
3. `scripts/ui_check.py`: one Streamlit AppTest walkthrough across all four
   views, loading sample orders, completing Hina's missing pickup time through
   structured fields, accepting Dani's order, marking it ready, accepting pickup
   as Dani, advancing the saved deadline without waiting 30 seconds, and checking
   fulfilled history and the default timer setting.

The two check scripts each had an initial pass and a final targeted pass after
updating the deprecated Streamlit width argument and the acknowledgment case.
Final runs passed. No dependency reinstall, paid test loop, browser automation,
or repeated model evaluation was performed.

**Actual Groq API requests during verification: 0.**
The engine check simulates one quota-accounting entry without an HTTP request;
the fixture agent budget remains zero. The UI check's workspace request count
remains zero. Real model response quality, account credentials, and rate limits
must be checked on the user's deployed app with their Groq key.

AppTest verifies rendering and interaction logic rather than taking a pixel
screenshot. The baseline stylesheet is retained with small additions for the
queue, menu, and empty states. Its standard missing-ScriptRunContext warning
occurs outside a live Streamlit server; final runs had no UI exceptions or app
errors, and deprecated width warnings were removed.

## Operational scope

Orders share SQLite on one app server. Refreshes and automatic countdown checks
make no model calls. Hosted local storage is not durable across server replacement
or redeployment; the included backup/restore preserves records when used. Role
switching and customer names are demo features, not secure authentication. There
is no always-on worker when the host suspends the app.

## Reproduce only if needed

From the repository root, in the deployed dependency environment:

```bash
python scripts/smoke_check.py
python scripts/ui_check.py
```

Both scripts use temporary databases under the project directory and clean up
after themselves. Neither submits a real AI request. Running these checks is
optional; deploying the app does not run them automatically.
