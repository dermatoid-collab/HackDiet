from flask import Flask, render_template, request, redirect, url_for, g, session
from functools import wraps
import sqlite3
import os
import threading
from datetime import date, timedelta, datetime
import calendar

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")
app.permanent_session_lifetime = timedelta(days=30)
DB_PATH = os.path.join(os.path.dirname(__file__), "hackdiet.db")
PASSWORD = os.environ.get("HACKDIET_PASSWORD", "dermate")

ALPHA = 0.1
HEIGHT_M = 1.78


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


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
    db.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cols = [r[1] for r in db.execute("PRAGMA table_info(entries)").fetchall()]
    if "comment" not in cols:
        db.execute("ALTER TABLE entries ADD COLUMN comment TEXT DEFAULT ''")
    if "kcal" not in cols:
        db.execute("ALTER TABLE entries ADD COLUMN kcal INTEGER")
    db.commit()
    db.close()


CALORIES_PER_KG = 7716


def get_config(key, default=None):
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    db.close()
    return row[0] if row else default


def set_config(key, value):
    db = sqlite3.connect(DB_PATH)
    db.execute("INSERT INTO config (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db.commit()
    db.close()


def garmin_login():
    """Login to Garmin, using saved token if available. Returns client."""
    from garminconnect import Garmin
    email = get_config("garmin_email")
    password = get_config("garmin_password")
    tokenstore = get_config("garmin_token")
    if tokenstore:
        try:
            client = Garmin(email, password, session_timeout=10)
            client.garth.loads(tokenstore)
            client.display_name  # test token validity
            return client
        except Exception:
            pass
    client = Garmin(email, password)
    client.login()
    set_config("garmin_token", client.garth.dumps())
    return client


def garmin_sync(days_back=30):
    email = get_config("garmin_email")
    password = get_config("garmin_password")
    if not email or not password:
        return "Credenziali Garmin non configurate."
    try:
        client = garmin_login()

        end = date.today()
        start = end - timedelta(days=days_back)

        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        updated = 0
        cur = start
        while cur <= end:
            ds = cur.isoformat()
            try:
                stats = client.get_stats(ds)
                kcal = stats.get("consumedKilocalories") if stats else None
                if kcal is not None and int(kcal) > 0:
                    existing = db.execute("SELECT id FROM entries WHERE date=?", (ds,)).fetchone()
                    if existing:
                        db.execute("UPDATE entries SET kcal=? WHERE date=?", (int(kcal), ds))
                        updated += 1
            except Exception:
                pass
            cur += timedelta(days=1)

        db.commit()
        db.close()
        set_config("last_sync", date.today().isoformat())
        set_config("sync_error", "")
        return None, updated
    except Exception as e:
        err = str(e)
        set_config("sync_error", err)
        return err, 0


def garmin_test():
    """Returns diagnostic info as a string."""
    email = get_config("garmin_email")
    if not email:
        return "❌ Credenziali non configurate."
    lines = []
    try:
        lines.append("✅ garminconnect importato")
        tokenstore = get_config("garmin_token")
        lines.append(f"✅ Client creato per {email}")
        client = garmin_login()
        lines.append(f"✅ Login riuscito {'(token)' if tokenstore else '(password)'}")
        today_s = date.today().isoformat()
        try:
            nutrition = client.get_nutrition_day(today_s)
            lines.append(f"✅ get_nutrition_day({today_s}) → {nutrition}")
        except Exception as e:
            lines.append(f"⚠️ get_nutrition_day fallito: {e}")
            try:
                stats = client.get_stats(today_s)
                lines.append(f"✅ get_stats → keys: {list(stats.keys()) if stats else 'vuoto'}")
            except Exception as e2:
                lines.append(f"❌ get_stats fallito: {e2}")
    except Exception as e:
        lines.append(f"❌ Errore: {e}")
    return "\n".join(lines)


def trendfit_slope_per_day(d_from, d_to, db):
    rows = db.execute(
        "SELECT date, trend FROM entries WHERE date >= ? AND date <= ? AND trend IS NOT NULL ORDER BY date ASC",
        (d_from.isoformat(), d_to.isoformat())
    ).fetchall()
    trend_map = {r["date"]: r["trend"] for r in rows}

    n = s1 = s2 = s3 = s4 = 0
    last_trend = None
    cur = d_from
    while cur <= d_to:
        ds = cur.isoformat()
        t = trend_map.get(ds, last_trend)
        if t is not None:
            n += 1
            s1 += n * t
            s2 += n
            s3 += t
            s4 += n * n
            last_trend = t
        cur += timedelta(days=1)

    denom = s4 * n - s2 * s2
    if denom == 0 or n < 2:
        return None
    return (s1 * n - s2 * s3) / denom


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


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            if request.form.get("remember"):
                session.permanent = True
            # Sync Garmin in background on every login
            if get_config("garmin_email"):
                t = threading.Thread(target=_sync_and_store_error, args=(30,), daemon=True)
                t.start()
            # Import kcal from Dropbox in background on every login
            if get_config("dropbox_refresh_token"):
                t2 = threading.Thread(target=_dropbox_import_and_store, daemon=True)
                t2.start()
            return redirect(url_for("index"))
        error = "Password errata."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    today = date.today()
    return redirect(url_for("month_view", year=today.year, month=today.month))


@app.route("/month/<int:year>/<int:month>", methods=["GET"])
@login_required
def month_view(year, month):
    db = get_db()
    month_str = f"{year:04d}-{month:02d}"
    rows = db.execute(
        "SELECT date, weight, trend, comment, kcal FROM entries WHERE date LIKE ? ORDER BY date ASC",
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
        kcal = entry["kcal"] if entry else None
        var = round(weight - trend, 1) if (weight is not None and trend is not None) else None
        dow = day_names[date(year, month, d).weekday()]
        day_rows.append({
            "day": d,
            "date": ds,
            "weight": weight,
            "trend": trend,
            "var": var,
            "comment": comment or "",
            "kcal": kcal,
            "dow": dow,
            "is_today": ds == today.isoformat(),
        })

    chart_labels = [r["day"] for r in day_rows]
    chart_weight = [r["weight"] for r in day_rows]
    chart_trend = [round(r["trend"], 1) if r["trend"] is not None else None for r in day_rows]
    kcal_data = [r["kcal"] for r in day_rows]

    last_entries = db.execute(
        "SELECT trend FROM entries WHERE trend IS NOT NULL ORDER BY date DESC LIMIT 8"
    ).fetchall()
    week_delta = None
    daily_calories = None
    if len(last_entries) >= 7:
        week_delta = round(last_entries[0]["trend"] - last_entries[6]["trend"], 2)
        daily_calories = round(abs(week_delta) / 7 * CALORIES_PER_KG)

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
        kcal_data=kcal_data,
        week_delta=week_delta,
        daily_calories=daily_calories,
        last_bmi=last_bmi,
        mean_bmi=mean_bmi,
        today=today.isoformat(),
        prev_year=prev_month.year, prev_month=prev_month.month,
        next_year=next_month_d.year, next_month=next_month_d.month,
    )


@app.route("/month/<int:year>/<int:month>/update", methods=["POST"])
@login_required
def month_update(year, month):
    db = get_db()
    days_in_month = calendar.monthrange(year, month)[1]
    changed = False
    for d in range(1, days_in_month + 1):
        ds = f"{year:04d}-{month:02d}-{d:02d}"
        w_str = request.form.get(f"w{d}", "").strip().replace(",", ".")
        comment = request.form.get(f"c{d}", "").strip()
        kcal_str = request.form.get(f"k{d}", "").strip()
        kcal = int(kcal_str) if kcal_str.isdigit() else None

        existing = db.execute("SELECT weight FROM entries WHERE date=?", (ds,)).fetchone()

        if w_str:
            try:
                weight = float(w_str)
            except ValueError:
                continue
            db.execute(
                "INSERT INTO entries (date, weight, comment, kcal) VALUES (?,?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET weight=excluded.weight, comment=excluded.comment, kcal=excluded.kcal",
                (ds, weight, comment, kcal)
            )
            changed = True
        else:
            if existing:
                db.execute("DELETE FROM entries WHERE date=?", (ds,))
                changed = True
            elif kcal is not None:
                # Save kcal even without weight
                db.execute(
                    "INSERT INTO entries (date, weight, comment, kcal) VALUES (?,?,?,?) "
                    "ON CONFLICT(date) DO UPDATE SET kcal=excluded.kcal",
                    (ds, 0, comment, kcal)
                )

    db.commit()
    if changed:
        recalculate_trends()
    return redirect(url_for("month_view", year=year, month=month))


@app.route("/year/<int:year>")
@login_required
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
@login_required
def trend_view():
    db = get_db()
    today = date.today()

    first_row = db.execute("SELECT MIN(date) as d FROM entries").fetchone()
    first_year = int(first_row["d"][:4]) if first_row["d"] else today.year

    custom_from = None
    custom_to = None
    if request.method == "POST":
        try:
            custom_from = date(int(request.form["from_y"]), int(request.form["from_m"]), int(request.form["from_d"]))
            custom_to = date(int(request.form["to_y"]), int(request.form["to_m"]), int(request.form["to_d"]))
        except (KeyError, ValueError):
            pass

    intervals = [
        ("Week", 7), ("Fortnight", 14), ("Month", 30),
        ("Quarter", 91), ("Six months", 182), ("Year", 365),
    ]

    stats = []
    for label, n_days in intervals:
        d_from = today - timedelta(days=n_days - 1)
        d_to = today
        slope = trendfit_slope_per_day(d_from, d_to, db)
        if slope is not None:
            kg_per_week = round(slope * 7, 2)
            cal_per_day = int(slope * CALORIES_PER_KG)
            rows = db.execute(
                "SELECT trend FROM entries WHERE date >= ? AND date <= ? AND trend IS NOT NULL",
                (d_from.isoformat(), d_to.isoformat())
            ).fetchall()
            trends = [r["trend"] for r in rows]
            stats.append({"label": label, "kg_per_week": kg_per_week, "cal_per_day": cal_per_day,
                          "t_min": round(min(trends), 1), "t_mean": round(sum(trends)/len(trends), 1), "t_max": round(max(trends), 1)})
        else:
            stats.append({"label": label, "kg_per_week": None, "cal_per_day": None,
                          "t_min": None, "t_mean": None, "t_max": None})

    custom_stats = None
    if custom_from and custom_to:
        slope = trendfit_slope_per_day(custom_from, custom_to, db)
        if slope is not None:
            rows = db.execute(
                "SELECT trend FROM entries WHERE date >= ? AND date <= ? AND trend IS NOT NULL",
                (custom_from.isoformat(), custom_to.isoformat())
            ).fetchall()
            trends = [r["trend"] for r in rows]
            custom_stats = {
                "kg_per_week": round(slope * 7, 2),
                "cal_per_day": int(slope * CALORIES_PER_KG),
                "t_min": round(min(trends), 1),
                "t_mean": round(sum(trends)/len(trends), 1),
                "t_max": round(max(trends), 1),
            }

    years = list(range(first_year, today.year + 1))
    return render_template("trend.html", active_tab='trend', stats=stats,
        custom_stats=custom_stats, custom_from=custom_from, custom_to=custom_to,
        years=years, today=today)


@app.route("/chart", methods=["GET", "POST"])
@login_required
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

    return render_template("chart.html", active_tab='chart', period=period,
        chart_labels=[r["date"] for r in rows],
        chart_weight=[r["weight"] for r in rows],
        chart_trend=[round(r["trend"], 1) if r["trend"] else None for r in rows],
        custom_from=custom_from, custom_to=custom_to, years=years, today=today)


@app.route("/demo")
@login_required
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


@app.route("/settings", methods=["GET"])
@login_required
def settings_view():
    garmin_email = get_config("garmin_email", "")
    last_sync = get_config("last_sync", "Mai")
    sync_error = get_config("sync_error", "")
    dropbox_configured = bool(get_config("dropbox_refresh_token", ""))
    dropbox_last_sync = get_config("dropbox_last_sync", "Mai")
    dropbox_sync_error = get_config("dropbox_sync_error", "")
    return render_template("settings.html", active_tab='settings',
        garmin_email=garmin_email, last_sync=last_sync, sync_error=sync_error,
        dropbox_configured=dropbox_configured, dropbox_last_sync=dropbox_last_sync,
        dropbox_sync_error=dropbox_sync_error)


@app.route("/settings/garmin", methods=["POST"])
@login_required
def settings_garmin_save():
    email = request.form.get("garmin_email", "").strip()
    password = request.form.get("garmin_password", "").strip()
    if email:
        set_config("garmin_email", email)
    if password:
        set_config("garmin_password", password)
    # Trigger full sync (last 365 days) in background
    days = int(request.form.get("days_back", 365))
    t = threading.Thread(target=_sync_and_store_error, args=(days,), daemon=True)
    t.start()
    return redirect(url_for("settings_view"))


@app.route("/settings/garmin/sync", methods=["POST"])
@login_required
def settings_garmin_sync():
    days = int(request.form.get("days_back", 30))
    t = threading.Thread(target=_sync_and_store_error, args=(days,), daemon=True)
    t.start()
    return redirect(url_for("settings_view"))


def _sync_and_store_error(days):
    err, updated = garmin_sync(days)
    set_config("sync_error", f"{err}" if err else f"OK — {updated} giorni aggiornati")


DROPBOX_KCAL_PATH = "/hackdiet_kcal.json"
DROPBOX_APP_KEY = "sln7rdtjj7fg1as"
DROPBOX_APP_SECRET = "08v9t03wvicwxec"


def dropbox_get_access_token():
    import requests as req
    refresh_token = get_config("dropbox_refresh_token")
    if not refresh_token:
        raise Exception("Dropbox refresh token non configurato")
    r = req.post("https://api.dropboxapi.com/oauth2/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": DROPBOX_APP_KEY,
        "client_secret": DROPBOX_APP_SECRET,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def dropbox_download_kcal(token):
    import requests as req
    import json as _json
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": _json.dumps({"path": DROPBOX_KCAL_PATH}),
        "Content-Type": "",
    }
    r = req.post("https://content.dropboxapi.com/2/files/download", headers=headers, data=b"", timeout=30)
    r.raise_for_status()
    return r.json()


def _dropbox_import_and_store(_ignored=None):
    try:
        token = dropbox_get_access_token()
        kcal_map = dropbox_download_kcal(token)
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        updated = 0
        for ds, kcal in kcal_map.items():
            if kcal and int(kcal) > 0:
                existing = db.execute("SELECT id FROM entries WHERE date=?", (ds,)).fetchone()
                if existing:
                    db.execute("UPDATE entries SET kcal=? WHERE date=?", (int(kcal), ds))
                    updated += 1
        db.commit()
        db.close()
        set_config("dropbox_sync_error", f"OK — {updated} giorni aggiornati")
        set_config("dropbox_last_sync", datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception as e:
        set_config("dropbox_sync_error", str(e))


@app.route("/settings/dropbox", methods=["POST"])
@login_required
def settings_dropbox_save():
    token = request.form.get("dropbox_refresh_token", "").strip()
    if token:
        set_config("dropbox_refresh_token", token)
    if get_config("dropbox_refresh_token"):
        t = threading.Thread(target=_dropbox_import_and_store, daemon=True)
        t.start()
    return redirect(url_for("settings_view"))


@app.route("/settings/dropbox/sync", methods=["POST"])
@login_required
def settings_dropbox_sync():
    if get_config("dropbox_refresh_token"):
        t = threading.Thread(target=_dropbox_import_and_store, daemon=True)
        t.start()
    return redirect(url_for("settings_view"))


@app.route("/settings/garmin/test")
@login_required
def settings_garmin_test():
    from flask import Response
    result = garmin_test()
    return Response(f"<pre style='font-size:14px;padding:20px'>{result}</pre>", mimetype="text/html")


@app.route("/download/db")
@login_required
def download_db():
    from flask import send_file
    return send_file(DB_PATH, as_attachment=True, download_name="hackdiet.db")


@app.route("/download/xml")
@login_required
def download_xml():
    from flask import Response
    db = get_db()
    rows = db.execute("SELECT date, weight, comment, kcal FROM entries ORDER BY date ASC").fetchall()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<hackdiet>']
    cur_ym = None
    for r in rows:
        parts = r["date"].split("-")
        ym = (parts[0], parts[1])
        if ym != cur_ym:
            if cur_ym is not None:
                lines.append('  </monthlog>')
            lines.append(f'  <monthlog year="{parts[0]}" month="{parts[1]}">')
            cur_ym = ym
        comment = (r["comment"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        kcal_attr = f' kcal="{r["kcal"]}"' if r["kcal"] is not None else ""
        lines.append(f'    <day day="{parts[2]}" weight="{r["weight"]}" comment="{comment}"{kcal_attr}/>')
    if cur_ym is not None:
        lines.append('  </monthlog>')
    lines.append('</hackdiet>')
    return Response("\n".join(lines), mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=hackdiet_db.xml"})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)


init_db()
