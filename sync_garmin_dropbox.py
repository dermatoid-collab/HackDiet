"""
Script locale Windows: sincronizza kcal da Garmin e carica su Dropbox.
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
DROPBOX_PATH = "/hackdiet_kcal.json"


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def garmin_login(cfg):
    from garminconnect import Garmin
    email = cfg["garmin_email"]
    password = cfg["garmin_password"]

    # Try saved token first
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

    # Full login with password
    client = Garmin(email, password)
    client.login()
    TOKEN_FILE.write_text(client.garth.dumps())
    print("  Login con password riuscito, token salvato")
    return client


def sync_garmin(cfg):
    client = garmin_login(cfg)

    days_back = cfg.get("days_back", 30)
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
            print(f"  {ds}: errore - {e}")
        cur += timedelta(days=1)

    return kcal_map


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


def upload_to_dropbox(token, kcal_map):
    data = json.dumps(kcal_map).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({
            "path": DROPBOX_PATH,
            "mode": "overwrite",
            "autorename": False,
        }),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=data,
        timeout=30,
    )
    r.raise_for_status()
    print(f"Caricato su Dropbox: {DROPBOX_PATH} ({len(kcal_map)} giorni)")


def main():
    print("=== Sync Garmin → Dropbox ===")
    cfg = load_config()

    print("Connessione a Garmin...")
    kcal_map = sync_garmin(cfg)
    print(f"Trovati {len(kcal_map)} giorni con kcal")

    if cfg.get("db_path"):
        update_local_db(cfg, kcal_map)

    print("Upload su Dropbox...")
    upload_to_dropbox(cfg["dropbox_token"], kcal_map)
    print("Fatto!")


if __name__ == "__main__":
    main()
