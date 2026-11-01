"""Tool schema definitions exposed to the LLM."""

from app.utils.dates import PERIODS

PERIOD_ENUM = list(PERIODS)
DATE = {"type": "string", "description": 'ISO date "YYYY-MM-DD"'}
TYPE_ENUM = ["expense", "income"]
SORT_ENUM = ["newest", "largest", "oldest"]

AMOUNT = {"type": "number", "description": "Amount in rupees"}
CATEGORY = {"type": "string", "description": "Category name"}
ACCOUNT = {"type": "string", "description": "Account name, e.g. Cash, JazzCash, Bank"}
PERIOD = {"type": "string", "enum": PERIOD_ENUM, "description": "Named period"}
MONTH = {
    "type": "string",
    "description": 'Month as "2026-10", "october", "this_month" or "last_month"',
}
REF = {
    "type": ["integer", "string"],
    "description": 'Transaction id, or "last" for the latest one',
}


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    """Wrap a parameter schema as an OpenAI function tool."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


TOOLS = [
    _tool(
        "add_transactions",
        "Record one or more expenses or incomes the user reports.",
        {
            "items": {
                "type": "array",
                "description": "One entry per expense or income",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": TYPE_ENUM,
                            "description": "Defaults to expense",
                        },
                        "amount": AMOUNT,
                        "category": CATEGORY,
                        "account": ACCOUNT,
                        "description": {"type": "string", "description": "Short note"},
                        "date": DATE,
                    },
                    "required": ["amount", "category"],
                },
            }
        },
        ["items"],
    ),
    _tool(
        "update_transaction",
        "Change fields of an already recorded transaction.",
        {
            "ref": REF,
            "amount": AMOUNT,
            "category": CATEGORY,
            "account": ACCOUNT,
            "description": {"type": "string", "description": "Short note"},
            "date": DATE,
            "type": {"type": "string", "enum": TYPE_ENUM, "description": "Expense or income"},
        },
        ["ref"],
    ),
    _tool(
        "delete_transaction",
        "Delete a recorded transaction.",
        {"ref": REF},
        ["ref"],
    ),
    _tool(
        "undo_last",
        "Delete the most recently recorded transaction.",
        {},
    ),
    _tool(
        "spending_summary",
        "Totals for a period, broken down by category and account.",
        {
            "period": PERIOD,
            "start": DATE,
            "end": DATE,
            "type": {
                "type": "string",
                "enum": TYPE_ENUM,
                "description": "Defaults to expense",
            },
            "category": CATEGORY,
            "account": ACCOUNT,
        },
    ),
    _tool(
        "search_transactions",
        "Find transactions matching text, amount range, category, account or period.",
        {
            "text": {"type": "string", "description": "Words to match in the description"},
            "min_amount": {"type": "number", "description": "Smallest amount to include"},
            "max_amount": {"type": "number", "description": "Largest amount to include"},
            "category": CATEGORY,
            "account": ACCOUNT,
            "type": {"type": "string", "enum": TYPE_ENUM, "description": "Expense or income"},
            "period": PERIOD,
            "start": DATE,
            "end": DATE,
            "sort": {"type": "string", "enum": SORT_ENUM, "description": "Defaults to newest"},
            "limit": {"type": "integer", "description": "How many to return, max 20"},
        },
    ),
    _tool(
        "get_balances",
        "Current balance of every account.",
        {},
    ),
    _tool(
        "set_account_balance",
        "Correct an account's balance to the amount the user states.",
        {"account": ACCOUNT, "balance": {"type": "number", "description": "Correct balance now"}},
        ["account", "balance"],
    ),
    _tool(
        "set_default_account",
        "Make an account the default for new transactions.",
        {"account": ACCOUNT},
        ["account"],
    ),
    _tool(
        "set_budget",
        "Set or change the monthly budget of an expense category.",
        {"category": CATEGORY, "amount": AMOUNT},
        ["category", "amount"],
    ),
    _tool(
        "remove_budget",
        "Remove the budget of an expense category.",
        {"category": CATEGORY},
        ["category"],
    ),
    _tool(
        "budget_status",
        "Budgets with spend and remaining amounts for a month.",
        {"month": MONTH},
    ),
    _tool(
        "compare_months",
        "Compare two months; month_a is the earlier baseline, month_b is compared to it.",
        {"month_a": MONTH, "month_b": MONTH},
    ),
    _tool(
        "get_insights",
        "Spending insights for a month against the same point last month.",
        {"month": MONTH},
    ),
    _tool(
        "add_category",
        "Create a new category.",
        {
            "name": CATEGORY,
            "kind": {"type": "string", "enum": TYPE_ENUM, "description": "Expense or income"},
        },
        ["name", "kind"],
    ),
]

TOOL_NAMES = {tool["function"]["name"] for tool in TOOLS}
