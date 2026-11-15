import base64
import json
import mimetypes
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- CONFIGURATION ---
DB_PATH = Path("creative_hub.db")
TEMPLATE_PATH = Path("src/card_template.html")
OUTPUT_DIR = Path("docs")
CARDS_DIR = OUTPUT_DIR / "cards"

# Replace with your actual GitHub Pages base URL
BASE_SHARE_URL = "https://github.io"


def init_db(conn: sqlite3.Connection) -> None:
    """Create the master creative database tables if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_name TEXT NOT NULL,
            occasion_title TEXT NOT NULL,
            cover_path TEXT NOT NULL,
            inside_path TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            encryption_key TEXT NOT NULL,
            share_url TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artworks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL,
            web_optimized_path TEXT,
            tags TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def ensure_neutral_index() -> None:
    """Keep docs/index.html private so visitors cannot list individual cards."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index_file = OUTPUT_DIR / "index.html"
    if not index_file.exists():
        index_file.write_text(
            """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Private Cards</title></head>
<body style="min-height:100vh;display:grid;place-items:center;background:#e9eeeb;color:#273532;font-family:Georgia,serif;">
  <p>Please use your personal invitation link to open your card.</p>
</body>
</html>
""",
            encoding="utf-8",
        )


def image_to_data_uri(image_path: Path) -> str:
    """Convert a local image file into a Base64 data URI."""
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def create_recipient_card(
    recipient_name: str,
    occasion_title: str,
    cover_image: Path,
    inside_image: Path,
) -> str:
    """Bundle, encrypt, and log a unique one-click greeting card in SQLite."""
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_neutral_index()

    # 1. Inject title and Base64 images into your two-page template
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace("{{CARD_TITLE}}", occasion_title)
    html = html.replace("{{COVER_IMAGE}}", image_to_data_uri(cover_image))
    html = html.replace("{{INSIDE_IMAGE}}", image_to_data_uri(inside_image))

    # 2. Encrypt with AES-256-GCM
    key = AESGCM.generate_key(bit_length=256)
    iv = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(iv, html.encode("utf-8"), None)

    key_b64 = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
    iv_b64 = base64.b64encode(iv).decode("ascii")
    ct_b64 = base64.b64encode(ciphertext).decode("ascii")

    # 3. Generate unique unguessable filename inside docs/cards/
    slug = f"{secrets.token_hex(6)}.html"
    out_file = CARDS_DIR / slug

    loader_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Private Greeting Card</title>
  <style>
    body {{
      min-height: 100vh; margin: 0; display: grid; place-items: center;
      background: #e9eeeb; color: #273532; font-family: Georgia, serif; text-align: center;
    }}
    .box {{ max-width: 380px; padding: 24px; }}
    input, button {{
      padding: 10px 14px; font: inherit; border: 1px solid #8b9b93; border-radius: 3px;
    }}
    button {{ background: #273532; color: #fff; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="box">
    <h2>Opening your card...</h2>
    <p id="msg">Your card will appear automatically.</p>
    <div id="manual" style="display:none; margin-top:16px;">
      <input type="password" id="keyInput" placeholder="Enter card key">
      <button onclick="unlock(document.getElementById('keyInput').value)">Open Card</button>
    </div>
  </div>
  <script>
    const IV = {json.dumps(iv_b64)};
    const DATA = {json.dumps(ct_b64)};

    function b64ToBytes(b64) {{
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return bytes;
    }}

    function urlB64ToBytes(u64) {{
      let b64 = u64.replace(/-/g, '+').replace(/_/g, '/');
      while (b64.length % 4) b64 += '=';
      return b64ToBytes(b64);
    }}

    async function unlock(rawKey) {{
      try {{
        const cryptoKey = await crypto.subtle.importKey(
          "raw", urlB64ToBytes(rawKey.trim()), "AES-GCM", false, ["decrypt"]
        );
        const decrypted = await crypto.subtle.decrypt(
          {{ name: "AES-GCM", iv: b64ToBytes(IV) }}, cryptoKey, b64ToBytes(DATA)
        );
        const pageHtml = new TextDecoder().decode(decrypted);
        document.open();
        document.write(pageHtml);
        document.close();
      }} catch (e) {{
        document.getElementById("msg").textContent = "Invalid or missing link key. Please enter the key below:";
        document.getElementById("manual").style.display = "block";
      }}
    }}

    const hashKey = window.location.hash.replace(/^#/, "");
    if (hashKey) {{
      unlock(hashKey);
    }} else {{
      document.getElementById("msg").textContent = "Please use your direct card link or enter the key below:";
      document.getElementById("manual").style.display = "block";
    }}
  </script>
</body>
</html>
"""
    out_file.write_text(loader_html, encoding="utf-8")

    share_url = f"{BASE_SHARE_URL.rstrip('/')}/cards/{slug}#{key_b64}"

    # 4. Log card details and key into SQLite
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO cards (
                recipient_name, occasion_title, cover_path, inside_path,
                slug, encryption_key, share_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipient_name,
                occasion_title,
                str(cover_image),
                str(inside_image),
                slug,
                key_b64,
                share_url,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()

    return share_url


def list_saved_cards() -> None:
    """Display all generated cards stored in creative_hub.db."""
    if not DB_PATH.exists():
        return
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, recipient_name, occasion_title, slug, created_at FROM cards ORDER BY id DESC"
        ).fetchall()
    if rows:
        print("\n--- Saved Cards in creative_hub.db ---")
        for row in rows:
            print(f"[{row[0]}] {row[1]} | {row[2]} | cards/{row[3]} ({row[4]})")


if __name__ == "__main__":
    # Interactive prompts so you can generate cards without editing code each time
    recipient = input("Recipient name (e.g. Sarah): ").strip() or "Friend"
    title = input("Card heading (e.g. Happy Birthday!): ").strip() or "Thinking of You"
    cover = Path(input("Cover image path [src/cover.png]: ").strip() or "src/cover.png")
    inside = Path(input("Inside image path [src/inside.png]: ").strip() or "src/inside.png")

    link = create_recipient_card(recipient, title, cover, inside)
    print(f"\nCard created for {recipient}!")
    print(f"One-click share link:\n{link}")
    list_saved_cards()
