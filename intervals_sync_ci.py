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
