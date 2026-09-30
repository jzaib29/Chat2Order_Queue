# Present Chat2Order in five minutes

## Demo the business workflow without spending any Groq requests

1. Open **Business Interface** and click **Load sample orders**. Four synthetic,
   pre-structured orders appear; this does not call the Interpreter or Groq.
2. Select **Hina**. Her order needs a pickup time and is visible in the business
   queue. Switch to **User Interface**, set Customer name to `Hina`, expand
   **Complete or update details**, select `15:00`, tick the confirmation, and
   click **Send these details**. Status becomes Placed, with zero AI calls.
3. Back in **Business Interface**, accept **Ayesha**'s six chocolate cupcakes.
   Review **Bilal**: his four chocolate cupcakes exceed the remaining two units.
   **Accept order** is disabled. Propose four vanilla cupcakes instead using
   **Suggest a modification**. The alternative is calculated by Python.
4. In **User Interface**, enter `Bilal`. Accept the modification. The order
   becomes Accepted only after a fresh capacity check.
5. In **Business Interface**, mark Bilal's order **Ready for Pickup**. In the
   User Interface as Bilal, click **Accept pickup**. Watch the 30-second countdown.
   The fulfilled order disappears from both active boards, and Order History
   retains the complete timeline.
6. Reject another pending order with a reason. Show the customer notification
   and its history record. Use **Clear active boards** to cancel the remaining
   unfinished orders; show that history and usage remain.
7. Download history CSV, activity CSV, or the workspace JSON backup.

## Show live agent interpretation

1. Configure the Groq key in Streamlit secrets.
2. In **User Interface**, enter a customer name and click **Use an example**.
   The generated date is three days ahead. Click **Place order**: one Interpreter
   request; the order enters the shared queue.
3. Submit a second request that omits a pickup time. Show its Needs clarification
   status in all three views. Complete the time using the structured form: zero
   extra calls. Alternatively, a free-text follow-up uses one Interpreter call.
4. In **Business Interface**, show validated alternatives first. Click **Get AI
   fulfillment advice** only when you want to demonstrate the second agent.
   It uses one request and changes no order status. Review its draft and use the
   proposal form to ask the customer for agreement.

## Explain the architecture

The Interpreter extracts meaning. Python validates and stores the request.
The Fulfillment Agent can then reason over the same order, feasible options,
and retrieved policy. Humans approve commitments. Python performs the queue,
stock checks, status changes, and countdown without additional model calls.

Business Settings lets you change the countdown before the next pickup acceptance.
For a shorter stage demo, use 5 seconds; restore 30 seconds afterward.

Keep the app open while demonstrating automatic completion. Download a backup
before redeploying. All role views are intentionally accessible in this demo.
