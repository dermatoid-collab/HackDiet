from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import os
from datetime import date, timedelta, datetime
import calendar

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "hackdiet.db")

ALPHA = 0.1
HEIGHT_M = 1.78  # metres, from account settings


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            weight REAL NOT NULL,
            trend REAL,
            comment TEXT DEFAULT ''
        )
    """)
    # migrate: add comment column if missing
    cols = [r[1] for r in db.execute("PRAGMA table_info(entries)").fetchall()]
    if "comment" not in cols:
        db.execute("ALTER TABLE entries ADD COLUMN comment TEXT DEFAULT ''")
    db.commit()
    db.close()


def recalculate_trends():
    db = get_db()
    rows = db.execute("SELECT date, weight FROM entries ORDER BY date ASC").fetchall()
    trend = None
    for row in rows:
        if trend is None:
            trend = row["weight"]
        else:
            trend = trend + ALPHA * (row["weight"] - trend)
        db.execute("UPDATE entries SET trend=? WHERE date=?", (round(trend, 4), row["date"]))
    db.commit()


def parse_weight(s):
    s = s.strip().replace(",", ".")
    return float(s)


@app.context_processor
def inject_globals():
    return {'current_year': date.today().year, 'active_tab': ''}


@app.route("/")
def index():
    today = date.today()
    return redirect(url_for("month_view", year=today.year, month=today.month))


@app.route("/month/<int:year>/<int:month>", methods=["GET"])
def month_view(year, month):
    db = get_db()
    month_str = f"{year:04d}-{month:02d}"
    rows = db.execute(
        "SELECT date, weight, trend, comment FROM entries WHERE date LIKE ? ORDER BY date ASC",
        (month_str + "-%",)
    ).fetchall()
    entries_by_day = {r["date"]: r for r in rows}

    days_in_month = calendar.monthrange(year, month)[1]
    today = date.today()
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    day_rows = []
    for d in range(1, days_in_month + 1):
        ds = f"{year:04d}-{month:02d}-{d:02d}"
        entry = entries_by_day.get(ds)
        weight = entry["weight"] if entry else None
        trend = entry["trend"] if entry else None
        comment = entry["comment"] if entry else ""
        # var = weight - trend (negative = below trend = good/green)
        var = round(weight - trend, 1) if (weight is not None and trend is not None) else None
        dow = day_names[date(year, month, d).weekday()]
        day_rows.append({
            "day": d,
            "date": ds,
            "weight": weight,
            "trend": trend,
            "var": var,
            "comment": comment or "",
            "dow": dow,
            "is_today": ds == today.isoformat(),
        })

    # chart data
    chart_labels, chart_weight, chart_trend = [], [], []
    for r in day_rows:
        if r["weight"] is not None or r["trend"] is not None:
            chart_labels.append(r["day"])
            chart_weight.append(r["weight"])
            chart_trend.append(round(r["trend"], 1) if r["trend"] else None)

    # stats: weekly delta from last 7 trend entries
    last_entries = db.execute(
        "SELECT trend FROM entries WHERE trend IS NOT NULL ORDER BY date DESC LIMIT 8"
    ).fetchall()
    week_delta = None
    daily_calories = None
    if len(last_entries) >= 7:
        week_delta = round(last_entries[0]["trend"] - last_entries[6]["trend"], 2)
        daily_calories = round(abs(week_delta) * 7700 / 7)

    # BMI from last weight
    last_weight_row = db.execute(
        "SELECT weight FROM entries ORDER BY date DESC LIMIT 1"
    ).fetchone()
    last_bmi = None
    mean_bmi = None
    if last_weight_row:
        last_bmi = round(last_weight_row["weight"] / (HEIGHT_M ** 2), 1)
    month_weights = [r["weight"] for r in day_rows if r["weight"] is not None]
    if month_weights:
        mean_bmi = round((sum(month_weights) / len(month_weights)) / (HEIGHT_M ** 2), 1)

    prev_month = date(year, month, 1) - timedelta(days=1)
    next_month_d = date(year, month, days_in_month) + timedelta(days=1)

    return render_template("month.html",
        active_tab='log',
        year=year, month=month,
        month_name=calendar.month_name[month],
        day_rows=day_rows,
        days_in_month=days_in_month,
        chart_labels=chart_labels,
        chart_weight=chart_weight,
        chart_trend=chart_trend,
        week_delta=week_delta,
        daily_calories=daily_calories,
        last_bmi=last_bmi,
        mean_bmi=mean_bmi,
        today=today.isoformat(),
        prev_year=prev_month.year, prev_month=prev_month.month,
        next_year=next_month_d.year, next_month=next_month_d.month,
    )


@app.route("/month/<int:year>/<int:month>/update", methods=["POST"])
def month_update(year, month):
    db = get_db()
    days_in_month = calendar.monthrange(year, month)[1]
    changed = False
    for d in range(1, days_in_month + 1):
        ds = f"{year:04d}-{month:02d}-{d:02d}"
        w_str = request.form.get(f"w{d}", "").strip().replace(",", ".")
        comment = request.form.get(f"c{d}", "").strip()

        existing = db.execute("SELECT weight FROM entries WHERE date=?", (ds,)).fetchone()

        if w_str:
            try:
                weight = float(w_str)
            except ValueError:
                continue
            db.execute(
                "INSERT INTO entries (date, weight, comment) VALUES (?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET weight=excluded.weight, comment=excluded.comment",
                (ds, weight, comment)
            )
            changed = True
        else:
            if existing:
                db.execute("DELETE FROM entries WHERE date=?", (ds,))
                changed = True

    db.commit()
    if changed:
        recalculate_trends()
    return redirect(url_for("month_view", year=year, month=month))


@app.route("/year/<int:year>")
def year_view(year):
    db = get_db()
    rows = db.execute(
        "SELECT date, weight, trend FROM entries WHERE date LIKE ? ORDER BY date ASC",
        (f"{year:04d}-%",)
    ).fetchall()

    chart_labels = [r["date"][5:] for r in rows]
    chart_weight = [r["weight"] for r in rows]
    chart_trend = [round(r["trend"], 1) if r["trend"] else None for r in rows]

    monthly = {}
    for r in rows:
        m = int(r["date"].split("-")[1])
        if m not in monthly:
            monthly[m] = {"weights": [], "name": calendar.month_abbr[m]}
        if r["weight"] is not None:
            monthly[m]["weights"].append(r["weight"])

    month_summaries = []
    for m in range(1, 13):
        if m in monthly and monthly[m]["weights"]:
            ws = monthly[m]["weights"]
            month_summaries.append({
                "name": monthly[m]["name"],
                "min": round(min(ws), 1),
                "max": round(max(ws), 1),
                "avg": round(sum(ws) / len(ws), 1),
                "count": len(ws),
                "year": year, "month": m,
            })
        else:
            month_summaries.append({
                "name": calendar.month_abbr[m],
                "min": None, "max": None, "avg": None, "count": 0,
                "year": year, "month": m,
            })

    return render_template("year.html",
        active_tab='history',
        year=year,
        chart_labels=chart_labels,
        chart_weight=chart_weight,
        chart_trend=chart_trend,
        month_summaries=month_summaries,
        prev_year=year - 1,
        next_year=year + 1,
    )


@app.route("/trend", methods=["GET", "POST"])
def trend_view():
    db = get_db()
    today = date.today()

    # Get first year in DB
    first_row = db.execute("SELECT MIN(date) as d FROM entries").fetchone()
    first_year = int(first_row["d"][:4]) if first_row["d"] else today.year

    # Custom date range from POST
    custom_from = None
    custom_to = None
    if request.method == "POST":
        try:
            custom_from = date(int(request.form["from_y"]), int(request.form["from_m"]), int(request.form["from_d"]))
            custom_to = date(int(request.form["to_y"]), int(request.form["to_m"]), int(request.form["to_d"]))
        except (KeyError, ValueError):
            pass

    intervals = [
        ("Week", 7),
        ("Fortnight", 14),
        ("Month", 30),
        ("Quarter", 91),
        ("Six months", 182),
        ("Year", 365),
    ]

    stats = []
    for label, n_days in intervals:
        d_from = today - timedelta(days=n_days - 1)
        d_to = today
        rows = db.execute(
            "SELECT trend FROM entries WHERE date >= ? AND date <= ? AND trend IS NOT NULL ORDER BY date ASC",
            (d_from.isoformat(), d_to.isoformat())
        ).fetchall()
        if len(rows) >= 2:
            trends = [r["trend"] for r in rows]
            kg_per_week = round((trends[-1] - trends[0]) / (n_days / 7), 2)
            cal_per_day = int(abs(kg_per_week) * 7700 / 7)
            t_min = round(min(trends), 1)
            t_mean = round(sum(trends) / len(trends), 1)
            t_max = round(max(trends), 1)
            stats.append({
                "label": label,
                "kg_per_week": kg_per_week,
                "cal_per_day": cal_per_day,
                "t_min": t_min,
                "t_mean": t_mean,
                "t_max": t_max,
            })
        else:
            stats.append({
                "label": label,
                "kg_per_week": None,
                "cal_per_day": None,
                "t_min": None,
                "t_mean": None,
                "t_max": None,
            })

    # Custom range stats
    custom_stats = None
    if custom_from and custom_to:
        rows = db.execute(
            "SELECT trend FROM entries WHERE date >= ? AND date <= ? AND trend IS NOT NULL ORDER BY date ASC",
            (custom_from.isoformat(), custom_to.isoformat())
        ).fetchall()
        n_days = (custom_to - custom_from).days + 1
        if len(rows) >= 2 and n_days > 0:
            trends = [r["trend"] for r in rows]
            kg_per_week = round((trends[-1] - trends[0]) / (n_days / 7), 2)
            cal_per_day = int(abs(kg_per_week) * 7700 / 7)
            custom_stats = {
                "kg_per_week": kg_per_week,
                "cal_per_day": cal_per_day,
                "t_min": round(min(trends), 1),
                "t_mean": round(sum(trends) / len(trends), 1),
                "t_max": round(max(trends), 1),
            }

    years = list(range(first_year, today.year + 1))

    return render_template("trend.html",
        active_tab='trend',
        stats=stats,
        custom_stats=custom_stats,
        custom_from=custom_from,
        custom_to=custom_to,
        years=years,
        today=today,
    )


@app.route("/chart", methods=["GET", "POST"])
def chart_view():
    db = get_db()
    today = date.today()

    first_row = db.execute("SELECT MIN(date) as d FROM entries").fetchone()
    first_year = int(first_row["d"][:4]) if first_row["d"] else today.year
    years = list(range(first_year, today.year + 1))

    period = request.form.get("period") or request.args.get("period", "q")

    custom_from = None
    custom_to = None
    if period == "c":
        try:
            custom_from = date(int(request.form["from_y"]), int(request.form["from_m"]), int(request.form["from_d"]))
            custom_to = date(int(request.form["to_y"]), int(request.form["to_m"]), int(request.form["to_d"]))
        except (KeyError, ValueError):
            period = "q"

    period_days = {"m": 30, "q": 91, "h": 182, "y": 365}
    if period in period_days:
        d_from = today - timedelta(days=period_days[period] - 1)
        d_to = today
    elif period == "c" and custom_from and custom_to:
        d_from = custom_from
        d_to = custom_to
    else:
        period = "q"
        d_from = today - timedelta(days=90)
        d_to = today

    rows = db.execute(
        "SELECT date, weight, trend FROM entries WHERE date >= ? AND date <= ? ORDER BY date ASC",
        (d_from.isoformat(), d_to.isoformat())
    ).fetchall()

    chart_labels = [r["date"] for r in rows]
    chart_weight = [r["weight"] for r in rows]
    chart_trend = [round(r["trend"], 1) if r["trend"] else None for r in rows]

    return render_template("chart.html",
        active_tab='chart',
        period=period,
        chart_labels=chart_labels,
        chart_weight=chart_weight,
        chart_trend=chart_trend,
        custom_from=custom_from,
        custom_to=custom_to,
        years=years,
        today=today,
    )


@app.route("/demo")
def demo():
    import random
    db = get_db()
    today = date.today()
    start = today - timedelta(days=59)
    weight = 82.0
    for i in range(60):
        d = start + timedelta(days=i)
        weight += random.uniform(-0.4, 0.3)
        weight = round(weight, 1)
        db.execute(
            "INSERT INTO entries (date, weight) VALUES (?,?) ON CONFLICT(date) DO UPDATE SET weight=excluded.weight",
            (d.isoformat(), weight)
        )
    db.commit()
    recalculate_trends()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)


init_db()
