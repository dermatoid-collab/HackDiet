"""
Script one-shot per ottenere il refresh token Dropbox.
Esegui una volta sola, poi salva il refresh token nei GitHub Secrets.
"""

import requests
import urllib.parse
import webbrowser

APP_KEY = "sln7rdtjj7fg1as"
APP_SECRET = "08v9t03wvicwxec"

# Step 1: genera URL di autorizzazione
auth_url = (
    "https://www.dropbox.com/oauth2/authorize"
    f"?client_id={APP_KEY}"
    "&response_type=code"
    "&token_access_type=offline"
)

print("Apertura browser per autorizzazione Dropbox...")
webbrowser.open(auth_url)
print("\nDopo aver autorizzato, copia il codice che ti mostra la pagina e incollalo qui:")
auth_code = input("Codice: ").strip()

# Step 2: scambia il codice per access + refresh token
r = requests.post(
    "https://api.dropboxapi.com/oauth2/token",
    data={
        "code": auth_code,
        "grant_type": "authorization_code",
        "client_id": APP_KEY,
        "client_secret": APP_SECRET,
    }
)
r.raise_for_status()
data = r.json()

print("\n=== TOKEN OTTENUTI ===")
print(f"Access token:  {data.get('access_token')}")
print(f"Refresh token: {data.get('refresh_token')}")
print("\nSalva il REFRESH TOKEN nei GitHub Secrets come DROPBOX_REFRESH_TOKEN")
