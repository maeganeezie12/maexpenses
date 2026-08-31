import json
import os
from datetime import date, timedelta

from analytics import CATEGORY_EMOJI, OTHER_EMOJI, fetch_expenses, fetch_income
from config import CATEGORIES

_STATE_FILE = os.path.join(os.path.dirname(__file__), "budget.json")

# Allowance is income landing in the out sheet, not a spend category — it's
# excluded from every budget calculation the same way analytics excludes it
# from "top category" narratives.
_EXCLUDE_FROM_BUDGET = {"Allowance"}
_SPEND_CATEGORIES = [c for c in CATEGORIES if c not in _EXCLUDE_FROM_BUDGET]

_STATUS_OVER = "🚨"
_STATUS_WARN = "⚠️"
_WARN_THRESHOLD = 0.9  # flag a category once 90% of its budget is used


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _add_months(d: date, delta: int) -> date:
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _month_bounds(d: date):
    start = d.replace(day=1)
    end = _add_months(start, 1) - timedelta(days=1)
    return start, end


def _load() -> dict:
    if not os.path.exists(_STATE_FILE):
        return {}
    with open(_STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(state: dict):
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _last_month_category_pcts(today: date) -> dict:
    """Share of spend per category (excl. income) for the calendar month
    before `today`. Falls back to an even split across all spend categories
    if last month has no logged expenses to benchmark against."""
    last_month_start = _add_months(today.replace(day=1), -1)
    last_month_end = today.replace(day=1) - timedelta(days=1)

    totals: dict = {}
    for e in fetch_expenses():
        if last_month_start <= e["date"] <= last_month_end and e["category"] not in _EXCLUDE_FROM_BUDGET:
            totals[e["category"]] = totals.get(e["category"], 0.0) + e["price"]

    total = sum(totals.values())
    if total <= 0:
        return {c: 1 / len(_SPEND_CATEGORIES) for c in _SPEND_CATEGORIES}

    return {cat: amt / total for cat, amt in totals.items()}


def get_last_salary(today: date):
    """Most recent salary-tagged income entry recorded before this month.
    Salary is paid at month-end to fund the month that follows, so this is
    normally last month's entry — but takes whatever's most recent if the
    user hasn't logged one for a while. Returns (amount, date), or None if
    no salary income has ever been logged."""
    cutoff = today.replace(day=1)
    salary_entries = [i for i in fetch_income() if "salary" in i["description"].lower() and i["date"] < cutoff]
    if not salary_entries:
        return None
    latest = max(salary_entries, key=lambda i: i["date"])
    return latest["amount"], latest["date"]


def _current_month_extra_income(today: date) -> float:
    """This month's non-salary income (cashback, gifts, etc. logged via
    /income) — money on top of last month's salary that's also available to
    spend or save this month."""
    start, end = _month_bounds(today)
    return sum(
        i["amount"] for i in fetch_income()
        if start <= i["date"] <= end and "salary" not in i["description"].lower()
    )


def set_budget(save_pct: float, today: date):
    """Persists this month's save percentage and category split (as
    percentages, not dollar amounts) benchmarked off last month's spend.
    Salary and any non-salary income are looked up live every time the
    budget is displayed (see _live_totals) rather than frozen here, so a
    cashback logged mid-month immediately grows the available budget.
    Returns the saved state, or None if no salary income has been logged."""
    salary_info = get_last_salary(today)
    if salary_info is None:
        return None
    salary, salary_date = salary_info

    state = {
        "month": today.strftime("%Y-%m"),
        "salary": salary,
        "salary_date": salary_date.isoformat(),
        "save_pct": save_pct,
        "category_pcts": _last_month_category_pcts(today),
    }
    _save(state)
    return state


def _live_totals(state: dict, today: date) -> dict:
    """Recomputes total income (last month's salary + this month's
    non-salary income so far), then derives the savings target, spending
    budget, and per-category budgets from the stored save % and split."""
    extra_income = _current_month_extra_income(today)
    total_income = state["salary"] + extra_income
    savings_target = total_income * state["save_pct"] / 100
    spending_budget = total_income - savings_target
    category_budgets = {cat: spending_budget * pct for cat, pct in state["category_pcts"].items()}
    return {
        "extra_income": extra_income,
        "total_income": total_income,
        "savings_target": savings_target,
        "spending_budget": spending_budget,
        "category_budgets": category_budgets,
    }


def _month_usage(today: date) -> dict:
    start, end = _month_bounds(today)
    used: dict = {}
    for e in fetch_expenses():
        if start <= e["date"] <= end and e["category"] not in _EXCLUDE_FROM_BUDGET:
            used[e["category"]] = used.get(e["category"], 0.0) + e["price"]
    return used


def build_budget_table(today: date) -> str:
    """Markdown table of budget vs. spent vs. remaining per category for the
    current month, plus an overspend callout if any category has run over."""
    state = _load()
    if state.get("month") != today.strftime("%Y-%m"):
        return (
            "📋 No budget set for this month yet.\n"
            "Use /setbudget <save_percent> to set one, e.g. /setbudget 20\n"
            "(the salary it budgets from is your last recorded salary income in the *in* sheet)"
        )

    totals = _live_totals(state, today)
    category_budgets = totals["category_budgets"]
    used = _month_usage(today)
    all_cats = sorted(set(category_budgets) | set(used), key=lambda c: category_budgets.get(c, 0.0), reverse=True)

    salary_date_note = ""
    if state.get("salary_date"):
        salary_date_note = f" (recorded {date.fromisoformat(state['salary_date']).strftime('%d %b %Y')})"

    income_line = f"💵 Income: ${state['salary']:,.2f} salary{salary_date_note}"
    if totals["extra_income"] > 0:
        income_line += f" + ${totals['extra_income']:,.2f} other = ${totals['total_income']:,.2f}"

    lines = [
        f"📋 *Budget — {today.strftime('%B %Y')}*",
        income_line,
        f"🏦 Savings target: ${totals['savings_target']:,.2f} ({state['save_pct']:.0f}%)",
        f"💼 Spending budget: ${totals['spending_budget']:,.2f}",
        "```",
    ]

    col_header = f"{'Category':<20}{'Budget':>11}{'Used':>11}{'Left':>11}"
    lines += [col_header, "-" * len(col_header)]

    total_budget = 0.0
    total_used = 0.0
    overspent = []

    for cat in all_cats:
        b = category_budgets.get(cat, 0.0)
        u = used.get(cat, 0.0)
        remaining = b - u
        total_budget += b
        total_used += u

        if remaining < 0:
            prefix = f"{_STATUS_OVER} "
            overspent.append((cat, -remaining))
        elif b > 0 and u / b >= _WARN_THRESHOLD:
            prefix = f"{_STATUS_WARN} "
        else:
            prefix = ""

        name = f"{prefix}{CATEGORY_EMOJI.get(cat, OTHER_EMOJI)} {cat}"
        lines.append(f"{name:<20}{_money(b):>11}{_money(u):>11}{_money(remaining):>11}")

    total_remaining = total_budget - total_used
    lines.append("-" * len(col_header))
    lines.append(f"{'💵 Total':<20}{_money(total_budget):>11}{_money(total_used):>11}{_money(total_remaining):>11}")
    lines.append("```")

    if overspent:
        total_overspend = sum(amt for _, amt in overspent)
        over_desc = ", ".join(f"{cat} (+${amt:,.2f})" for cat, amt in overspent)
        lines.append("")
        lines.append(f"🚨 Overspent in: {over_desc}")
        lines.append(
            f"To stay within your ${totals['spending_budget']:,.2f} budget this month, "
            f"you'll need to spend ${total_overspend:,.2f} less across your other categories."
        )
    elif 0 <= total_remaining < total_budget * 0.2:
        lines.append("")
        lines.append(f"⚠️ Only ${total_remaining:,.2f} left in your overall budget this month — pace yourself.")

    return "\n".join(lines)
