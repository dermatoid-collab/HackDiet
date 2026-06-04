from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import os
from datetime import date, timedelta, datetime
import calendar
import random

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "hackdiet.db")

ALPHA = 0.1  # EMA smoothing factor


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
            trend REAL
        )
    """)
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
        db.execute("UPDATE entries SET trend=? WHERE date=?", (round(trend, 2), row["date"]))
    db.commit()


def parse_weight(s):
    return float(s.replace(",", "."))


@app.route("/")
def index():
    today = date.today()
    return redirect(url_for("month_view", year=today.year, month=today.month))


@app.route("/month/<int:year>/<int:month>")
def month_view(year, month):
    db = get_db()
    # fetch all entries for this month
    month_str = f"{year:04d}-{month:02d}"
    rows = db.execute(
        "SELECT date, weight, trend FROM entries WHERE date LIKE ? ORDER BY date ASC",
        (month_str + "-%",)
    ).fetchall()

    entries_by_day = {r["date"]: r for r in rows}

    # build day list
    days_in_month = calendar.monthrange(year, month)[1]
    today = date.today()

    day_rows = []
    prev_trend = None
    for d in range(1, days_in_month + 1):
        ds = f"{year:04d}-{month:02d}-{d:02d}"
        entry = entries_by_day.get(ds)
        weight = entry["weight"] if entry else None
        trend = entry["trend"] if entry else None
        delta = None
        if trend is not None and prev_trend is not None:
            delta = round(trend - prev_trend, 2)
        if trend is not None:
            prev_trend = trend
        day_rows.append({
            "day": d,
            "date": ds,
            "weight": weight,
            "trend": trend,
            "delta": delta,
            "is_today": ds == today.isoformat(),
        })

    # chart data: only days with data
    chart_days = [r["day"] for r in day_rows if r["weight"] is not None or r["trend"] is not None]
    chart_weight = []
    chart_trend = []
    chart_labels = []
    for r in day_rows:
        if r["weight"] is not None or r["trend"] is not None:
            chart_labels.append(r["day"])
            chart_weight.append(r["weight"])
            chart_trend.append(r["trend"])

    # latest trend value and weekly estimate
    latest_trend = None
    week_delta = None
    all_entries = db.execute("SELECT date, trend FROM entries WHERE trend IS NOT NULL ORDER BY date DESC LIMIT 8").fetchall()
    if all_entries:
        latest_trend = all_entries[0]["trend"]
        if len(all_entries) >= 7:
            week_delta = round(all_entries[0]["trend"] - all_entries[6]["trend"], 2)

    prev_month = date(year, month, 1) - timedelta(days=1)
    next_month_d = date(year, month, days_in_month) + timedelta(days=1)

    return render_template("month.html",
        year=year, month=month,
        month_name=calendar.month_name[month],
        day_rows=day_rows,
        chart_labels=chart_labels,
        chart_weight=chart_weight,
        chart_trend=chart_trend,
        latest_trend=latest_trend,
        week_delta=week_delta,
        today=today.isoformat(),
        prev_year=prev_month.year, prev_month=prev_month.month,
        next_year=next_month_d.year, next_month=next_month_d.month,
    )


@app.route("/entry", methods=["POST"])
def add_entry():
    ds = request.form.get("date", "").strip()
    weight_str = request.form.get("weight", "").strip()
    if not ds or not weight_str:
        return redirect(url_for("index"))
    try:
        weight = parse_weight(weight_str)
        d = datetime.strptime(ds, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("index"))

    db = get_db()
    db.execute(
        "INSERT INTO entries (date, weight) VALUES (?,?) ON CONFLICT(date) DO UPDATE SET weight=excluded.weight",
        (ds, weight)
    )
    db.commit()
    recalculate_trends()
    return redirect(url_for("month_view", year=d.year, month=d.month))


@app.route("/entry/<string:ds>/delete", methods=["POST"])
def delete_entry(ds):
    try:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("index"))
    db = get_db()
    db.execute("DELETE FROM entries WHERE date=?", (ds,))
    db.commit()
    recalculate_trends()
    return redirect(url_for("month_view", year=d.year, month=d.month))


@app.route("/year/<int:year>")
def year_view(year):
    db = get_db()
    rows = db.execute(
        "SELECT date, weight, trend FROM entries WHERE date LIKE ? ORDER BY date ASC",
        (f"{year:04d}-%",)
    ).fetchall()

    chart_labels = [r["date"][5:] for r in rows]  # MM-DD
    chart_weight = [r["weight"] for r in rows]
    chart_trend = [r["trend"] for r in rows]

    # monthly summary
    monthly = {}
    for r in rows:
        m = int(r["date"][5:7])
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
            })
        else:
            month_summaries.append({"name": calendar.month_abbr[m], "min": None, "max": None, "avg": None, "count": 0})

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
