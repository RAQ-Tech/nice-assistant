import base64
import cgi
import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.getenv("PORT", "3000"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.18.200:11434")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/archives"))
AUDIO_HOT_LIMIT = int(os.getenv("AUDIO_HOT_LIMIT", "200"))
SESSION_COOKIE = "nice_assistant_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))

AUDIO_DIR = DATA_DIR / "audio"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "nice_assistant.db"
SETTINGS_JSON = DATA_DIR / "settings.json"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def ensure_dirs():
    for p in [DATA_DIR, AUDIO_DIR, LOG_DIR, ARCHIVE_DIR, ARCHIVE_DIR / "audio", ARCHIVE_DIR / "logs", ARCHIVE_DIR / "db_backups"]:
        p.mkdir(parents=True, exist_ok=True)


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS personas (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            avatar_url TEXT,
            system_prompt TEXT,
            default_model TEXT,
            preferred_voice TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            workspace_id TEXT,
            persona_id TEXT,
            model_override TEXT,
            memory_mode TEXT DEFAULT 'auto',
            title TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tier TEXT NOT NULL,
            tier_ref_id TEXT,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            user_id TEXT PRIMARY KEY,
            global_default_model TEXT,
            default_memory_mode TEXT DEFAULT 'auto',
            stt_provider TEXT DEFAULT 'disabled',
            tts_provider TEXT DEFAULT 'disabled',
            tts_format TEXT DEFAULT 'wav',
            openai_api_key TEXT,
            onboarding_done INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audio_files (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            persona_id TEXT,
            chat_id TEXT,
            format TEXT NOT NULL,
            local_path TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "expires_at" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN expires_at INTEGER")
        conn.execute("UPDATE sessions SET expires_at = created_at + ? WHERE expires_at IS NULL", (SESSION_TTL_SECONDS,))
        conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"{salt}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
        return base64.b64encode(dk).decode() == digest
    except Exception:
        return False


def now_ts():
    return int(time.time())


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def rotate_audio_cache():
    files = sorted([p for p in AUDIO_DIR.glob("*") if p.is_file()], key=lambda p: p.stat().st_mtime)
    while len(files) > AUDIO_HOT_LIMIT:
        oldest = files.pop(0)
        shutil.move(str(oldest), ARCHIVE_DIR / "audio" / oldest.name)


def backup_db_if_needed():
    stamp = datetime.utcnow().strftime("%Y%m%d")
    target = ARCHIVE_DIR / "db_backups" / f"nice_assistant_{stamp}.db"
    if not target.exists() and DB_PATH.exists():
        shutil.copy2(DB_PATH, target)


def rotate_logs(limit=50):
    logs = sorted([p for p in LOG_DIR.glob("*.log") if p.is_file()], key=lambda p: p.stat().st_mtime)
    while len(logs) > limit:
        f = logs.pop(0)
        shutil.move(str(f), ARCHIVE_DIR / "logs" / f.name)


def ollama_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=6) as r:
            data = json.loads(r.read().decode())
            return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def call_ollama(model, messages):
    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
        return data.get("message", {}).get("content", "")


def openai_speech(text, voice, fmt, api_key):
    payload = json.dumps({"model": "gpt-4o-mini-tts", "input": text, "voice": voice or "alloy", "format": fmt}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def openai_stt(filepath, api_key):
    boundary = "----NiceAssistantBoundary" + secrets.token_hex(8)
    with open(filepath, "rb") as f:
        audio = f.read()
    parts = []
    def add(name, value, filename=None, ctype="text/plain"):
        parts.append(f"--{boundary}\r\n".encode())
        if filename:
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
            parts.append(value)
            parts.append(b"\r\n")
        else:
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    add("model", "whisper-1")
    add("file", audio, filename="audio.wav", ctype="audio/wav")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


class Handler(BaseHTTPRequestHandler):
    server_version = "NiceAssistant/0.1"

    def _set_headers(self, code=200, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")

    def _json(self, data, code=200, cookie=None):
        self._set_headers(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _read_json(self):
        l = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(l) if l else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except Exception:
            return {}

    def _cookies(self):
        c = SimpleCookie()
        c.load(self.headers.get("Cookie", ""))
        return c

    def _auth_user(self):
        c = self._cookies()
        tok = c.get(SESSION_COOKIE)
        if not tok:
            return None
        conn = db_conn()
        row = conn.execute("SELECT user_id, expires_at FROM sessions WHERE token=?", (tok.value,)).fetchone()
        if row and row["expires_at"] and row["expires_at"] <= now_ts():
            conn.execute("DELETE FROM sessions WHERE token=?", (tok.value,))
            conn.commit()
            row = None
        conn.close()
        return row["user_id"] if row else None

    def _require_auth(self):
        uid = self._auth_user()
        if not uid:
            self._json({"error": "unauthorized"}, 401)
            return None
        return uid

    def do_OPTIONS(self):
        self._set_headers(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            return self._json({"ok": True})
        if self.path == "/api/models":
            return self._json({"models": ollama_models()})
        if self.path.startswith("/api/tts/audio/"):
            aid = self.path.rsplit("/", 1)[-1]
            conn = db_conn(); row = conn.execute("SELECT * FROM audio_files WHERE id=?", (aid,)).fetchone(); conn.close()
            if not row:
                return self._json({"error": "not found"}, 404)
            p = Path(row["local_path"])
            if not p.exists():
                return self._json({"error": "missing file"}, 404)
            self._set_headers(200, mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            self.end_headers()
            self.wfile.write(p.read_bytes())
            return
        if self.path == "/api/workspaces":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); rows = [dict(r) for r in conn.execute("SELECT * FROM workspaces WHERE user_id=?", (uid,)).fetchall()]; conn.close()
            return self._json({"items": rows})
        if self.path == "/api/personas":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); rows = [dict(r) for r in conn.execute("SELECT p.* FROM personas p JOIN workspaces w ON p.workspace_id=w.id WHERE w.user_id=?", (uid,)).fetchall()]; conn.close()
            return self._json({"items": rows})
        if self.path == "/api/chats":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); rows = [dict(r) for r in conn.execute("SELECT * FROM chats WHERE user_id=? ORDER BY updated_at DESC", (uid,)).fetchall()]; conn.close()
            return self._json({"items": rows})
        if self.path.startswith("/api/chats/"):
            uid = self._require_auth();
            if not uid: return
            chat_id = self.path.split("/")[3]
            conn = db_conn()
            chat = conn.execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
            msgs = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at", (chat_id,)).fetchall()]
            conn.close()
            if not chat: return self._json({"error": "not found"}, 404)
            return self._json({"chat": dict(chat), "messages": msgs})
        if self.path.startswith("/api/memory/"):
            uid = self._require_auth();
            if not uid: return
            conn = db_conn()
            if self.path == "/api/memory/global":
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='global'", (uid,)).fetchall()]
            elif self.path.startswith("/api/memory/workspace/"):
                wid = self.path.rsplit("/", 1)[-1]
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=?", (uid, wid)).fetchall()]
            elif self.path.startswith("/api/memory/persona/"):
                pid = self.path.rsplit("/", 1)[-1]
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=?", (uid, pid)).fetchall()]
            else:
                conn.close(); return self._json({"error": "unknown tier"}, 400)
            conn.close(); return self._json({"items": rows})
        if self.path == "/api/settings":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); row = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone(); conn.close()
            return self._json({"settings": dict(row) if row else {"default_memory_mode": "auto", "stt_provider": "disabled", "tts_provider": "disabled", "tts_format": "wav"}})
        if self.path == "/api/session":
            uid = self._require_auth();
            if not uid: return
            tok = self._cookies().get(SESSION_COOKIE)
            conn = db_conn(); row = conn.execute("SELECT expires_at FROM sessions WHERE token=? AND user_id=?", (tok.value, uid)).fetchone(); conn.close()
            return self._json({"expiresAt": row["expires_at"] if row else None, "ttlSeconds": SESSION_TTL_SECONDS, "now": now_ts()})
        # static
        rel = self.path if self.path != "/" else "/index.html"
        target = (WEB_DIR / rel.lstrip("/")).resolve()
        if WEB_DIR in target.parents or target == WEB_DIR:
            if target.exists() and target.is_file():
                self._set_headers(200, mimetypes.guess_type(str(target))[0] or "text/plain")
                self.end_headers(); self.wfile.write(target.read_bytes()); return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/users":
            body = self._read_json(); username = body.get("username", "").strip(); password = body.get("password", "")
            if not username or not password: return self._json({"error": "username/password required"}, 400)
            conn = db_conn()
            try:
                uid = secrets.token_hex(8)
                conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES (?,?,?,?)", (uid, username, hash_password(password), now_ts()))
                conn.execute("INSERT INTO app_settings(user_id) VALUES (?)", (uid,))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close(); return self._json({"error": "username exists"}, 409)
            conn.close(); return self._json({"ok": True})
        if self.path == "/api/login":
            body = self._read_json(); username = body.get("username", "").strip(); password = body.get("password", "")
            conn = db_conn(); row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not verify_password(password, row["password_hash"]): conn.close(); return self._json({"error": "invalid credentials"}, 401)
            tok = secrets.token_hex(24)
            created = now_ts()
            expires = created + SESSION_TTL_SECONDS
            conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES (?,?,?,?)", (tok, row["id"], created, expires)); conn.commit(); conn.close()
            return self._json({"ok": True, "userId": row["id"], "expiresAt": expires, "ttlSeconds": SESSION_TTL_SECONDS}, cookie=f"{SESSION_COOKIE}={tok}; Max-Age={SESSION_TTL_SECONDS}; Path=/; HttpOnly; SameSite=Lax")
        if self.path == "/api/logout":
            tok = self._cookies().get(SESSION_COOKIE)
            if tok:
                conn = db_conn(); conn.execute("DELETE FROM sessions WHERE token=?", (tok.value,)); conn.commit(); conn.close()
            return self._json({"ok": True}, cookie=f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        uid = self._require_auth()
        if not uid: return
        if self.path == "/api/workspaces":
            b = self._read_json(); wid = secrets.token_hex(8)
            conn = db_conn(); conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES(?,?,?,?)", (wid, uid, b.get("name", "Workspace"), now_ts())); conn.commit(); conn.close()
            return self._json({"id": wid})
        if self.path == "/api/personas":
            b = self._read_json(); pid = secrets.token_hex(8)
            conn = db_conn(); conn.execute("INSERT INTO personas(id,workspace_id,name,avatar_url,system_prompt,default_model,preferred_voice,created_at) VALUES(?,?,?,?,?,?,?,?)", (pid,b.get("workspaceId"),b.get("name","Persona"),b.get("avatarUrl"),b.get("systemPrompt"),b.get("defaultModel"),b.get("preferredVoice"),now_ts())); conn.commit(); conn.close()
            return self._json({"id": pid})
        if self.path == "/api/chats":
            b = self._read_json(); cid = secrets.token_hex(8); t=now_ts()
            conn = db_conn(); conn.execute("INSERT INTO chats(id,user_id,workspace_id,persona_id,model_override,memory_mode,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (cid,uid,b.get("workspaceId"),b.get("personaId"),b.get("model"),b.get("memoryMode","auto"),b.get("title","New chat"),t,t)); conn.commit(); conn.close()
            return self._json({"id": cid})
        if self.path == "/api/chat":
            b = self._read_json(); text = b.get("text", "").strip();
            if not text: return self._json({"error": "text required"}, 400)
            conn = db_conn(); t=now_ts()
            chat_id = b.get("chatId")
            if chat_id:
                chat = conn.execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
            else:
                chat = None
            if not chat:
                chat_id = secrets.token_hex(8)
                conn.execute("INSERT INTO chats(id,user_id,persona_id,model_override,memory_mode,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (chat_id,uid,b.get("personaId"),b.get("model"),b.get("memoryMode","auto"),text[:40],t,t))
                chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            mem_mode = b.get("memoryMode") or chat["memory_mode"] or "auto"
            persona_id = b.get("personaId") or chat["persona_id"]
            model = b.get("model") or chat["model_override"]
            settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone()
            if not model and persona_id:
                p = conn.execute("SELECT default_model FROM personas WHERE id=?", (persona_id,)).fetchone(); model = p["default_model"] if p else None
            model = model or (settings["global_default_model"] if settings else None) or (ollama_models()[0] if ollama_models() else "llama3")

            sys_msgs = []
            if mem_mode != "off":
                gm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='global'", (uid,)).fetchall()]
                sys_msgs += gm
                if chat["workspace_id"]:
                    wm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=?", (uid, chat["workspace_id"]))]
                    sys_msgs += wm
                if persona_id:
                    pm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=?", (uid, persona_id))]
                    sys_msgs += pm
            p = conn.execute("SELECT system_prompt FROM personas WHERE id=?", (persona_id,)).fetchone() if persona_id else None
            if p and p[0]: sys_msgs.append(p[0])
            messages = [{"role":"system","content":"\n".join(sys_msgs)}] if sys_msgs else []
            hist = conn.execute("SELECT role,text FROM messages WHERE chat_id=? ORDER BY created_at DESC LIMIT 20", (chat_id,)).fetchall()
            for r in reversed(hist): messages.append({"role":r[0],"content":r[1]})
            messages.append({"role":"user","content":text})
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)", (secrets.token_hex(8),chat_id,"user",text,t))
            try:
                reply = call_ollama(model, messages)
            except Exception as e:
                reply = f"Model call failed: {e}"
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)", (secrets.token_hex(8),chat_id,"assistant",reply,now_ts()))
            conn.execute("UPDATE chats SET updated_at=?, memory_mode=?, persona_id=?, model_override=? WHERE id=?", (now_ts(), mem_mode, persona_id, b.get("model") or chat["model_override"], chat_id))
            if mem_mode == "auto":
                if len(text) < 280 and any(k in text.lower() for k in ["my ", "i like", "remember", "name is"]):
                    conn.execute("INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES(?,?,?,?,?,?)", (secrets.token_hex(8), uid, "persona" if persona_id else "global", persona_id, text, now_ts()))
            conn.commit(); conn.close(); backup_db_if_needed()
            return self._json({"text": reply, "chatId": chat_id})
        if self.path == "/api/settings":
            b = self._read_json()
            conn = db_conn()
            conn.execute("INSERT INTO app_settings(user_id) VALUES(?) ON CONFLICT(user_id) DO NOTHING", (uid,))
            conn.execute("UPDATE app_settings SET global_default_model=?, default_memory_mode=?, stt_provider=?, tts_provider=?, tts_format=?, openai_api_key=?, onboarding_done=? WHERE user_id=?", (
                b.get("global_default_model"), b.get("default_memory_mode","auto"), b.get("stt_provider","disabled"), b.get("tts_provider","disabled"), b.get("tts_format","wav"), b.get("openai_api_key"), int(bool(b.get("onboarding_done"))), uid
            ))
            conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/memory/"):
            b = self._read_json(); mid=secrets.token_hex(8); tier="global"; ref=None
            if self.path.startswith("/api/memory/workspace/"): tier="workspace"; ref=self.path.rsplit("/",1)[-1]
            elif self.path.startswith("/api/memory/persona/"): tier="persona"; ref=self.path.rsplit("/",1)[-1]
            conn = db_conn(); conn.execute("INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES(?,?,?,?,?,?)", (mid,uid,tier,ref,b.get("content",""),now_ts())); conn.commit(); conn.close(); return self._json({"id": mid})
        if self.path == "/api/tts":
            b = self._read_json(); text=b.get("text","")
            conn = db_conn(); settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone(); conn.close()
            if not settings or settings["tts_provider"] == "disabled": return self._json({"error":"TTS disabled"}, 400)
            fmt = b.get("format") or settings["tts_format"] or "wav"
            out_id = secrets.token_hex(8)
            out_path = AUDIO_DIR / f"{out_id}.{fmt}"
            if settings["tts_provider"] == "openai":
                key = settings["openai_api_key"]
                if not key: return self._json({"error":"OPENAI API key missing"}, 400)
                try:
                    audio = openai_speech(text, b.get("voice"), fmt, key)
                    out_path.write_bytes(audio)
                except Exception as e:
                    return self._json({"error": f"TTS failed: {e}"}, 500)
            else:
                return self._json({"error":"Local provider not implemented yet"}, 400)
            conn = db_conn(); conn.execute("INSERT INTO audio_files(id,user_id,persona_id,chat_id,format,local_path,created_at) VALUES(?,?,?,?,?,?,?)", (out_id, uid, b.get("personaId"), b.get("chatId"), fmt, str(out_path), now_ts())); conn.commit(); conn.close()
            rotate_audio_cache()
            return self._json({"audioUrl": f"/api/tts/audio/{out_id}", "format": fmt})
        if self.path == "/api/stt":
            conn = db_conn(); settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone(); conn.close()
            if not settings or settings["stt_provider"] == "disabled": return self._json({"error":"STT disabled"}, 400)
            fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD":"POST", "CONTENT_TYPE":self.headers.get("Content-Type")})
            fitem = fs["file"] if "file" in fs else None
            if not fitem: return self._json({"error": "file required"}, 400)
            raw = DATA_DIR / f"upload_{secrets.token_hex(6)}.webm"
            wav = DATA_DIR / f"upload_{secrets.token_hex(6)}.wav"
            with open(raw, "wb") as f: f.write(fitem.file.read())
            subprocess.run(["ffmpeg", "-y", "-i", str(raw), str(wav)], check=False, capture_output=True)
            if settings["stt_provider"] == "openai":
                key = settings["openai_api_key"]
                if not key: return self._json({"error":"OPENAI API key missing"}, 400)
                try:
                    data = openai_stt(str(wav), key)
                    return self._json({"text": data.get("text", ""), "language": data.get("language")})
                except Exception as e:
                    return self._json({"error": f"STT failed: {e}"}, 500)
            return self._json({"error":"Local provider not implemented yet"}, 400)
        if self.path == "/api/tts/stream":
            return self._json({"todo": "Streaming TTS endpoint foundation placeholder"}, 501)
        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        uid = self._require_auth();
        if not uid: return
        if self.path.startswith("/api/chats/"):
            chat_id=self.path.rsplit("/",1)[-1]; b=self._read_json()
            conn=db_conn(); conn.execute("UPDATE chats SET model_override=?, memory_mode=?, persona_id=?, updated_at=? WHERE id=? AND user_id=?", (b.get("model_override"), b.get("memory_mode"), b.get("persona_id"), now_ts(), chat_id, uid)); conn.commit(); conn.close(); return self._json({"ok":True})
        return self._json({"error":"not found"},404)

    def do_DELETE(self):
        uid = self._require_auth();
        if not uid: return
        if self.path.startswith("/api/memory/"):
            mid=self.path.rsplit("/",1)[-1]
            conn=db_conn(); conn.execute("DELETE FROM memories WHERE id=? AND user_id=?", (mid,uid)); conn.commit(); conn.close(); return self._json({"ok":True})
        return self._json({"error":"not found"},404)


def main():
    ensure_dirs(); init_db(); rotate_logs(); backup_db_if_needed()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Nice Assistant listening on {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
