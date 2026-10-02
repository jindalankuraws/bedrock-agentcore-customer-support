"""Mock telco REST API for the support-agent demo.

All data is fake and held in memory. Runs on AWS Lambda behind API Gateway
(REST, IAM auth) via Mangum. Each route has an explicit operation_id because
AgentCore Gateway turns operationIds into MCP tool names.
"""

from datetime import date
from typing import Literal
import uuid

from fastapi import FastAPI, HTTPException, Query
from mangum import Mangum
from pydantic import BaseModel

app = FastAPI(title="Mock Telco API", version="1.0.0")

# ---------------------------------------------------------------------------
# Mock data (fake, no real customers)
# ---------------------------------------------------------------------------
PLANS = {
    "VALUE_50": {"plan_id": "VALUE_50", "name": "Value 50", "monthly_price_sgd": 9.90,
                 "data_gb": 50, "talk_mins": 1000, "sms": 1000, "contract_months": 0},
    "LITE_150": {"plan_id": "LITE_150", "name": "Lite 150", "monthly_price_sgd": 14.95,
                 "data_gb": 150, "talk_mins": 1000, "sms": 1000, "contract_months": 0},
    "MAX_1TB": {"plan_id": "MAX_1TB", "name": "Max 1TB", "monthly_price_sgd": 17.95,
                "data_gb": 1000, "talk_mins": 1000, "sms": 1000, "contract_months": 0},
}

ACCOUNTS = {
    "ACC-1001": {"account_id": "ACC-1001", "name": "Alex", "plan_id": "VALUE_50",
                 "data_used_gb": 42.5, "contract_end_date": None},
    "ACC-1002": {"account_id": "ACC-1002", "name": "Jamie", "plan_id": "LITE_150",
                 "data_used_gb": 88.0, "contract_end_date": "2027-03-31"},
}

BILLS = {
    "ACC-1001": [
        {"billing_month": "2026-07", "plan_name": "Value 50",
         "line_items": [{"description": "Value 50 monthly fee", "amount_sgd": 9.90}],
         "total_sgd": 9.90, "due_date": "2026-08-21", "status": "PAID"},
        {"billing_month": "2026-08", "plan_name": "Value 50",
         "line_items": [{"description": "Value 50 monthly fee", "amount_sgd": 9.90},
                        {"description": "Excess data usage (2GB)", "amount_sgd": 6.00}],
         "total_sgd": 15.90, "due_date": "2026-09-21", "status": "UNPAID"},
    ],
    "ACC-1002": [
        {"billing_month": "2026-07", "plan_name": "Lite 150",
         "line_items": [{"description": "Lite 150 monthly fee", "amount_sgd": 14.95},
                        {"description": "Roaming data - Malaysia (1GB)", "amount_sgd": 5.00}],
         "total_sgd": 19.95, "due_date": "2026-08-21", "status": "PAID"},
        {"billing_month": "2026-08", "plan_name": "Lite 150",
         "line_items": [{"description": "Lite 150 monthly fee", "amount_sgd": 14.95},
                        {"description": "Late payment fee", "amount_sgd": 10.00}],
         "total_sgd": 24.95, "due_date": "2026-09-10", "status": "OVERDUE"},
    ],
}

TICKETS: dict[str, dict] = {}

TEAM_BY_CATEGORY = {
    "BILLING_DISPUTE": "Billing Team",
    "FEE_WAIVER": "Billing Team",
    "ACCOUNT_CHANGE": "Account Services",
    "COMPLAINT": "Customer Care Escalations",
    "OTHER": "General Support",
}
SLA_HOURS_BY_PRIORITY = {"HIGH": 4, "MEDIUM": 24, "LOW": 72}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class PlanChangeRequest(BaseModel):
    new_plan_id: str


class TicketRequest(BaseModel):
    account_id: str
    category: Literal["BILLING_DISPUTE", "FEE_WAIVER", "ACCOUNT_CHANGE", "COMPLAINT", "OTHER"]
    summary: str
    priority: Literal["LOW", "MEDIUM", "HIGH"]


def _get_account(account_id: str) -> dict:
    account = ACCOUNTS.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    return account


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/plans", operation_id="get_all_plans",
         summary="List all available mobile plans")
def get_all_plans():
    """Return every mobile plan on offer with price, data, talk time, SMS and contract length."""
    return list(PLANS.values())


@app.get("/accounts/{account_id}/plan", operation_id="get_plan",
         summary="Get a customer's current plan and usage")
def get_plan(account_id: str):
    """Return the customer's current plan details, data used this month and contract end date."""
    account = _get_account(account_id)
    return {
        "account_id": account_id,
        "plan": PLANS[account["plan_id"]],
        "data_used_gb": account["data_used_gb"],
        "contract_end_date": account["contract_end_date"],
    }


@app.get("/accounts/{account_id}/bills", operation_id="get_bill",
         summary="Get a customer's bill")
def get_bill(account_id: str,
             month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$",
                                       description="Billing month YYYY-MM. Defaults to the latest bill.")):
    """Return one bill with line items, total, due date and status (PAID, UNPAID or OVERDUE)."""
    _get_account(account_id)
    bills = BILLS.get(account_id, [])
    if month is None:
        if not bills:
            raise HTTPException(status_code=404, detail="No bills found")
        return {"account_id": account_id, **bills[-1]}
    for bill in bills:
        if bill["billing_month"] == month:
            return {"account_id": account_id, **bill}
    raise HTTPException(status_code=404, detail=f"No bill for {month}")


@app.post("/accounts/{account_id}/plan-change", operation_id="change_plan",
          summary="Change a customer's mobile plan")
def change_plan(account_id: str, body: PlanChangeRequest):
    """Switch the customer to a new plan from the first day of next month. Requires human approval in the agent."""
    account = _get_account(account_id)
    if body.new_plan_id not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan {body.new_plan_id}")
    old_plan_id = account["plan_id"]
    if body.new_plan_id == old_plan_id:
        raise HTTPException(status_code=400, detail="Customer is already on this plan")

    today = date.today()
    effective = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    account["plan_id"] = body.new_plan_id
    return {
        "status": "SUCCESS",
        "account_id": account_id,
        "old_plan_id": old_plan_id,
        "new_plan_id": body.new_plan_id,
        "effective_date": effective.isoformat(),
        "one_time_fee_sgd": 0,
    }


@app.post("/tickets", operation_id="create_ticket",
          summary="Create a support ticket to hand over to a human team")
def create_ticket(body: TicketRequest):
    """Open a ticket for a human team (billing dispute, fee waiver, complaint, etc.) and return its SLA."""
    _get_account(body.account_id)
    ticket = {
        "ticket_id": f"TKT-{uuid.uuid4().hex[:4].upper()}",
        "status": "OPEN",
        "assigned_team": TEAM_BY_CATEGORY[body.category],
        "sla_hours": SLA_HOURS_BY_PRIORITY[body.priority],
        **body.model_dump(),
    }
    TICKETS[ticket["ticket_id"]] = ticket
    return ticket


# Lambda entry point
handler = Mangum(app)
