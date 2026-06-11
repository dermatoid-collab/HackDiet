"""
Script per GitHub Actions: legge credenziali da variabili d'ambiente.
"""

import json
import os
import requests
from datetime import date, timedelta

DROPBOX_KCAL_PATH = "/hackdiet_kcal.json"
DROPBOX_SLEEP_PATH = "/hackdiet_sleep.json"
DAYS_BACK = 7


def garmin_login():
    from garminconnect import Garmin
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]
    client = Garmin(email, password)
    client.login()
    print(f"Login Garmin riuscito per {email}")
    return client


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
            data = client.get_sleep_data(ds)
            if data and "dailySleepDTO" in data:
                dto = data["dailySleepDTO"]
                deep = dto.get("deepSleepSeconds", 0)
                rem = dto.get("remSleepSeconds", 0)
                if deep or rem:
                    sleep_map[ds] = {
                        "deep": round(deep / 60),
                        "rem": round(rem / 60),
                    }
                    print(f"  {ds}: deep={round(deep/60)}m rem={round(rem/60)}m")
        except Exception as e:
            print(f"  {ds}: errore sonno - {e}")
        cur += timedelta(days=1)
    return sleep_map


def upload_to_dropbox(path, data_map):
    token = os.environ["DROPBOX_TOKEN"]
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
    print("=== Garmin → Dropbox CI Sync ===")
    client = garmin_login()

    print("Sync kcal...")
    kcal_map = sync_kcal(client)
    print(f"Trovati {len(kcal_map)} giorni con kcal")

    print("Sync sonno...")
    sleep_map = sync_sleep(client)
    print(f"Trovati {len(sleep_map)} giorni con dati sonno")

    upload_to_dropbox(DROPBOX_KCAL_PATH, kcal_map)
    upload_to_dropbox(DROPBOX_SLEEP_PATH, sleep_map)
    print("Fatto!")


if __name__ == "__main__":
    main()
