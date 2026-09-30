"""One Streamlit AppTest walkthrough using sample data. No text/AI submissions."""
from __future__ import annotations

import os
import sys
from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from chat2order.store import Store


def widget(elements, label):
    return next(w for w in elements if w.label == label)


def check(app):
    assert not app.exception, [e.message for e in app.exception]
    assert not app.error, [e.value for e in app.error]


def nav(app, page):
    widget(app.radio, "Navigation").set_value(page).run()
    check(app)


def main():
    with TemporaryDirectory(prefix="ui-smoke-", dir=ROOT) as folder:
        path = Path(folder) / "orders.sqlite3"
        original_path = os.environ.get("CHAT2ORDER_DB_PATH")
        os.environ["CHAT2ORDER_DB_PATH"] = str(path)
        try:
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            check(app)
            assert widget(app.radio, "Navigation").options == ["User Interface", "Business Interface", "Order History", "Business Settings"]
            assert not any(w.label == "Processing mode" for w in app.radio)
            nav(app, "Business Interface")
            widget(app.button, "Load sample orders").click().run()
            check(app)
            store = Store(path)
            hina = next(o for o in store.snapshot()["orders"] if o.customer == "Hina")
            assert hina.status == "needs_clarification"
            nav(app, "User Interface")
            widget(app.text_input, "Customer name").set_value("Hina").run()
            check(app)
            widget(app.time_input, "Pickup time").set_value(time(15, 0))
            widget(app.checkbox, "These details confirm my standard pickup order.").set_value(True)
            widget(app.button, "Send these details").click().run()
            check(app)
            assert store.get(hina.id).status == "placed"
            nav(app, "Business Interface")
            dani = next(o for o in store.snapshot()["orders"] if o.customer == "Dani")
            widget(app.selectbox, "Review order").set_value(dani.id).run()
            check(app)
            widget(app.button, "Accept order").click().run()
            check(app)
            widget(app.button, "Mark Ready for Pickup").click().run()
            check(app)
            nav(app, "User Interface")
            widget(app.text_input, "Customer name").set_value("Dani").run()
            check(app)
            widget(app.button, "Accept pickup ✓").click().run()
            check(app)
            dani = store.get(dani.id)
            assert dani.pickup_accepted_at and dani.fulfill_due_at
            store.settle_due(datetime.fromisoformat(dani.fulfill_due_at))
            app.run()
            check(app)
            assert not any(w.label == "Accept pickup ✓" for w in app.button)
            nav(app, "Order History")
            assert any(row == "Fulfilled" for row in app.dataframe[0].value["Status"])
            nav(app, "Business Settings")
            assert widget(app.number_input, "Completion delay after pickup acceptance (seconds)").value == 30
            assert store.snapshot()["usage"]["requests"] == 0
            print("PASS: all four views; sample queue → acceptance → ready → user pickup → timed fulfillment → history. Real Groq requests: 0.")
        finally:
            if original_path is None:
                os.environ.pop("CHAT2ORDER_DB_PATH", None)
            else:
                os.environ["CHAT2ORDER_DB_PATH"] = original_path


if __name__ == "__main__":
    main()
