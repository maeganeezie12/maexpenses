import io
from datetime import date, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sheets

# Google Sheets' date serial epoch (day 0 = 30 Dec 1899).
_SHEETS_EPOCH = date(1899, 12, 30)

_PERIOD_LABELS = {"d": "day", "w": "week", "m": "month", "y": "year"}

# Custom brand palette, reordered from the user's original list to clear the
# dataviz skill's CVD/normal-vision adjacency gates (validated via
# scripts/validate_palette.js — the given order failed both hard gates; this
# order passes with wide margin: worst adjacent normal ΔE 37.1, CVD ΔE 20.1).
# Two colors (#00C2FF/#00B8D9 and #0057FF/#7B2CFF) remain too similar for an
# all-slices-at-once comparison regardless of order — a residual risk mitigated
# by every chart already carrying a legend and direct value/category labels.
_PALETTE_ORDER = [
    "FNB", "Transport", "Shopping", "Entertainment",
    "Subscription", "Travel", "Alcohol", "Misc",
    "Allowance", "Investment", "Hobbies", "Gifts",
]
_PALETTE_HEX = [
    "#00B8D9", "#FF1744", "#00C2FF", "#FF5C00",
    "#5B5FFF", "#00D68F", "#0057FF", "#FF9D00",
    "#C000FF", "#00E5FF", "#7B2CFF", "#FFD600",
]
_CATEGORY_COLOR = dict(zip(_PALETTE_ORDER, _PALETTE_HEX))
_OTHER_COLOR = "#898781"  # fallback only, for any category string outside the fixed list

_SURFACE = "#fcfcfb"
_INK_PRIMARY = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_GRIDLINE = "#e1e0d9"

# Single sequential hue for /history's day granularity: hundreds of daily
# buckets can't work as a labeled stacked bar (confirmed by rendering it —
# solid color wall, illegible overlapping labels), so it stays a plain total.
_SEQUENTIAL_HUE = "#2a78d6"

# Status colors (fixed, never themed) for above/below-zero net-income points.
_STATUS_GOOD = "#0ca30c"
_STATUS_CRITICAL = "#d03b3b"

_TREND_SPAN = {"d": (7, "last 7 days"), "w": (4, "last 4 weeks"), "m": (3, "last 3 months")}

# Exact comparison-sentence wording per period kind, filled with the direction
# ("up"/"down") and the absolute percent change.
_COMPARE_TEMPLATES = {
    "d": "Compared to yesterday, spending is {dir} {pct:.0f}%.",
    "w": "At this time last week, your spending this week is {dir} {pct:.0f}%.",
    "m": "At this point last month, your spending this month is {dir} {pct:.0f}%.",
    "y": "Compared with the same period last year, spending is {dir} {pct:.0f}%.",
}
# Matching wording for the no-prior-spend-to-compare case.
_COMPARE_NEW_TEMPLATES = {
    "d": "Compared to yesterday, this is all new spending (${total:,.2f}).",
    "w": "There was no spending at this time last week — this week's ${total:,.2f} is all new.",
    "m": "There was no spending at this point last month — this month's ${total:,.2f} is all new.",
    "y": "Compared with the same period last year, this is all new spending (${total:,.2f}).",
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


def fetch_income() -> list:
    income = []
    for row in sheets.get_in_rows():
        if len(row) < 3 or row[0] == "":
            continue
        try:
            income.append(
                {
                    "date": _serial_to_date(row[0]),
                    "amount": float(row[1]),
                    "description": str(row[2]),
                }
            )
        except (ValueError, TypeError):
            continue
    return income


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
    """The comparison baseline for each period, always on a to-date basis —
    yesterday; the same Mon..weekday range last week (e.g. this Mon-Wed vs
    last Mon-Wed); the same day-of-month range last month (e.g. 1-14 Aug vs
    1-14 Jul); or the same date range last year. Never a full prior period —
    always matched to how much of the current period has actually elapsed."""
    if kind == "d":
        prev = start - timedelta(days=1)
        return prev, prev

    if kind == "w":
        return start - timedelta(days=7), end - timedelta(days=7)

    if kind == "m":
        prev_start = _add_months(start, -1)
        prev_month_start = _add_months(end.replace(day=1), -1)
        last_day_prev_month = (_add_months(prev_month_start, 1) - timedelta(days=1)).day
        prev_end = prev_month_start.replace(day=min(end.day, last_day_prev_month))
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


def _category_change_phrase(cat: str, cur: float, prev: float) -> str:
    if prev == 0 and cur > 0:
        return f"✨ {cat} is new this period"
    if cur == 0 and prev > 0:
        return f"⚠️ {cat} dropped to zero (was ${prev:,.2f})"
    pct = (cur - prev) / prev * 100
    if pct >= 0:
        return f"📈 {cat} increased by {pct:.0f}%"
    return f"📉 {cat} decreased by {abs(pct):.0f}%"


def _join_phrases(phrases: list) -> str:
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return f"{phrases[0]}, {phrases[1]}, and {phrases[2]}"


# Recurring, non-discretionary categories excluded from "top category" and the
# narrative's top-3 callouts — they'd otherwise dominate every period without
# telling the user anything about their actual variable spending.
_EXCLUDE_FROM_TOP = {"Allowance"}


def _ranked_categories(totals: dict) -> list:
    return sorted(
        ((c, v) for c, v in totals.items() if c not in _EXCLUDE_FROM_TOP),
        key=lambda kv: kv[1],
        reverse=True,
    )


def _build_comparison_narrative(kind: str, total: float, prev_total: float, totals: dict, prev_totals: dict):
    if kind not in _COMPARE_TEMPLATES or (total == 0 and prev_total == 0):
        return None

    if prev_total == 0:
        sentence = f"✨ {_COMPARE_NEW_TEMPLATES[kind].format(total=total)}"
    else:
        pct = (total - prev_total) / prev_total * 100
        emoji = "📈" if pct >= 0 else "📉"
        direction = "up" if pct >= 0 else "down"
        sentence = f"{emoji} {_COMPARE_TEMPLATES[kind].format(dir=direction, pct=abs(pct))}"

    top3 = _ranked_categories(totals)[:3]
    phrases = [_category_change_phrase(cat, cur, prev_totals.get(cat, 0.0)) for cat, cur in top3]
    if phrases:
        sentence += f" {_join_phrases(phrases)}."

    return sentence


def build_insights(kind: str, filtered: list, totals: dict, prev_filtered: list, start: date, end: date) -> str:
    label = _period_label(kind, start, end)
    total = sum(totals.values())
    prev_total = sum(e["price"] for e in prev_filtered)
    lines = [f"📊 Summary for {label}"]

    if not filtered and not prev_filtered:
        lines.append("No expenses logged for this period or the previous one.")
        return "\n".join(lines)

    lines.append(f"💰 Total: ${total:,.2f}, {len(filtered)} transaction{'s' if len(filtered) != 1 else ''}")

    ranked = _ranked_categories(totals)
    if ranked:
        top_category, top_amount = ranked[0]
        lines.append(f"🏆 Top category: {top_category} - ${top_amount:,.2f} ({top_amount / total * 100:.0f}% of total)")

    if filtered and kind != "d":
        days_elapsed = (end - start).days + 1
        lines.append(f"📅 Daily average: ${total / days_elapsed:,.2f}")

    narrative = _build_comparison_narrative(kind, total, prev_total, totals, _totals_by_category(prev_filtered))
    if narrative:
        lines.append("")
        lines.append(narrative)

    return "\n".join(lines)


def _palette_ordered(categories) -> list:
    """Order categories by the fixed high-contrast palette sequence — adjacent
    entries in _PALETTE_ORDER were chosen (via the dataviz validator) to
    maximize color distance — rather than by value, so stacked/pie segments
    never place two similar hues next to each other. Legend order doesn't
    need to match value rank; this ordering is used for both."""
    cats = set(categories)
    ordered = [c for c in _PALETTE_ORDER if c in cats]
    ordered += [c for c in cats if c not in _PALETTE_ORDER]  # defensive fallback
    return ordered


def render_pie_chart(totals: dict, kind: str, start: date, end: date) -> io.BytesIO:
    items = [(c, totals[c]) for c in _palette_ordered(totals.keys())]

    labels = [name for name, _ in items]
    values = [v for _, v in items]
    colors = [_CATEGORY_COLOR.get(name, _OTHER_COLOR) for name in labels]
    total = sum(values)

    fig, ax = plt.subplots(figsize=(6, 6), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    def _autopct(pct):
        # Every category still gets its own slice, color, and legend entry —
        # this only skips the inline $/% label on slivers too thin to hold
        # readable text, where it would otherwise overlap its neighbors.
        if pct < 3:
            return ""
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


def _render_stacked_bar_chart(buckets: list, expenses: list, title: str):
    """Stacked bar chart of category spend across the given (start, end, label)
    buckets. Shared by the trend charts (day/week/month buckets) and /spending
    (month buckets). Returns None if there's no data across the whole window.
    """
    labels = [b[2] for b in buckets]
    windowed = _filter(expenses, buckets[0][0], buckets[-1][1])
    totals_overall = _totals_by_category(windowed)
    if not totals_overall:
        return None

    all_cats = _palette_ordered(totals_overall.keys())
    series = {cat: [] for cat in all_cats}

    for b_start, b_end, _ in buckets:
        bucket_totals = _totals_by_category(_filter(expenses, b_start, b_end))
        for cat in all_cats:
            series[cat].append(bucket_totals.get(cat, 0.0))

    many = len(labels) > 12
    width = min(20, max(5, len(labels) * 0.5))
    fig, ax = plt.subplots(figsize=(width, 4), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    x = range(len(labels))
    bottom = [0.0] * len(labels)
    for cat, amounts in series.items():
        ax.bar(x, amounts, bottom=bottom, color=_CATEGORY_COLOR.get(cat, _OTHER_COLOR), label=cat, width=0.6)
        bottom = [b + v for b, v in zip(bottom, amounts)]

    # bottom now holds each bar's grand total (the top of its stack).
    ax.set_ylim(top=max(bottom) * 1.12)
    for xi, bar_total in zip(x, bottom):
        if bar_total > 0:
            ax.annotate(
                f"${bar_total:,.0f}",
                (xi, bar_total),
                textcoords="offset points",
                xytext=(0, 4),
                ha="center",
                fontsize=7,
                color=_INK_PRIMARY,
            )

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, color=_INK_SECONDARY, fontsize=8, rotation=45 if many else 0, ha="right" if many else "center")
    ax.tick_params(axis="y", colors=_INK_SECONDARY, labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_GRIDLINE)
    ax.spines["left"].set_color(_GRIDLINE)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, labelcolor=_INK_PRIMARY, fontsize=7)
    ax.set_title(title, color=_INK_PRIMARY, fontsize=11, pad=10)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=_SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf


def render_trend_chart(kind: str, expenses: list, today: date):
    if kind not in _TREND_SPAN:
        return None
    _, span_label = _TREND_SPAN[kind]
    buckets = _trend_buckets(kind, today)
    return _render_stacked_bar_chart(buckets, expenses, f"Trend by category — {span_label}")


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

    many_months = len(months) > 12
    buckets = [
        (m, _add_months(m, 1) - timedelta(days=1), m.strftime("%b %y") if many_months else m.strftime("%b"))
        for m in months
    ]
    return _render_stacked_bar_chart(buckets, expenses, title)


def _range_buckets(granularity: str, start: date, end: date, spans_years: bool):
    buckets = []

    if granularity == "month":
        cur = start.replace(day=1)
        end_month = end.replace(day=1)
        label_fmt = "%b %y" if spans_years else "%b"
        while cur <= end_month:
            buckets.append((cur, _add_months(cur, 1) - timedelta(days=1), cur.strftime(label_fmt)))
            cur = _add_months(cur, 1)

    elif granularity == "week":
        cur = start - timedelta(days=start.weekday())  # Monday on/before start
        while cur <= end:
            buckets.append((cur, cur + timedelta(days=6), cur.strftime("%d %b %y" if spans_years else "%d %b")))
            cur += timedelta(days=7)

    elif granularity == "day":
        cur = start
        while cur <= end:
            buckets.append((cur, cur, cur.strftime("%d %b")))
            cur += timedelta(days=1)

    else:
        raise ValueError(f"unknown granularity: {granularity}")

    return buckets


def _resolve_year_option(option: str, today: date, all_time_dates: list):
    """Returns (start, end, label, spans_years) for a this/last/next/all picker."""
    if option == "all":
        if not all_time_dates:
            return None
        start = min(all_time_dates).replace(day=1)
        end = max(max(all_time_dates), today)
        return start, end, "all time", start.year != end.year

    year = {"this": today.year, "last": today.year - 1, "next": today.year + 1}[option]
    return date(year, 1, 1), date(year, 12, 31), str(year), False


def render_history_chart(granularity: str, year_option: str, today: date):
    """Chart bucketed by day/week/month, for this/last/next year or all time.
    None if there's no data. Week/month use the same stacked-by-category
    renderer as /spending (with a total above each bar); day stays a plain
    total line — hundreds of daily buckets confirmed unreadable as labeled
    stacked bars (a solid wall of color with overlapping text)."""
    expenses = fetch_expenses()
    resolved = _resolve_year_option(year_option, today, [e["date"] for e in expenses])
    if not resolved:
        return None
    start, end, label, spans_years = resolved
    buckets = _range_buckets(granularity, start, end, spans_years)
    title = f"Total spending by {granularity} — {label}"

    if granularity != "day":
        return _render_stacked_bar_chart(buckets, expenses, title)

    totals = [sum(e["price"] for e in expenses if b[0] <= e["date"] <= b[1]) for b in buckets]
    if not any(totals):
        return None

    fig, ax = plt.subplots(figsize=(max(12, len(buckets) * 0.05), 4), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)
    x = range(len(buckets))
    ax.plot(x, totals, color=_SEQUENTIAL_HUE, linewidth=1.5)
    ax.fill_between(x, totals, color=_SEQUENTIAL_HUE, alpha=0.15)
    month_starts = [i for i, (b_start, _, _) in enumerate(buckets) if b_start.day == 1]
    tick_fmt = "%b %y" if spans_years else "%b"
    ax.set_xticks(month_starts)
    ax.set_xticklabels([buckets[i][0].strftime(tick_fmt) for i in month_starts], color=_INK_SECONDARY, fontsize=8)
    ax.tick_params(axis="y", colors=_INK_SECONDARY, labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_GRIDLINE)
    ax.spines["left"].set_color(_GRIDLINE)
    ax.set_title(title, color=_INK_PRIMARY, fontsize=11, pad=10)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=_SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf


def render_net_income_chart(option: str, today: date):
    """Line chart of net income (in-sheet income minus out-sheet expenses) per
    month, for this/last/next year or all time. None if there's no data."""
    expenses = fetch_expenses()
    income = fetch_income()
    all_dates = [e["date"] for e in expenses] + [i["date"] for i in income]
    resolved = _resolve_year_option(option, today, all_dates)
    if not resolved:
        return None
    start, end, label, spans_years = resolved

    months = []
    cur = start.replace(day=1)
    end_month = end.replace(day=1)
    while cur <= end_month:
        months.append(cur)
        cur = _add_months(cur, 1)

    net_values = []
    for m_start in months:
        m_end = _add_months(m_start, 1) - timedelta(days=1)
        inc_total = sum(i["amount"] for i in income if m_start <= i["date"] <= m_end)
        exp_total = sum(e["price"] for e in expenses if m_start <= e["date"] <= m_end)
        net_values.append(inc_total - exp_total)

    if not any(net_values):
        return None

    labels = [m.strftime("%b %y") if spans_years else m.strftime("%b") for m in months]
    many = len(months) > 12
    width = min(20, max(6, len(months) * 0.4))

    fig, ax = plt.subplots(figsize=(width, 4), dpi=150)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    x = range(len(labels))
    ax.axhline(0, color=_GRIDLINE, linewidth=1, zorder=1)
    ax.plot(x, net_values, color=_INK_SECONDARY, linewidth=1.5, zorder=2)
    point_colors = [_STATUS_GOOD if v >= 0 else _STATUS_CRITICAL for v in net_values]
    ax.scatter(x, net_values, color=point_colors, zorder=3, s=28)

    span = max(net_values) - min(net_values) or 1
    ax.set_ylim(min(net_values) - span * 0.15, max(net_values) + span * 0.15)
    for xi, v in zip(x, net_values):
        ax.annotate(
            f"${v:,.0f}",
            (xi, v),
            textcoords="offset points",
            xytext=(0, 6) if v >= 0 else (0, -6),
            ha="center",
            va="bottom" if v >= 0 else "top",
            fontsize=7,
            color=_INK_PRIMARY,
        )

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, color=_INK_SECONDARY, fontsize=8, rotation=45 if many else 0, ha="right" if many else "center")
    ax.tick_params(axis="y", colors=_INK_SECONDARY, labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_GRIDLINE)
    ax.spines["left"].set_color(_GRIDLINE)
    total_net = sum(net_values)
    ax.set_title(f"Net income by month — {label} (Total: ${total_net:,.0f})", color=_INK_PRIMARY, fontsize=11, pad=10)

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
