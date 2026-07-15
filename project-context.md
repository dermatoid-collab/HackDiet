# HackDiet — Project Context

## Repository

- **GitHub**: https://github.com/dermatoid-collab/hackdiet
- **Sito pubblicato**: deployato su PythonAnywhere (URL non incluso in questo file; non è GitHub Pages)
- **README**: non presente nel repository

---

## Albero delle cartelle

```
HackDiet/
├── .github/
│   └── workflows/
│       ├── garmin_sync.yml          # Nightly: Garmin → Dropbox (kcal + sonno/RHR)
│       └── intervals_sync.yml       # Nightly: Intervals.icu → Dropbox (daily brief)
├── static/
│   └── style.css                    # CSS dell'app Flask
├── templates/
│   ├── base.html                    # Layout base con navbar
│   ├── chart.html                   # Pagina Chart Workshop
│   ├── login.html                   # Pagina di login
│   ├── month.html                   # Vista mensile (log + grafici)
│   ├── settings.html                # Pagina impostazioni
│   ├── trend.html                   # Analisi trend
│   └── year.html                    # Vista annuale
├── app.py                           # Applicazione Flask principale
├── garmin_sync_ci.py                # Script CI: Garmin → Dropbox
├── get_dropbox_token.py             # Script one-shot: ottieni refresh token Dropbox
├── import_excel_to_dropbox.py       # Script one-shot: importa kcal da Excel → Dropbox
├── import_xml.py                    # Script one-shot: importa backup XML → hackdiet.db
├── intervals_sync_ci.py             # Script CI: Intervals.icu → Dropbox
├── requirements.txt
├── sync_config.json.example         # Esempio configurazione per sync locale Windows
├── sync_garmin_dropbox.py           # Script locale Windows (alternativa a CI)
└── .gitignore
```

---

## Script Python di sync

### `garmin_sync_ci.py`
Script eseguito da GitHub Actions ogni notte. Legge kcal consumate e dati sonno/RHR da Garmin Connect per gli ultimi `DAYS_BACK` giorni (default 7), fa merge con i dati esistenti su Dropbox e carica i file aggiornati.

**File Dropbox prodotti:**
- `/hackdiet_kcal.json` — `{"YYYY-MM-DD": kcal_int, ...}` (cresce indefinitamente)
- `/hackdiet_sleep.json` — `{"YYYY-MM-DD": {"total": min, "deep": min, "rem": min, "light": min, "rhr": int}, ...}`

**Variabili d'ambiente richieste:** `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `DROPBOX_REFRESH_TOKEN`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`

```python
"""
Script per GitHub Actions: legge credenziali da variabili d'ambiente.
"""

import json
import os
import requests
from datetime import date, timedelta

DROPBOX_KCAL_PATH = "/hackdiet_kcal.json"
DROPBOX_SLEEP_PATH = "/hackdiet_sleep.json"
DAYS_BACK = int(os.environ.get("DAYS_BACK", 7))


def garmin_login():
    from garminconnect import Garmin
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]
    client = Garmin(email, password)
    client.login()
    print(f"Login Garmin riuscito per {email}")
    return client


def download_from_dropbox(token, path):
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path}),
        "Content-Type": "",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/download",
                      headers=headers, data=b"", timeout=30)
    if r.status_code == 200:
        return r.json()
    return {}


def sync_kcal(client):
    end = date.today()
    start = end - timedelta(days=DAYS_BACK)
    kcal_map = {}
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        try:
            stats = client.get_stats(ds)
            kcal = stats.get("consumedKilocalories") if stats else None
            if kcal is not None and int(kcal) > 0:
                kcal_map[ds] = int(kcal)
                print(f"  {ds}: {int(kcal)} kcal")
        except Exception as e:
            print(f"  {ds}: errore kcal - {e}")
        cur += timedelta(days=1)
    return kcal_map


def sync_sleep(client):
    end = date.today()
    start = end - timedelta(days=DAYS_BACK)
    sleep_map = {}
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        try:
            stats = client.get_stats(ds)
            rhr = stats.get("restingHeartRate") if stats else None

            data = client.get_sleep_data(ds)
            entry = {}
            if data and "dailySleepDTO" in data:
                dto = data["dailySleepDTO"]
                deep = dto.get("deepSleepSeconds", 0) or 0
                rem = dto.get("remSleepSeconds", 0) or 0
                light = dto.get("lightSleepSeconds", 0) or 0
                total = dto.get("sleepTimeSeconds", 0) or 0
                if total:
                    entry["total"] = round(total / 60)
                if deep:
                    entry["deep"] = round(deep / 60)
                if rem:
                    entry["rem"] = round(rem / 60)
                if light:
                    entry["light"] = round(light / 60)
            if rhr:
                entry["rhr"] = int(rhr)

            if entry:
                sleep_map[ds] = entry
                print(f"  {ds}: {entry}")
        except Exception as e:
            print(f"  {ds}: errore sonno/rhr - {e}")
        cur += timedelta(days=1)
    return sleep_map


def get_dropbox_access_token():
    r = requests.post(
        "https://api.dropboxapi.com/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["DROPBOX_REFRESH_TOKEN"],
            "client_id": os.environ["DROPBOX_APP_KEY"],
            "client_secret": os.environ["DROPBOX_APP_SECRET"],
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def upload_to_dropbox(token, path, data_map):
    data = json.dumps(data_map).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False}),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/upload",
                      headers=headers, data=data, timeout=30)
    r.raise_for_status()
    print(f"Caricato su Dropbox: {path} ({len(data_map)} giorni)")


def main():
    print(f"=== Garmin → Dropbox CI Sync (ultimi {DAYS_BACK} giorni) ===")
    client = garmin_login()

    print("Sync kcal...")
    kcal_map = sync_kcal(client)
    print(f"Trovati {len(kcal_map)} giorni con kcal")

    print("Sync sonno + RHR...")
    sleep_map = sync_sleep(client)
    print(f"Trovati {len(sleep_map)} giorni con dati sonno/RHR")

    print("Ottengo access token Dropbox...")
    dropbox_token = get_dropbox_access_token()

    # Merge con dati esistenti su Dropbox
    existing_kcal = download_from_dropbox(dropbox_token, DROPBOX_KCAL_PATH)
    existing_sleep = download_from_dropbox(dropbox_token, DROPBOX_SLEEP_PATH)
    merged_kcal = {**existing_kcal, **kcal_map}
    merged_sleep = {**existing_sleep, **sleep_map}

    upload_to_dropbox(dropbox_token, DROPBOX_KCAL_PATH, merged_kcal)
    upload_to_dropbox(dropbox_token, DROPBOX_SLEEP_PATH, merged_sleep)
    print("Fatto!")


if __name__ == "__main__":
    main()
```

---

### `intervals_sync_ci.py`
Script eseguito da GitHub Actions ogni notte. Estrae dati giornalieri da Intervals.icu (attività, wellness, note calendario) e li salva su Dropbox come "daily brief" in un unico JSON indicizzato per data che cresce di giorno in giorno.

**File Dropbox prodotto:**
- `/hackdiet_intervals_brief.json` — `{"YYYY-MM-DD": { ... }, ...}` (struttura sotto)

**Struttura di ogni voce:**
```json
{
  "ride_date": "YYYY-MM-DD",
  "activities": [
    {
      "id": "...", "name": "...", "type": "Ride", "indoor": false,
      "start_time": "...", "elapsed_time_s": 3600, "moving_time_s": 3540,
      "coasting_time_s": 60, "distance_m": 50000, "elevation_gain_m": 400,
      "avg_watts": 210, "np_watts": 225, "ftp": 280, "intensity_factor": 0.80,
      "variability_index": 1.07, "max_watts": 650, "work_kj": 756,
      "work_above_ftp_kj": 45, "calories": 800, "carbs_used_g": 90,
      "tss": 78, "trimp": 95, "efficiency_factor": 1.45, "decoupling_pct": 3.2,
      "avg_hr": 145, "max_hr": 178, "lthr": 162, "avg_cadence": 88,
      "avg_temp_c": 22, "min_temp_c": 18, "max_temp_c": 28,
      "rpe": 7, "feel": 3,
      "power_zone_times_s": [...], "hr_zone_times_s": [...],
      "intervals_summary": [{"label": "...", "duration_s": 300, "avg_watts": 290}]
    }
  ],
  "wellness_yesterday": {"weight": 78.5, "kcal_consumed": 2100},
  "wellness_today": {
    "sleep_secs": 27000, "deep_sleep_min": 78, "rem_sleep_min": 54,
    "resting_hr": 44, "hrv": 68.5, "hrv_sdnn": 52.3,
    "hrv_score": 82, "sleep_quality": 3, "body_battery_min": 15
  },
  "fitness": {"ctl": 72.4, "atl": 65.1, "tsb": 7.3},
  "notes_yesterday": [{"name": "...", "description": "..."}],
  "notes_today": []
}
```

**Variabili d'ambiente richieste:** `INTERVALS_API_KEY`, `INTERVALS_ATHLETE_ID`, `DROPBOX_REFRESH_TOKEN`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`

```python
"""
Script per GitHub Actions: estrae dati giornalieri da Intervals.icu (attività,
wellness, note di calendario) e li salva su Dropbox come brief giornaliero,
in un unico file JSON che cresce di giorno in giorno (indicizzato per data).
"""

import json
import os
import requests
from datetime import date, timedelta
from requests.auth import HTTPBasicAuth

DROPBOX_BRIEF_PATH = "/hackdiet_intervals_brief.json"

INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
INTERVALS_ATHLETE_ID = os.environ["INTERVALS_ATHLETE_ID"]
DAYS_BACK = int(os.environ.get("DAYS_BACK", 1))

API_BASE = "https://intervals.icu/api/v1"
AUTH = HTTPBasicAuth("API_KEY", INTERVALS_API_KEY)


def intervals_get(path, params=None):
    r = requests.get(f"{API_BASE}{path}", auth=AUTH, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_activities(day):
    data = intervals_get(
        f"/athlete/{INTERVALS_ATHLETE_ID}/activities",
        params={"oldest": day.isoformat(), "newest": day.isoformat()},
    )
    return data or []


def get_activity_intervals(activity_id):
    try:
        data = intervals_get(f"/activity/{activity_id}/intervals")
        return data.get("icu_intervals", []) if isinstance(data, dict) else []
    except Exception as e:
        print(f"    errore intervalli attivita {activity_id}: {e}")
        return []


def summarize_activity(a):
    intervals = get_activity_intervals(a["id"])
    interval_summary = [
        {
            "label": iv.get("label"),
            "duration_s": iv.get("moving_time") or iv.get("elapsed_time"),
            "avg_watts": iv.get("average_watts"),
        }
        for iv in intervals
        if iv.get("type") == "WORK" or iv.get("label")
    ]
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "type": a.get("type"),
        "indoor": a.get("trainer"),
        "start_time": a.get("start_date_local"),
        "elapsed_time_s": a.get("elapsed_time"),
        "moving_time_s": a.get("moving_time"),
        "coasting_time_s": a.get("elapsed_time", 0) - a.get("moving_time", 0) if a.get("elapsed_time") and a.get("moving_time") else None,
        "distance_m": a.get("distance"),
        "elevation_gain_m": a.get("total_elevation_gain"),
        "avg_watts": a.get("icu_average_watts"),
        "np_watts": a.get("icu_weighted_avg_watts"),
        "ftp": a.get("icu_ftp"),
        "intensity_factor": a.get("icu_intensity"),
        "variability_index": a.get("icu_variability_index"),
        "max_watts": a.get("icu_max_watts") or a.get("max_watts"),
        "work_kj": a.get("icu_joules") and round(a["icu_joules"] / 1000),
        "work_above_ftp_kj": a.get("icu_joules_above_ftp") and round(a["icu_joules_above_ftp"] / 1000),
        "calories": a.get("calories"),
        "carbs_used_g": a.get("carbs_ingested") or a.get("icu_carbs_used"),
        "tss": a.get("icu_training_load"),
        "trimp": a.get("trimp"),
        "efficiency_factor": a.get("icu_efficiency_factor"),
        "decoupling_pct": a.get("decoupling"),
        "avg_hr": a.get("average_heartrate"),
        "max_hr": a.get("max_heartrate"),
        "lthr": a.get("lthr"),
        "avg_cadence": a.get("average_cadence"),
        "avg_temp_c": a.get("average_temp"),
        "min_temp_c": a.get("min_temp"),
        "max_temp_c": a.get("max_temp"),
        "rpe": a.get("perceived_exertion"),
        "feel": a.get("feel"),
        "power_zone_times_s": a.get("icu_power_zone_times"),
        "hr_zone_times_s": a.get("icu_hr_zone_times"),
        "intervals_summary": interval_summary,
    }


def get_wellness(day):
    try:
        data = intervals_get(f"/athlete/{INTERVALS_ATHLETE_ID}/wellness/{day.isoformat()}")
        return data or {}
    except Exception as e:
        print(f"  errore wellness {day}: {e}")
        return {}


def parse_hm_to_minutes(s):
    if not s:
        return None
    h = 0
    m = 0
    if "h" in s:
        h_part, s = s.split("h")
        h = int(h_part)
    if "m" in s:
        m = int(s.replace("m", "") or 0)
    return h * 60 + m


def get_notes(day):
    data = intervals_get(
        f"/athlete/{INTERVALS_ATHLETE_ID}/events",
        params={"oldest": day.isoformat(), "newest": day.isoformat(), "category": "NOTE"},
    )
    notes = []
    for ev in (data or []):
        if ev.get("category") == "NOTE":
            notes.append({"name": ev.get("name"), "description": ev.get("description")})
    return notes


def build_day_brief(yesterday, today):
    print(f"Attivita di {yesterday}...")
    activities = [summarize_activity(a) for a in get_activities(yesterday)]
    print(f"  trovate {len(activities)} attivita")

    print(f"Wellness {yesterday} (peso/kcal) e {today} (sonno/HRV/RHR)...")
    wellness_yesterday = get_wellness(yesterday)
    wellness_today = get_wellness(today)

    print(f"Note calendario {yesterday} e {today}...")
    notes_yesterday = get_notes(yesterday)
    notes_today = get_notes(today)

    return {
        "ride_date": yesterday.isoformat(),
        "activities": activities,
        "wellness_yesterday": {
            "weight": wellness_yesterday.get("weight"),
            "kcal_consumed": wellness_yesterday.get("kcalConsumed"),
        },
        "wellness_today": {
            "sleep_secs": wellness_today.get("sleepSecs"),
            "deep_sleep_min": parse_hm_to_minutes(wellness_today.get("DeepSleep")),
            "rem_sleep_min": parse_hm_to_minutes(wellness_today.get("REMSleep")),
            "resting_hr": wellness_today.get("restingHR"),
            "hrv": wellness_today.get("hrv"),
            "hrv_sdnn": wellness_today.get("hrvSDNN"),
            "hrv_score": wellness_today.get("HRVScore"),
            "sleep_quality": wellness_today.get("sleepQuality"),
            "body_battery_min": wellness_today.get("BodyBatteryMin"),
        },
        "fitness": {
            "ctl": wellness_today.get("ctl"),
            "atl": wellness_today.get("atl"),
            "tsb": (wellness_today.get("ctl") - wellness_today.get("atl"))
                   if wellness_today.get("ctl") is not None and wellness_today.get("atl") is not None else None,
        },
        "notes_yesterday": notes_yesterday,
        "notes_today": notes_today,
    }


def download_from_dropbox(token, path):
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path}),
        "Content-Type": "",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/download",
                      headers=headers, data=b"", timeout=30)
    if r.status_code == 200:
        return r.json()
    return {}


def get_dropbox_access_token():
    r = requests.post(
        "https://api.dropboxapi.com/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["DROPBOX_REFRESH_TOKEN"],
            "client_id": os.environ["DROPBOX_APP_KEY"],
            "client_secret": os.environ["DROPBOX_APP_SECRET"],
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def upload_to_dropbox(token, path, data_map):
    data = json.dumps(data_map).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False}),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/upload",
                      headers=headers, data=data, timeout=30)
    r.raise_for_status()
    print(f"Caricato su Dropbox: {path} ({len(data_map)} giorni totali)")


def main():
    print(f"=== Intervals.icu Daily Brief -> Dropbox (ultimi {DAYS_BACK} giorni) ===")
    today = date.today()

    print("Ottengo access token Dropbox...")
    dropbox_token = get_dropbox_access_token()
    brief_map = download_from_dropbox(dropbox_token, DROPBOX_BRIEF_PATH)

    cur_today = today
    for _ in range(DAYS_BACK):
        cur_yesterday = cur_today - timedelta(days=1)
        try:
            brief = build_day_brief(cur_yesterday, cur_today)
            brief_map[cur_yesterday.isoformat()] = brief
        except Exception as e:
            print(f"  errore brief {cur_yesterday}: {e}")
        cur_today -= timedelta(days=1)

    upload_to_dropbox(dropbox_token, DROPBOX_BRIEF_PATH, brief_map)
    print("Fatto!")


if __name__ == "__main__":
    main()
```

---

### `get_dropbox_token.py`
Script one-shot interattivo: apre il browser per autorizzare l'app Dropbox e stampa il refresh token da salvare nei GitHub Secrets. Da eseguire una sola volta in locale.

```python
"""
Script one-shot per ottenere il refresh token Dropbox.
Esegui una volta sola, poi salva il refresh token nei GitHub Secrets.
"""

import requests
import urllib.parse
import webbrowser

# Credenziali omesse — usa DROPBOX_APP_KEY / DROPBOX_APP_SECRET

# Step 1: genera URL di autorizzazione e apri browser
# Step 2: scambia il codice per access + refresh token
# Stampa: Access token, Refresh token
```

---

### `import_excel_to_dropbox.py`
Script one-shot: legge kcal da un file Excel (colonne: data, kcal) e le carica su Dropbox facendo merge con i dati esistenti. Richiede `sync_config.json` con il token Dropbox (access token diretto, non refresh token).

```python
"""
Script one-shot: importa kcal da Excel e le carica su Dropbox.
Uso: python import_excel_to_dropbox.py percorso_file.xlsx
"""
# Legge config da sync_config.json["dropbox_token"]
# Merge: Excel ha priorità sui dati esistenti
# Dipendenza aggiuntiva: openpyxl
```

---

### `import_xml.py`
Script one-shot: importa pesate da un backup XML in formato HackDiet originale nel database SQLite locale (`hackdiet.db`) e ricalcola i trend.

```
Uso: python import_xml.py hackdiet_db.xml
```

---

### `sync_garmin_dropbox.py`
Script locale per Windows (alternativa ai CI): sincronizza kcal e sonno da Garmin e carica su Dropbox. Legge configurazione da `sync_config.json` (non incluso nel repo, aggiunto al `.gitignore`). Salva il token Garmin in `garmin_token.json` per evitare login ripetuti.

```
Configurazione: sync_config.json con garmin_email, garmin_password, dropbox_token, days_back, db_path
```

---

## GitHub Actions Workflows

### `.github/workflows/garmin_sync.yml`
**Trigger:** ogni notte alle 22:00 UTC (mezzanotte ora italiana CEST), oppure manualmente con input `days_back`.

```yaml
name: Garmin → Dropbox Sync

on:
  schedule:
    - cron: '0 22 * * *'
  workflow_dispatch:
    inputs:
      days_back:
        description: 'Giorni da scaricare (default 7)'
        required: false
        default: '7'

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install garminconnect requests
      - name: Run sync
        env:
          GARMIN_EMAIL: ${{ secrets.GARMIN_EMAIL }}
          GARMIN_PASSWORD: ${{ secrets.GARMIN_PASSWORD }}
          DROPBOX_REFRESH_TOKEN: ${{ secrets.DROPBOX_REFRESH_TOKEN }}
          DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY }}
          DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET }}
          DAYS_BACK: ${{ github.event.inputs.days_back || '7' }}
        run: python garmin_sync_ci.py
```

---

### `.github/workflows/intervals_sync.yml`
**Trigger:** ogni notte alle 22:30 UTC (00:30 ora italiana CEST, subito dopo il Garmin sync), oppure manualmente con input `days_back`.

```yaml
name: Intervals.icu → Dropbox Daily Brief

on:
  schedule:
    - cron: '30 22 * * *'
  workflow_dispatch:
    inputs:
      days_back:
        description: 'Giorni da elaborare (default 1)'
        required: false
        default: '1'

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install requests
      - name: Run sync
        env:
          INTERVALS_API_KEY: ${{ secrets.INTERVALS_API_KEY }}
          INTERVALS_ATHLETE_ID: ${{ secrets.INTERVALS_ATHLETE_ID }}
          DROPBOX_REFRESH_TOKEN: ${{ secrets.DROPBOX_REFRESH_TOKEN }}
          DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY }}
          DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET }}
          DAYS_BACK: ${{ github.event.inputs.days_back || '1' }}
        run: python intervals_sync_ci.py
```

---

## Applicazione Flask (`app.py`)

App web Flask deployata su PythonAnywhere. Funzionalità principali:

- **Auth**: login con password singola (env `HACKDIET_PASSWORD`), sessione con "ricordami" 30 giorni
- **DB**: SQLite (`hackdiet.db`), tabelle `entries` (date, weight, trend, comment, kcal) e `config`
- **Trend**: exponential moving average con `ALPHA=0.1`, regressione lineare per slope kg/settimana
- **Sync Garmin** (integrato): al login, sync background kcal ultimi 30 giorni via `garminconnect`
- **Sync Dropbox** (integrato): al login, importa `hackdiet_kcal.json` da Dropbox e aggiorna i record `kcal` per le date già presenti nel DB
- **Route principali:**
  - `GET /` → redirect a vista mensile corrente
  - `GET/POST /month/<year>/<month>` → log mensile con grafici peso e kcal
  - `GET /year/<year>` → vista annuale con grafico e tabella mensile
  - `GET/POST /trend` → analisi trend su intervalli fissi e personalizzati
  - `GET/POST /chart` → chart workshop con selezione periodo
  - `GET /settings` → impostazioni Garmin e Dropbox
  - `GET /download/db` → download SQLite
  - `GET /download/xml` → export backup XML compatibile HackDiet originale
  - `GET /demo` → popola 60 giorni di dati casuali per testing

**Costanti chiave:** `ALPHA = 0.1`, `HEIGHT_M = 1.78`, `CALORIES_PER_KG = 7716`

**Variabili d'ambiente:** `SECRET_KEY`, `HACKDIET_PASSWORD`

---

## Frontend (HTML/CSS/JS)

Il frontend esiste ed è server-side rendered con Jinja2. Non è una SPA — ogni pagina è un template HTML.

### `static/style.css`
CSS custom ispirato al look originale di The Hacker's Diet: palette navy (`#000080`) e bianco, font Inter, layout tabellare. Nessun framework CSS.

### `templates/base.html`
Layout base con navbar orizzontale (Log / History / Chart / Trend / Settings / Sign Out). Carica Chart.js 4.4.0 via CDN.

### `templates/month.html`
Vista principale. Tabella mensile con input peso/kcal/commento per ogni giorno. Grafici inline:
- Grafico linea peso + trend (Chart.js, `canvas 640×300`)
- Grafico a barre kcal (Chart.js, `canvas 640×200`, mostrato solo se ci sono dati)
- Submit automatico al cambio peso via `change` event JS

### `templates/year.html`
Grafico annuale peso + trend (`canvas 700×350`) + tabella min/avg/max per mese.

### `templates/chart.html`
Chart Workshop: grafico custom su periodo selezionabile (mese / trimestre / semestre / anno / personalizzato).

### `templates/trend.html`
Tabella analisi trend su intervalli fissi (7/14/30/91/182/365 giorni) + range personalizzato. Mostra kg/settimana, cal/giorno, min/mean/max trend.

### `templates/settings.html`
Form configurazione Dropbox refresh token + pulsante importazione manuale. Download DB e XML.

### `templates/login.html`
Pagina di login con campo password e checkbox "Ricordami".

---

## `requirements.txt`

```
flask>=3.0.0
garminconnect>=0.2.19
```

---

## `.gitignore`

```
__pycache__/
*.pyc
hackdiet.db
*.db
.env
sync_config.json
garmin_token.json
```

---

## File Dropbox prodotti (non nel repo)

| File | Contenuto | Aggiornato da |
|------|-----------|---------------|
| `/hackdiet_kcal.json` | `{"YYYY-MM-DD": kcal_int}` — dal 05/10/2025 ad oggi | `garmin_sync.yml` ogni notte |
| `/hackdiet_sleep.json` | `{"YYYY-MM-DD": {total, deep, rem, light, rhr}}` — dal 05/10/2025 | `garmin_sync.yml` ogni notte |
| `/hackdiet_intervals_brief.json` | `{"YYYY-MM-DD": {activities, wellness, fitness, notes}}` — dal 05/10/2025 | `intervals_sync.yml` ogni notte |

> ⚠️ I file Dropbox **non vengono letti dal sito** (eccetto `hackdiet_kcal.json` che viene importato nel DB). `hackdiet_sleep.json` e `hackdiet_intervals_brief.json` sono pensati per uso esterno (es. prompt AI tipo Claude/ChatGPT).
