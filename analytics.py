import io
from datetime import date, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sheets

# Google Sheets' date serial epoch (day 0 = 30 Dec 1899).
_SHEETS_EPOCH = date(1899, 12, 30)

_PERIOD_LABELS = {"d": "day", "w": "week", "m": "month", "y": "year"}

# Fixed categorical hues (validated palette, see dataviz skill), assigned in a
# stable order so a category keeps the same color across every chart. Categories
# past the 7th real slice fold into "Other" rather than generating a new hue.
_PALETTE_ORDER = [
    "FNB", "Transport", "Shopping", "Entertainment",
    "Subscription", "Travel", "Alcohol", "Misc",
]
_PALETTE_HEX = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
_CATEGORY_COLOR = dict(zip(_PALETTE_ORDER, _PALETTE_HEX))
_OTHER_COLOR = "#898781"
_MAX_SLICES = 7

_SURFACE = "#fcfcfb"
_INK_PRIMARY = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_GRIDLINE = "#e1e0d9"

# Sequential blue ramp (single-hue, for the trend line + its area fill).
_TREND_LINE = "#256abf"
_TREND_FILL = "#cde2fb"

_TREND_SPAN = {"d": (7, "last 7 days"), "w": (4, "last 4 weeks"), "m": (3, "last 3 months")}

# Labels for the detailed previous-vs-current breakdown, shown only for d/w/m
# (not y — the user asked for this on d/w/m specifically).
_COMPARE_LABELS = {
    "d": ("Yesterday", "Today"),
    "w": ("Last full week", "Week to date"),
    "m": ("Last month", "Month to date"),
}


def _serial_to_date(serial) -> date:
    return _SHEETS_EPOCH + timedelta(days=int(serial))


def fetch_expenses() -> list:
    expenses = []
    for row in sheets.get_out_rows():
        if len(row) < 4 or row[0] == "":
            continue
        try:
            expenses.append(
                {
                    "date": _serial_to_date(row[0]),
                    "item": str(row[1]),
                    "price": float(row[2]),
                    "category": str(row[3]),
                }
            )
        except (ValueError, TypeError):
            continue
    return expenses


def period_bounds(kind: str, today: date):
    if kind == "d":
        start = today
    elif kind == "w":
        start = today - timedelta(days=today.weekday())  # Monday
    elif kind == "m":
        start = today.replace(day=1)
    elif kind == "y":
        start = today.replace(month=1, day=1)
    else:
        raise ValueError(f"unknown period kind: {kind}")
    return start, today


def _add_months(d: date, delta: int) -> date:
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _previous_bounds(kind: str, start: date, end: date):
    """The full immediately-preceding period — yesterday, last full week (Mon-Sun),
    last full calendar month, or same date range last year — used as the
    comparison baseline, regardless of how much of the current period has
    elapsed. This deliberately compares "so far" against a complete period."""
    if kind == "d":
        prev = start - timedelta(days=1)
        return prev, prev

    if kind == "w":
        prev_end = start - timedelta(days=1)  # Sunday before this week's Monday
        prev_start = prev_end - timedelta(days=6)
        return prev_start, prev_end

    if kind == "m":
        prev_start = _add_months(start, -1)
        prev_end = start - timedelta(days=1)  # last day of previous month
        return prev_start, prev_end

    # kind == "y"
    prev_start = start.replace(year=start.year - 1)
    try:
        prev_end = end.replace(year=end.year - 1)
    except ValueError:  # 29 Feb with no leap year prior
        prev_end = end.replace(year=end.year - 1, day=28)
    return prev_start, prev_end


def _filter(expenses: list, start: date, end: date) -> list:
    return [e for e in expenses if start <= e["date"] <= end]


def has_investment_this_month(today: date) -> bool:
    start, end = period_bounds("m", today)
    filtered = _filter(fetch_expenses(), start, end)
    return any(e["category"] == "Investment" for e in filtered)


def _totals_by_category(filtered: list) -> dict:
    totals = {}
    for e in filtered:
        totals[e["category"]] = totals.get(e["category"], 0.0) + e["price"]
    return totals


def _period_label(kind: str, start: date, end: date) -> str:
    if kind == "d":
        return end.strftime("%d %b %Y")
    if kind == "w":
        return f"{start.strftime('%d %b')} - {end.strftime('%d %b %Y')}"
    if kind == "m":
        return f"{end.strftime('%B %Y')} (to date)"
    return f"{end.year} (year to date)"


def build_insights(kind: str, filtered: list, totals: dict, prev_filtered: list, start: date, end: date) -> str:
    label = _period_label(kind, start, end)
    total = sum(totals.values())
    prev_total = sum(e["price"] for e in prev_filtered)
    lines = [f"Summary for {label}"]

    if not filtered and not prev_filtered:
        lines.append("No expenses logged for this period or the previous one.")
        return "\n".join(lines)

    lines.append(f"Total: ${total:,.2f} across {len(filtered)} transaction{'s' if len(filtered) != 1 else ''}")

    compare = _COMPARE_LABELS.get(kind)
    if compare:
        prev_label, cur_label = compare
        lines.append(f"{prev_label}: ${prev_total:,.2f}  |  {cur_label}: ${total:,.2f}")

    if prev_total > 0:
        pct = (total - prev_total) / prev_total * 100
        direction = "up" if pct >= 0 else "down"
        lines.append(f"Change: {direction} {abs(pct):.0f}%")
    elif total > 0 and not compare:
        lines.append(f"No spending in the previous {_PERIOD_LABELS[kind]} to compare against")

    if filtered:
        top_category, top_amount = max(totals.items(), key=lambda kv: kv[1])
        lines.append(f"Top category: {top_category} - ${top_amount:,.2f} ({top_amount / total * 100:.0f}% of total)")

        biggest = max(filtered, key=lambda e: e["price"])
        lines.append(
            f"Biggest expense: {biggest['item']} - ${biggest['price']:,.2f} "
            f"({biggest['category']}, {biggest['date'].strftime('%d %b')})"
        )

        if kind != "d":
            days_elapsed = (end - start).days + 1
            lines.append(f"Daily average: ${total / days_elapsed:,.2f}")

    if compare:
        prev_totals = _totals_by_category(prev_filtered)
        all_categories = sorted(set(totals) | set(prev_totals), key=lambda c: totals.get(c, 0), reverse=True)
        if all_categories:
            prev_label, cur_label = compare
            lines.append(f"\nBy category ({cur_label.lower()} vs {prev_label.lower()}):")
            for cat in all_categories:
                lines.append(f"- {cat}: ${totals.get(cat, 0.0):,.2f} (was ${prev_totals.get(cat, 0.0):,.2f})")

    return "\n".join(lines)


def render_pie_chart(totals: dict, kind: str, start: date, end: date) -> io.BytesIO:
    items = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    if len(items) > _MAX_SLICES:
        head, tail = items[:_MAX_SLICES], items[_MAX_SLICES:]
        head.append(("Other", sum(v for _, v in tail)))
        items = head

    labels = [name for name, _ in items]
    values = [v for _, v in items]
    colors = [_CATEGORY_COLOR.get(name, _OTHER_COLOR) for name in labels]
    total = sum(values)

    fig, ax = plt.subplots(figsize=(6, 6), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    def _autopct(pct):
        return f"${pct / 100 * total:,.2f}\n({pct:.0f}%)"

    wedges, texts, autotexts = ax.pie(
        values,
        colors=colors,
        startangle=90,
        counterclock=False,
        autopct=_autopct,
        pctdistance=0.75,
        wedgeprops={"linewidth": 2, "edgecolor": _SURFACE},
        textprops={"fontsize": 9, "color": _INK_PRIMARY},
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontsize(8)

    ax.legend(
        wedges,
        labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        labelcolor=_INK_PRIMARY,
    )
    ax.set_title(f"Expenses by category — {_period_label(kind, start, end)}", color=_INK_PRIMARY, fontsize=12, pad=16)
    ax.axis("equal")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=_SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf


def _trend_buckets(kind: str, today: date):
    """Returns oldest-to-newest list of (start, end, label) buckets for the trend chart."""
    count, _ = _TREND_SPAN[kind]
    buckets = []

    if kind == "d":
        for i in range(count - 1, -1, -1):
            day = today - timedelta(days=i)
            buckets.append((day, day, day.strftime("%d %b")))

    elif kind == "w":
        cur_monday = today - timedelta(days=today.weekday())
        for i in range(count - 1, -1, -1):
            wk_start = cur_monday - timedelta(weeks=i)
            wk_end = today if i == 0 else wk_start + timedelta(days=6)
            buckets.append((wk_start, wk_end, wk_start.strftime("%d %b")))

    elif kind == "m":
        cur_month_start = today.replace(day=1)
        for i in range(count - 1, -1, -1):
            m_start = _add_months(cur_month_start, -i)
            m_end = today if i == 0 else _add_months(m_start, 1) - timedelta(days=1)
            buckets.append((m_start, m_end, m_start.strftime("%b %Y")))

    return buckets


def render_trend_chart(kind: str, expenses: list, today: date):
    if kind not in _TREND_SPAN:
        return None

    _, span_label = _TREND_SPAN[kind]
    buckets = _trend_buckets(kind, today)
    labels = [b[2] for b in buckets]
    totals = [sum(e["price"] for e in expenses if b[0] <= e["date"] <= b[1]) for b in buckets]

    # Kept brief by design: a compact sparkline-style chart, no per-point value
    # labels — the detailed numbers already live in the insights text.
    fig, ax = plt.subplots(figsize=(5, 2), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    x = range(len(labels))
    ax.plot(x, totals, color=_TREND_LINE, linewidth=2, marker="o", markersize=4, zorder=3)
    ax.fill_between(x, totals, color=_TREND_FILL, alpha=0.6, zorder=1)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, color=_INK_SECONDARY, fontsize=8)
    ax.set_ylim(bottom=0)
    ax.set_yticks([])
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_GRIDLINE)
    ax.tick_params(axis="both", length=0)
    ax.set_title(f"Trend — {span_label}", color=_INK_PRIMARY, fontsize=10, pad=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=_SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf


def render_spending_by_month(option: str, today: date):
    """Stacked bar chart of category spend per month for 'last'/'this'/'next' year or 'all' time.

    Returns None if there's no expense data in the chosen range.
    """
    expenses = fetch_expenses()

    if option == "all":
        if not expenses:
            return None
        start_month = min(e["date"] for e in expenses).replace(day=1)
        end_month = max(max(e["date"] for e in expenses), today).replace(day=1)
        title = "Spending by category — all time"
    else:
        year = {"last": today.year - 1, "this": today.year, "next": today.year + 1}[option]
        start_month = date(year, 1, 1)
        end_month = date(year, 12, 1)
        title = f"Spending by category — {year}"

    months = []
    cur = start_month
    while cur <= end_month:
        months.append(cur)
        cur = _add_months(cur, 1)

    window_end = _add_months(months[-1], 1) - timedelta(days=1)
    windowed = _filter(expenses, months[0], window_end)
    totals_overall = _totals_by_category(windowed)
    if not totals_overall:
        return None

    ranked = sorted(totals_overall.items(), key=lambda kv: kv[1], reverse=True)
    top_cats = [c for c, _ in ranked[:_MAX_SLICES]]
    has_other = len(ranked) > _MAX_SLICES

    series = {cat: [] for cat in top_cats}
    if has_other:
        series["Other"] = []

    for m_start in months:
        m_end = _add_months(m_start, 1) - timedelta(days=1)
        month_totals = _totals_by_category(_filter(expenses, m_start, m_end))
        for cat in top_cats:
            series[cat].append(month_totals.get(cat, 0.0))
        if has_other:
            series["Other"].append(sum(v for c, v in month_totals.items() if c not in top_cats))

    many_months = len(months) > 12
    labels = [m.strftime("%b %y") if many_months else m.strftime("%b") for m in months]

    width = min(20, max(6, len(months) * 0.4))
    fig, ax = plt.subplots(figsize=(width, 5), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    x = range(len(labels))
    bottom = [0.0] * len(labels)
    for cat, amounts in series.items():
        ax.bar(x, amounts, bottom=bottom, color=_CATEGORY_COLOR.get(cat, _OTHER_COLOR), label=cat, width=0.6)
        bottom = [b + v for b, v in zip(bottom, amounts)]

    ax.set_xticks(list(x))
    ax.set_xticklabels(
        labels, color=_INK_SECONDARY, fontsize=8, rotation=45 if many_months else 0, ha="right" if many_months else "center"
    )
    ax.tick_params(axis="y", colors=_INK_SECONDARY, labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_GRIDLINE)
    ax.spines["left"].set_color(_GRIDLINE)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, labelcolor=_INK_PRIMARY, fontsize=8)
    ax.set_title(title, color=_INK_PRIMARY, fontsize=12, pad=12)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=_SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf


def build_summary(kind: str, today: date):
    """Returns (pie_chart, trend_chart, insights_text) for the given period kind (d/w/m/y)."""
    expenses = fetch_expenses()
    start, end = period_bounds(kind, today)
    prev_start, prev_end = _previous_bounds(kind, start, end)

    filtered = _filter(expenses, start, end)
    prev_filtered = _filter(expenses, prev_start, prev_end)
    totals = _totals_by_category(filtered)

    insights = build_insights(kind, filtered, totals, prev_filtered, start, end)
    pie_chart = render_pie_chart(totals, kind, start, end) if totals else None
    trend_chart = render_trend_chart(kind, expenses, today)
    return pie_chart, trend_chart, insights
