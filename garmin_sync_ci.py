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
