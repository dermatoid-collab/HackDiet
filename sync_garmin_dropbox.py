"""
Script locale Windows: sincronizza kcal e sonno da Garmin e carica su Dropbox.
Configurazione: crea un file sync_config.json nella stessa cartella con:
{
    "garmin_email": "tua@email.com",
    "garmin_password": "tuapassword",
    "dropbox_token": "il_tuo_token",
    "days_back": 30,
    "db_path": "H:/hackdiet/hackdiet.db"
}
Il token Garmin viene salvato automaticamente in garmin_token.json dopo il primo login.
"""

import json
import sqlite3
import requests
from datetime import date, timedelta
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "sync_config.json"
TOKEN_FILE = Path(__file__).parent / "garmin_token.json"
DROPBOX_KCAL_PATH = "/hackdiet_kcal.json"
DROPBOX_SLEEP_PATH = "/hackdiet_sleep.json"


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def garmin_login(cfg):
    from garminconnect import Garmin
    email = cfg["garmin_email"]
    password = cfg["garmin_password"]

    if TOKEN_FILE.exists():
        try:
            tokenstore = TOKEN_FILE.read_text()
            client = Garmin(email, password)
            client.garth.loads(tokenstore)
            client.display_name  # test validity
            print("  Login con token salvato — nessuna chiamata di autenticazione")
            return client
        except Exception as e:
            print(f"  Token scaduto ({e}), rifare login completo...")

    client = Garmin(email, password)
    client.login()
    TOKEN_FILE.write_text(client.garth.dumps())
    print("  Login con password riuscito, token salvato")
    return client


def sync_kcal(client, days_back):
    end = date.today()
    start = end - timedelta(days=days_back)
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


def sync_sleep(client, days_back):
    end = date.today()
    start = end - timedelta(days=days_back)
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


def update_local_db(cfg, kcal_map):
    db_path = cfg.get("db_path")
    if not db_path:
        return
    db = sqlite3.connect(db_path)
    for ds, kcal in kcal_map.items():
        existing = db.execute("SELECT id FROM entries WHERE date=?", (ds,)).fetchone()
        if existing:
            db.execute("UPDATE entries SET kcal=? WHERE date=?", (kcal, ds))
    db.commit()
    db.close()
    print(f"Database locale aggiornato: {len(kcal_map)} giorni")


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
    print("=== Sync Garmin → Dropbox ===")
    cfg = load_config()
    days_back = cfg.get("days_back", 30)

    print("Connessione a Garmin...")
    client = garmin_login(cfg)

    print("Sync kcal...")
    kcal_map = sync_kcal(client, days_back)
    print(f"Trovati {len(kcal_map)} giorni con kcal")

    print("Sync sonno...")
    sleep_map = sync_sleep(client, days_back)
    print(f"Trovati {len(sleep_map)} giorni con dati sonno")

    if cfg.get("db_path"):
        update_local_db(cfg, kcal_map)

    token = cfg["dropbox_token"]
    print("Upload su Dropbox...")
    upload_to_dropbox(token, DROPBOX_KCAL_PATH, kcal_map)
    upload_to_dropbox(token, DROPBOX_SLEEP_PATH, sleep_map)
    print("Fatto!")


if __name__ == "__main__":
    main()
