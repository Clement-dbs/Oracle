"""
Script one-shot pour (re)générer les credentials Google OAuth d'Oracle.

Usage :
    python regenerate_token.py

Le script démarre un serveur local sur le port 8000, ouvre le navigateur
pour l'autorisation Google, puis sauvegarde directement les credentials dans
./credentials/google_credentials.json — aucune manipulation du .env requise.
"""

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

load_dotenv()

CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/auth/google/callback"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CREDS_PATH = Path(__file__).parent / "credentials" / "google_credentials.json"

client_config = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}

flow = Flow.from_client_config(
    client_config,
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI,
    autogenerate_code_verifier=True,
)

auth_url, _ = flow.authorization_url(
    access_type="offline",
    prompt="consent",
    include_granted_scopes="true",
)

# ── Serveur local qui capte le callback ──────────────────────────────────
_code = None
_server = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _code
        parsed = urlparse(self.path)
        if parsed.path == "/auth/google/callback":
            params = parse_qs(parsed.query)
            _code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Autoris\xc3\xa9 \xe2\x80\x94 tu peux fermer cet onglet.</h2>")
        else:
            self.send_response(404)
            self.end_headers()
        threading.Thread(target=_server.shutdown, daemon=True).start()

    def log_message(self, *args):
        pass


_server = HTTPServer(("localhost", 8000), CallbackHandler)

print("\nOuverture du navigateur pour l'autorisation Google...")
webbrowser.open(auth_url)
print("(Si le navigateur ne s'ouvre pas, colle cette URL :)")
print(f"\n  {auth_url}\n")

_server.serve_forever()

# ── Échange du code contre les tokens ────────────────────────────────────
if not _code:
    raise SystemExit("Aucun code reçu — autorisation annulée.")

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
flow.fetch_token(code=_code)
creds = flow.credentials

if not creds.refresh_token:
    raise SystemExit(
        "Pas de refresh_token reçu.\n"
        "Révoque l'accès sur https://myaccount.google.com/permissions puis réessaie."
    )

# ── Sauvegarde directe dans credentials/ ─────────────────────────────────
CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
CREDS_PATH.write_text(
    json.dumps(
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        },
        indent=2,
    )
)

print(f"\nCredentials sauvegardés dans {CREDS_PATH}")
print("Oracle utilisera ce fichier automatiquement — aucune action supplémentaire requise.")
