"""
Visualizzatore PDF da Google Drive
- Legge i PDF di una cartella Drive (service account)
- Mostra anteprime (prima pagina) in una griglia
- Apre il documento (pagine renderizzate), scarica il PDF o apre su Drive
- Campo libero per appunti per ogni documento (salvati su Drive, con fallback locale)
"""
import io
import json
import math
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

st.set_page_config(page_title="Archivio PDF", page_icon="📄", layout="wide")

SCOPES = ["https://www.googleapis.com/auth/drive"]
NOTES_FILENAME = "appunti_pdf.json"
LOCAL_NOTES = Path("appunti_locali.json")
PAGE_SIZE = 12


# ───────────────────────── Google Drive ─────────────────────────
def drive():
    if "oauth" in st.secrets:
        # Accesso con il TUO account Google (nessuna chiave di service account necessaria)
        o = st.secrets["oauth"]
        creds = Credentials(
            token=None,
            refresh_token=o["refresh_token"],
            client_id=o["client_id"],
            client_secret=o["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
    else:
        creds = service_account.Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=SCOPES
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@st.cache_data(ttl=60, show_spinner="Leggo la cartella Drive…")
def list_pdfs(folder_id: str) -> list[dict]:
    svc, files, token = drive(), [], None
    while True:
        resp = (
            svc.files()
            .list(
                q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
                fields="nextPageToken, files(id,name,size,modifiedTime,webViewLink)",
                orderBy="name",
                pageSize=200,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return files


@st.cache_data(ttl=900, max_entries=40, show_spinner=False)
def download_pdf(file_id: str, modified: str) -> bytes:
    request = drive().files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


@st.cache_data(ttl=900, max_entries=300, show_spinner=False)
def render_page(file_id: str, modified: str, page: int, zoom: float) -> tuple[bytes, int]:
    data = download_pdf(file_id, modified)
    with fitz.open(stream=data, filetype="pdf") as doc:
        pix = doc[page].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pix.tobytes("png"), doc.page_count


# ───────────────────────── Appunti ─────────────────────────
def find_notes_file(folder_id: str) -> str | None:
    resp = (
        drive()
        .files()
        .list(
            q=f"'{folder_id}' in parents and name='{NOTES_FILENAME}' and trashed=false",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def load_notes(folder_id: str) -> dict:
    try:
        notes_id = find_notes_file(folder_id)
        if notes_id:
            raw = drive().files().get_media(fileId=notes_id, supportsAllDrives=True).execute()
            return json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        st.sidebar.warning(f"Appunti da Drive non leggibili: {e}")
    if LOCAL_NOTES.exists():
        return json.loads(LOCAL_NOTES.read_text(encoding="utf-8"))
    return {}


def save_notes(folder_id: str, notes: dict) -> str:
    """Salva su Drive; se non possibile ripiega sul file locale. Ritorna dove ha salvato."""
    payload = json.dumps(notes, ensure_ascii=False, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json")
    try:
        svc = drive()
        notes_id = find_notes_file(folder_id)
        if notes_id:
            svc.files().update(fileId=notes_id, media_body=media, supportsAllDrives=True).execute()
        else:
            svc.files().create(
                body={"name": NOTES_FILENAME, "parents": [folder_id]},
                media_body=media,
                supportsAllDrives=True,
            ).execute()
        return "drive"
    except HttpError:
        LOCAL_NOTES.write_text(payload.decode("utf-8"), encoding="utf-8")
        return "locale"


# ───────────────────────── UI ─────────────────────────
def fmt_size(size) -> str:
    if not size:
        return "–"
    size = int(size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")


def detail_view(doc: dict, folder_id: str):
    fid, mod = doc["id"], doc["modifiedTime"]

    top = st.columns([1, 5])
    if top[0].button("← Torna all'elenco"):
        st.session_state.selected = None
        st.rerun()
    top[1].subheader(doc["name"])
    st.caption(f"{fmt_size(doc.get('size'))} · modificato il {fmt_date(mod)}")

    a, b, _ = st.columns([1, 1, 4])
    a.download_button(
        "⬇️ Scarica PDF",
        data=download_pdf(fid, mod),
        file_name=doc["name"],
        mime="application/pdf",
        use_container_width=True,
    )
    b.link_button("↗ Apri su Drive", doc["webViewLink"], use_container_width=True)

    left, right = st.columns([3, 2], gap="large")

    with left:
        _, n_pages = render_page(fid, mod, 0, 0.3)
        c1, c2 = st.columns([2, 1])
        page = c1.number_input("Pagina", 1, n_pages, 1, key=f"page_{fid}") - 1
        zoom = c2.select_slider("Zoom", [1.0, 1.5, 2.0], value=1.5, key="zoom")
        img, _ = render_page(fid, mod, page, zoom)
        st.image(img, caption=f"Pagina {page + 1} di {n_pages}", use_container_width=True)

    with right:
        st.markdown("#### 📝 Appunti")
        entry = st.session_state.notes.get(fid, {})
        text = st.text_area(
            "Scrivi qui i tuoi appunti su questo documento",
            value=entry.get("testo", ""),
            height=380,
            key=f"note_{fid}",
        )
        if st.button("💾 Salva appunti", type="primary"):
            st.session_state.notes[fid] = {
                "nome": doc["name"],
                "testo": text,
                "aggiornato": datetime.now().isoformat(timespec="seconds"),
            }
            where = save_notes(folder_id, st.session_state.notes)
            if where == "drive":
                st.success("Appunti salvati su Drive.")
            else:
                st.warning(
                    "Salvati solo in locale (la cartella non è scrivibile con queste credenziali)."
                )
        if entry.get("aggiornato"):
            st.caption(f"Ultimo salvataggio: {entry['aggiornato'].replace('T', ' ')}")


def grid_view(docs: list[dict]):
    pages = max(1, math.ceil(len(docs) / PAGE_SIZE))
    p = st.sidebar.number_input("Pagina elenco", 1, pages, 1) if pages > 1 else 1
    chunk = docs[(p - 1) * PAGE_SIZE : p * PAGE_SIZE]
    cols_n = st.sidebar.slider("Colonne", 2, 6, 4)

    st.caption(f"{len(docs)} documenti")
    cols = st.columns(cols_n)
    for i, d in enumerate(chunk):
        with cols[i % cols_n].container(border=True):
            try:
                thumb, n_pages = render_page(d["id"], d["modifiedTime"], 0, 0.6)
                st.image(thumb, use_container_width=True)
            except Exception:  # noqa: BLE001
                n_pages = "?"
                st.warning("Anteprima non disponibile")
            has_note = bool(st.session_state.notes.get(d["id"], {}).get("testo", "").strip())
            st.markdown(f"**{d['name']}**{' 📝' if has_note else ''}")
            st.caption(f"{n_pages} pag. · {fmt_size(d.get('size'))}")
            if st.button("Apri", key=f"open_{d['id']}", use_container_width=True):
                st.session_state.selected = d["id"]
                st.rerun()


def main():
    st.title("📄 Archivio PDF")

    has_auth = "oauth" in st.secrets or "gcp_service_account" in st.secrets
    if not has_auth or "drive_folder_id" not in st.secrets:
        st.error("Mancano `[oauth]` (o `[gcp_service_account]`) e/o `drive_folder_id` in `.streamlit/secrets.toml`.")
        st.stop()

    folder_id = st.secrets["drive_folder_id"]

    if "notes" not in st.session_state:
        st.session_state.notes = load_notes(folder_id)
    st.session_state.setdefault("selected", None)

    st.sidebar.header("Filtri")
    query = st.sidebar.text_input("🔍 Cerca per nome o negli appunti")
    only_notes = st.sidebar.checkbox("Solo documenti con appunti")
    if st.sidebar.button("🔄 Ricarica cartella"):
        list_pdfs.clear()
        st.session_state.notes = load_notes(folder_id)
        st.rerun()

    try:
        docs = list_pdfs(folder_id)
    except HttpError as e:
        st.error(f"Errore Google Drive: {e}")
        st.stop()

    if st.session_state.selected:
        doc = next((d for d in docs if d["id"] == st.session_state.selected), None)
        if doc:
            detail_view(doc, folder_id)
            return
        st.session_state.selected = None

    q = query.lower().strip()
    if q:
        docs = [
            d
            for d in docs
            if q in d["name"].lower()
            or q in st.session_state.notes.get(d["id"], {}).get("testo", "").lower()
        ]
    if only_notes:
        docs = [d for d in docs if st.session_state.notes.get(d["id"], {}).get("testo", "").strip()]

    if not docs:
        st.info("Nessun PDF trovato.")
        return
    grid_view(docs)


main()
