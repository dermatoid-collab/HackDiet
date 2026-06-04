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
        year=year,
        chart_labels=chart_labels,
        chart_weight=chart_weight,
        chart_trend=chart_trend,
        month_summaries=month_summaries,
        prev_year=year - 1,
        next_year=year + 1,
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
