"""
Script one-shot: importa kcal da Excel e le carica su Dropbox.
Uso: python import_excel_to_dropbox.py percorso_file.xlsx
"""

import json
import sys
import requests
from pathlib import Path

DROPBOX_PATH = "/hackdiet_kcal.json"
CONFIG_FILE = Path(__file__).parent / "sync_config.json"


def load_token():
    with open(CONFIG_FILE) as f:
        return json.load(f)["dropbox_token"]


def read_excel(path):
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    kcal_map = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        dt, kcal = row
        if dt is None or kcal is None:
            continue
        ds = dt.strftime("%Y-%m-%d")
        if int(kcal) > 0:
            kcal_map[ds] = int(kcal)
    return kcal_map


def download_existing(token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": DROPBOX_PATH}),
        "Content-Type": "",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/download",
                      headers=headers, data=b"", timeout=30)
    if r.status_code == 200:
        return r.json()
    return {}


def upload(token, kcal_map):
    data = json.dumps(kcal_map).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": DROPBOX_PATH, "mode": "overwrite", "autorename": False}),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post("https://content.dropboxapi.com/2/files/upload",
                      headers=headers, data=data, timeout=30)
    r.raise_for_status()


def main():
    if len(sys.argv) < 2:
        print("Uso: python import_excel_to_dropbox.py percorso_file.xlsx")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    print(f"Lettura Excel: {xlsx_path}")
    excel_kcal = read_excel(xlsx_path)
    print(f"  {len(excel_kcal)} giorni trovati nell'Excel")

    token = load_token()

    print("Download dati esistenti da Dropbox...")
    existing = download_existing(token)
    print(f"  {len(existing)} giorni già su Dropbox")

    # Merge: Excel ha priorità sui dati esistenti
    merged = {**existing, **excel_kcal}
    print(f"  Totale dopo merge: {len(merged)} giorni")

    print("Upload su Dropbox...")
    upload(token, merged)
    print("Fatto!")


if __name__ == "__main__":
    main()
