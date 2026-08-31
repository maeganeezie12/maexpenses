import io
import json
import os
import textwrap
from datetime import date, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

from analytics import fetch_expenses, fetch_income
from config import CATEGORIES

_STATE_FILE = os.path.join(os.path.dirname(__file__), "budget.json")

# Decorative corner image for the budget chart — loaded once at import time
# rather than per-render since it never changes.
_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "mrkrabs.png")
_LOGO_IMG = mpimg.imread(_LOGO_PATH) if os.path.exists(_LOGO_PATH) else None

# Allowance (money given to parents each month) gets a fixed target carved
# out of income up front — like the savings target — rather than a
# proportional share of the discretionary spending budget, so it's excluded
# from the last-month percentage split the other categories benchmark off.
_EXCLUDE_FROM_PCT_SPLIT = {"Allowance"}
_SPEND_CATEGORIES = [c for c in CATEGORIES if c not in _EXCLUDE_FROM_PCT_SPLIT]

_WARN_THRESHOLD = 0.9  # flag a category once 90% of its budget is used

# Same brand palette as analytics.py's charts (kept local rather than
# imported since these are plain drawing colors, not category-keyed data).
_SURFACE = "#fcfcfb"
_INK_PRIMARY = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_GRIDLINE = "#e1e0d9"
_STATUS_WARN_COLOR = "#e0a030"
_STATUS_OVER_COLOR = "#d03b3b"
_TINT_WARN = "#fdf0d9"
_TINT_OVER = "#fbe2e2"


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _pct_used(used: float, budget: float) -> str:
    if budget > 0:
        return f"{used / budget * 100:.0f}%"
    return "—" if used == 0 else "∞"


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
        if last_month_start <= e["date"] <= last_month_end and e["category"] not in _EXCLUDE_FROM_PCT_SPLIT:
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


def get_last_allowance():
    """The most recent Allowance-category expense logged in the out sheet,
    full stop — unlike salary there's no month-end lag to account for, so
    this just carries forward whatever was last given to parents, even if
    it was logged earlier this same month. Returns (amount, date), or None
    if no Allowance expense has ever been logged."""
    allowance_entries = [e for e in fetch_expenses() if e["category"] == "Allowance"]
    if not allowance_entries:
        return None
    latest = max(allowance_entries, key=lambda e: e["date"])
    return latest["price"], latest["date"]


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
    """Persists this month's save percentage, allowance target, and category
    split (as percentages, not dollar amounts) benchmarked off last month's
    spend. Salary, allowance, and any non-salary income are looked up live
    every time the budget is displayed (see _live_totals) rather than frozen
    here, so a cashback logged mid-month immediately grows the available
    budget. save_pct is also remembered as the standing preference (see
    ensure_budget_for_month) so future months don't need it re-picked.
    Returns the saved state, or None if no salary income has been logged."""
    salary_info = get_last_salary(today)
    if salary_info is None:
        return None
    salary, salary_date = salary_info

    allowance_info = get_last_allowance()
    allowance_target, allowance_date = allowance_info if allowance_info else (0.0, None)

    state = {
        "month": today.strftime("%Y-%m"),
        "salary": salary,
        "salary_date": salary_date.isoformat(),
        "save_pct": save_pct,
        "last_save_pct": save_pct,
        "allowance_target": allowance_target,
        "allowance_date": allowance_date.isoformat() if allowance_date else None,
        "category_pcts": _last_month_category_pcts(today),
    }
    _save(state)
    return state


def ensure_budget_for_month(today: date):
    """Returns this month's budget state, auto-carrying forward the last
    picked save % into a fresh month if one hasn't been explicitly set yet.
    The saved % stays in effect indefinitely until a new one is picked via
    /setbudget. Returns None only if no save % has ever been picked (or
    salary lookup fails), meaning there's truly nothing to show yet."""
    state = _load()
    if state.get("month") == today.strftime("%Y-%m"):
        return state
    last_pct = state.get("last_save_pct")
    if last_pct is None:
        return None
    return set_budget(last_pct, today)


def _live_totals(state: dict, today: date) -> dict:
    """Recomputes total income (last month's salary + this month's
    non-salary income so far), then derives the savings target, the
    allowance target (fixed at last month's given amount), the remaining
    discretionary spending budget, and per-category budgets from the stored
    save % and split. Allowance is carved out up front like savings rather
    than getting a proportional share, and always gets its own budget row."""
    extra_income = _current_month_extra_income(today)
    total_income = state["salary"] + extra_income
    savings_target = total_income * state["save_pct"] / 100
    allowance_target = state.get("allowance_target", 0.0)
    spending_budget = total_income - savings_target - allowance_target
    category_budgets = {cat: spending_budget * pct for cat, pct in state["category_pcts"].items()}
    category_budgets["Allowance"] = allowance_target
    return {
        "extra_income": extra_income,
        "total_income": total_income,
        "savings_target": savings_target,
        "allowance_target": allowance_target,
        "spending_budget": spending_budget,
        "category_budgets": category_budgets,
    }


def _month_usage(today: date) -> dict:
    start, end = _month_bounds(today)
    used: dict = {}
    for e in fetch_expenses():
        if start <= e["date"] <= end:
            used[e["category"]] = used.get(e["category"], 0.0) + e["price"]
    return used


def build_no_budget_message() -> str:
    return (
        "📋 No budget set yet.\n"
        "Use /setbudget and pick a savings % (10/15/20) to set one.\n"
        "(the salary it budgets from is your last recorded salary income in the *in* sheet)"
    )


# Fixed row heights (inches) that make up the image, in top-to-bottom draw
# order — kept as constants so the upfront figure-height calculation and the
# actual drawing loop can't drift out of sync with each other.
_MARGIN_TOP = 0.3
_TITLE_H = 0.42
_INCOME_H = 0.3
_ALLOWANCE_H = 0.3
_SUMMARY_H = 0.32
_GAP_BEFORE_HEADER = 0.16
_COL_HEADER_H = 0.3
_ROW_H = 0.4
_GAP_BEFORE_TOTAL = 0.06
_TOTAL_H = 0.42
_GAP_BEFORE_FOOTER = 0.22
_FOOTER_LINE_H = 0.26
_MARGIN_BOTTOM = 0.25

_FIG_W = 8.8
_COL_CAT_X = 0.35
_COL_BUDGET_X = 4.5
_COL_USED_X = 5.9
_COL_PCT_X = 7.0
_COL_LEFT_X = 8.45


def _text(ax, x, y, s, **kwargs):
    """ax.text, but with literal '$' escaped — matplotlib's mathtext parser
    treats any matched pair of unescaped '$' as a LaTeX math span, and every
    dollar-amount line here has two or more."""
    ax.text(x, y, s.replace("$", "\\$"), **kwargs)


def render_budget_chart(today: date):
    """Image of budget vs. spent vs. remaining (and % used) per category for
    the current month — status-tinted rows for anything over/near budget,
    plus a wrapped overspend callout at the bottom. None if no save % has
    ever been picked or there's nothing to show."""
    state = ensure_budget_for_month(today)
    if state is None:
        return None

    totals = _live_totals(state, today)
    category_budgets = totals["category_budgets"]
    used = _month_usage(today)
    all_cats = sorted(set(category_budgets) | set(used), key=lambda c: used.get(c, 0.0), reverse=True)
    if not all_cats:
        return None

    rows = []
    overspent = []
    for cat in all_cats:
        b = category_budgets.get(cat, 0.0)
        u = used.get(cat, 0.0)
        remaining = b - u
        if remaining < 0:
            status = "over"
            overspent.append((cat, -remaining))
        elif b > 0 and u / b >= _WARN_THRESHOLD:
            status = "warn"
        else:
            status = "ok"
        rows.append((cat, b, u, remaining, status))

    total_budget = sum(r[1] for r in rows)
    total_used = sum(r[2] for r in rows)
    total_remaining = total_budget - total_used

    footer_lines = []
    footer_color = _INK_PRIMARY
    if overspent:
        total_overspend = sum(amt for _, amt in overspent)
        over_desc = ", ".join(f"{cat} (+${amt:,.2f})" for cat, amt in overspent)
        footer_lines = textwrap.wrap(
            f"Overspent in: {over_desc}. To stay within your ${totals['spending_budget']:,.2f} budget "
            f"this month, you'll need to spend ${total_overspend:,.2f} less across your other categories.",
            width=72,
        )
        footer_color = _STATUS_OVER_COLOR
    elif 0 <= total_remaining < total_budget * 0.2:
        footer_lines = [f"Only ${total_remaining:,.2f} left in your overall budget this month — pace yourself."]
        footer_color = _STATUS_WARN_COLOR

    logo_w_in = 1.3
    logo_h_in = 0.0
    logo_area_h = 0.0
    if _LOGO_IMG is not None:
        logo_h_in = logo_w_in * _LOGO_IMG.shape[0] / _LOGO_IMG.shape[1]
        logo_area_h = logo_h_in + 0.15

    n = len(rows)
    fig_h = (
        _MARGIN_TOP + _TITLE_H + _INCOME_H + _ALLOWANCE_H + _SUMMARY_H + _GAP_BEFORE_HEADER + _COL_HEADER_H
        + _ROW_H * n + _GAP_BEFORE_TOTAL + _TOTAL_H
        + (_GAP_BEFORE_FOOTER + _FOOTER_LINE_H * len(footer_lines) if footer_lines else 0)
        + logo_area_h
        + _MARGIN_BOTTOM
    )

    fig = plt.figure(figsize=(_FIG_W, fig_h), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(_SURFACE)
    ax.set_xlim(0, _FIG_W)
    ax.set_ylim(0, fig_h)
    ax.invert_yaxis()
    ax.axis("off")

    y = _MARGIN_TOP
    _text(ax, _COL_CAT_X, y, f"Budget — {today.strftime('%B %Y')}", fontsize=14, fontweight="bold",
            color=_INK_PRIMARY, va="top")
    y += _TITLE_H

    salary_date_note = ""
    if state.get("salary_date"):
        salary_date_note = f" (recorded {date.fromisoformat(state['salary_date']).strftime('%d %b %Y')})"
    income_line = f"Income: ${state['salary']:,.2f} salary{salary_date_note}"
    if totals["extra_income"] > 0:
        income_line += f" + ${totals['extra_income']:,.2f} other = ${totals['total_income']:,.2f}"
    _text(ax, _COL_CAT_X, y, income_line, fontsize=9.5, color=_INK_SECONDARY, va="top")
    y += _INCOME_H

    allowance_date_note = ""
    if state.get("allowance_date"):
        allowance_date_note = f" (last given {date.fromisoformat(state['allowance_date']).strftime('%d %b %Y')})"
    _text(
        ax, _COL_CAT_X, y,
        f"Allowance to parents: ${totals['allowance_target']:,.2f}{allowance_date_note}",
        fontsize=9.5, color=_INK_SECONDARY, va="top",
    )
    y += _ALLOWANCE_H

    _text(ax,
        _COL_CAT_X, y,
        f"Savings target: ${totals['savings_target']:,.2f} ({state['save_pct']:.0f}%)   "
        f"Spending budget: ${totals['spending_budget']:,.2f}",
        fontsize=9.5, color=_INK_SECONDARY, va="top",
    )
    y += _SUMMARY_H + _GAP_BEFORE_HEADER

    for x, label, ha in ((_COL_CAT_X, "Category", "left"), (_COL_BUDGET_X, "Budget", "right"),
                         (_COL_USED_X, "Used", "right"), (_COL_PCT_X, "%", "right"),
                         (_COL_LEFT_X, "Left", "right")):
        _text(ax, x, y, label, fontsize=8.5, fontweight="bold", color=_INK_SECONDARY, va="top", ha=ha)
    y += _COL_HEADER_H
    ax.plot([_COL_CAT_X, _FIG_W - _COL_CAT_X], [y, y], color=_GRIDLINE, linewidth=1)

    for cat, b, u, remaining, status in rows:
        if status == "over":
            ax.add_patch(Rectangle((0, y), _FIG_W, _ROW_H, facecolor=_TINT_OVER, edgecolor="none", zorder=0))
        elif status == "warn":
            ax.add_patch(Rectangle((0, y), _FIG_W, _ROW_H, facecolor=_TINT_WARN, edgecolor="none", zorder=0))

        text_y = y + _ROW_H / 2
        if status != "ok":
            dot_color = _STATUS_OVER_COLOR if status == "over" else _STATUS_WARN_COLOR
            ax.add_patch(Circle((0.14, text_y), 0.055, color=dot_color, zorder=2))

        _text(ax, _COL_CAT_X, text_y, cat, va="center", ha="left", fontsize=9.5, color=_INK_PRIMARY, zorder=2)
        _text(ax, _COL_BUDGET_X, text_y, f"${b:,.2f}", va="center", ha="right", fontsize=9.5,
                color=_INK_SECONDARY, zorder=2)
        _text(ax, _COL_USED_X, text_y, f"${u:,.2f}", va="center", ha="right", fontsize=9.5,
                color=_INK_PRIMARY, zorder=2)
        _text(ax, _COL_PCT_X, text_y, _pct_used(u, b), va="center", ha="right", fontsize=9.5,
                color=_STATUS_OVER_COLOR if remaining < 0 else _INK_SECONDARY,
                fontweight="bold" if remaining < 0 else "normal", zorder=2)
        _text(ax, _COL_LEFT_X, text_y, _money(remaining), va="center", ha="right", fontsize=9.5,
                color=_STATUS_OVER_COLOR if remaining < 0 else _INK_PRIMARY,
                fontweight="bold" if remaining < 0 else "normal", zorder=2)
        y += _ROW_H

    y += _GAP_BEFORE_TOTAL
    ax.plot([_COL_CAT_X, _FIG_W - _COL_CAT_X], [y, y], color=_GRIDLINE, linewidth=1.2)
    text_y = y + _TOTAL_H / 2
    _text(ax, _COL_CAT_X, text_y, "Total", va="center", ha="left", fontsize=10, fontweight="bold", color=_INK_PRIMARY)
    _text(ax, _COL_BUDGET_X, text_y, f"${total_budget:,.2f}", va="center", ha="right", fontsize=10,
            fontweight="bold", color=_INK_PRIMARY)
    _text(ax, _COL_USED_X, text_y, f"${total_used:,.2f}", va="center", ha="right", fontsize=10,
            fontweight="bold", color=_INK_PRIMARY)
    _text(ax, _COL_PCT_X, text_y, _pct_used(total_used, total_budget), va="center", ha="right", fontsize=10,
            fontweight="bold", color=_STATUS_OVER_COLOR if total_remaining < 0 else _INK_PRIMARY)
    _text(ax, _COL_LEFT_X, text_y, _money(total_remaining), va="center", ha="right", fontsize=10, fontweight="bold",
            color=_STATUS_OVER_COLOR if total_remaining < 0 else _INK_PRIMARY)
    y += _TOTAL_H

    if footer_lines:
        y += _GAP_BEFORE_FOOTER
        for line in footer_lines:
            _text(ax, _COL_CAT_X, y, line, fontsize=9, color=footer_color, va="top")
            y += _FOOTER_LINE_H

    if _LOGO_IMG is not None:
        y += 0.15
        x1 = _FIG_W - 0.2
        x0 = x1 - logo_w_in
        ax.imshow(_LOGO_IMG, extent=(x0, x1, y + logo_h_in, y), zorder=1, aspect="auto")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf
