"""
Eseguilo UNA volta sul tuo PC per ottenere il refresh token OAuth.
Uso:  python get_refresh_token.py client_secret.json
Poi copia i valori stampati in .streamlit/secrets.toml
"""
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]

flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\n# --- Incolla in .streamlit/secrets.toml ---")
print("[oauth]")
print(f'client_id = "{creds.client_id}"')
print(f'client_secret = "{creds.client_secret}"')
print(f'refresh_token = "{creds.refresh_token}"')
