"""
Script per GitHub Actions: legge credenziali da variabili d'ambiente.
"""

import json
import os
import requests
from datetime import date, timedelta

DROPBOX_PATH = "/hackdiet_kcal.json"
DAYS_BACK = 60


def garmin_login():
    from garminconnect import Garmin
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]
    client = Garmin(email, password)
    client.login()
    print(f"Login Garmin riuscito per {email}")
    return client


def sync_garmin(client):
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
            print(f"  {ds}: errore - {e}")
        cur += timedelta(days=1)
    return kcal_map


def upload_to_dropbox(kcal_map):
    token = os.environ["DROPBOX_TOKEN"]
    data = json.dumps(kcal_map).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": DROPBOX_PATH, "mode": "overwrite", "autorename": False}),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/upload", headers=headers, data=data, timeout=30)
    r.raise_for_status()
    print(f"Caricato su Dropbox: {len(kcal_map)} giorni")


def main():
    print("=== Garmin → Dropbox CI Sync ===")
    client = garmin_login()
    kcal_map = sync_garmin(client)
    print(f"Trovati {len(kcal_map)} giorni con kcal")
    upload_to_dropbox(kcal_map)
    print("Fatto!")


if __name__ == "__main__":
    main()
