#!/usr/bin/env python3
"""
My AI CLI — Ollama / OpenRouter + Tool Calling
Fitur: baca/tulis file, edit kode, web search, akses folder
Provider: ollama (lokal) atau openrouter (cloud, butuh API key)
"""

import os, json, datetime, subprocess, re, sys
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv tidak terinstall, skip

try:
    import requests
except ImportError:
    print("❌ Jalankan: pip install requests")
    sys.exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────

HISTORY_FILE     = Path(__file__).parent / "history" / "history.json"
MAX_FILE_BYTES   = 100_000   # batas baca file ~100 KB
MAX_SEARCH_CHARS = 3_000     # batas karakter hasil search

# Provider: "ollama" atau "openrouter"
PROVIDER         = "ollama"   # akan di-override di main()
OLLAMA_URL       = "http://localhost:11434/api/chat"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

# Model fallback jika Ollama tidak bisa di-ping
MODELS_OLLAMA = {
    "1": ("llama3.2:latest", "Llama 3.2 (latest)"),
}

# Model populer OpenRouter (gratis / murah)
MODELS_OPENROUTER = {
    "1":  ("meta-llama/llama-3.3-70b-instruct:free",    "Llama 3.3 70B (free)"),
    "2":  ("meta-llama/llama-3.1-8b-instruct:free",     "Llama 3.1 8B (free)"),
    "3":  ("mistralai/mistral-7b-instruct:free",         "Mistral 7B (free)"),
    "4":  ("google/gemma-3-27b-it:free",                 "Gemma 3 27B (free)"),
    "5":  ("deepseek/deepseek-r1:free",                  "DeepSeek R1 (free)"),
    "6":  ("openai/gpt-4o-mini",                         "GPT-4o Mini"),
    "7":  ("anthropic/claude-sonnet-4-5",                "Claude Sonnet 4.5"),
    "8":  ("google/gemini-2.0-flash-exp:free",           "Gemini 2.0 Flash (free)"),
}

# ─── VOICEVOX (Text-to-Speech) ─────────────────────────────────────────────────
# Aplikasi VOICEVOX harus dijalankan terpisah (https://voicevox.hiroshiba.jp/),
# berjalan sebagai server lokal di port 50021.
VOICEVOX_URL     = "http://127.0.0.1:50021"
VOICEVOX_SPEAKER = 3   # 3 = Zundamon (Normal). Lihat /speakers utk daftar lengkap.
VOICE_ENABLED    = False  # toggle via /voice

# ─── Whitelist direktori ───────────────────────────────────────────────────────
# Tools file hanya boleh akses path di dalam ALLOWED_ROOT (mencegah AI
# membaca/menulis file di luar working directory project, mis. ~/.ssh).
ALLOWED_ROOT = Path(".").resolve()

def set_allowed_root(path: Path):
    global ALLOWED_ROOT
    ALLOWED_ROOT = path.resolve()

def check_path(path: str) -> tuple[Path, str]:
    """Resolve path & pastikan berada di dalam ALLOWED_ROOT.
    Return (resolved_path, error_msg). error_msg kosong jika aman."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (ALLOWED_ROOT / p)
    p = p.resolve()
    try:
        p.relative_to(ALLOWED_ROOT)
    except ValueError:
        return p, f"❌ Akses ditolak: '{p}' berada di luar direktori yang diizinkan ({ALLOWED_ROOT})"
    return p, ""

PERSONA_NAMA   = "My AI"
PERSONA_PROMPT = (
    "Kamu adalah My AI, asisten pribadi yang serba bisa. Kemampuanmu meliputi:\n"
    "- Asisten umum: menjawab pertanyaan, menulis, merangkum, brainstorming.\n"
    "- Developer: membaca, menulis, mengedit, dan menjelaskan kode di berbagai bahasa.\n"
    "- File manager: mengorganisasi, mencari, dan memodifikasi file dan folder.\n"
    "- Researcher: mencari informasi terkini di internet lalu merangkumnya.\n\n"
    "Gunakan tools yang tersedia secara proaktif tanpa perlu diminta eksplisit. "
    "Selalu jelaskan dengan singkat apa yang sedang kamu lakukan sebelum mengeksekusi tool. "
    "Untuk tugas besar, pecah menjadi langkah-langkah kecil dan laporkan progresnya.\n\n"
    "Gaya respons:\n"
    "- Untuk sapaan kasual (hai, halo, pagi, makasih, dll) atau basa-basi sehari-hari, "
    "balas SANGAT SINGKAT (1 kalimat pendek atau beberapa kata saja), santai, "
    "tanpa basa-basi tambahan dan tanpa tools, dalam Bahasa Indonesia.\n"
    "- KHUSUS jika pesan user HANYA berupa kata 'test', 'tes', 'testing', 'cek', atau 'ping' "
    "(tanpa konten lain), balas dengan SATU kalimat SANGAT PENDEK dalam BAHASA JEPANG saja "
    "(contoh: 'テスト成功です！', 'はい、聞こえています！', 'マイクのテスト中だよ！'). "
    "Tanpa terjemahan, tanpa romaji, tanpa penjelasan tambahan, tanpa tools.\n"
    "- Untuk pertanyaan/tugas yang butuh penjelasan, kerja teknis, atau analisis, "
    "balas selengkap dan sejelas yang dibutuhkan seperti biasa dalam Bahasa Indonesia."
)

# ─── Warna ────────────────────────────────────────────────────────────────────

R="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
CYAN="\033[96m"; GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"; MAGENTA="\033[95m"; WHITE="\033[97m"

def c(text, color): return f"{color}{text}{R}"
def yn(prompt): return input(f"{YELLOW}⚠ {prompt} [y/N]: {R}").strip().lower() == "y"

# ─── VOICEVOX TTS ──────────────────────────────────────────────────────────────

_TEMP_VOICE_FILE = Path(__file__).parent / "history" / "_voice_tmp.wav"

def voicevox_check() -> bool:
    """Cek apakah server VOICEVOX berjalan."""
    try:
        r = requests.get(f"{VOICEVOX_URL}/version", timeout=3)
        return r.ok
    except Exception:
        return False


def voicevox_speakers() -> list:
    """Ambil daftar speaker/karakter yang tersedia."""
    try:
        r = requests.get(f"{VOICEVOX_URL}/speakers", timeout=5)
        if not r.ok: return []
        out = []
        for sp in r.json():
            for style in sp.get("styles", []):
                out.append((style["id"], f"{sp['name']} - {style['name']}"))
        return out
    except Exception:
        return []


def _clean_text_for_tts(text: str) -> str:
    """Buang markdown/simbol yang aneh kalau dibaca TTS."""
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)   # blok kode
    text = re.sub(r'`([^`]*)`', r'\1', text)                  # inline code
    text = re.sub(r'[*_#>~\[\]()]', '', text)                 # markdown chars
    text = re.sub(r'https?://\S+', '', text)                  # URL
    text = re.sub(r'\n+', '. ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _play_audio_file(path: Path):
    """Putar file audio secara cross-platform (blocking)."""
    system = sys.platform
    try:
        if system.startswith("win"):
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
        elif system == "darwin":
            subprocess.run(["afplay", str(path)], check=False)
        else:  # linux
            for player in (["paplay"], ["aplay"], ["ffplay", "-nodisp", "-autoexit"]):
                if subprocess.run(["which", player[0]], capture_output=True).returncode == 0:
                    subprocess.run(player + [str(path)], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
    except Exception as e:
        print(f"  {DIM}(gagal memutar audio: {e}){R}")


def speak_text(text: str) -> str:
    """Sintesis teks via VOICEVOX lalu mainkan audionya. Return pesan status."""
    text = _clean_text_for_tts(text)
    if not text:
        return "ℹ️ Tidak ada teks untuk dibacakan."

    try:
        # 1) audio_query: bangun parameter prosodi dari teks
        q = requests.post(
            f"{VOICEVOX_URL}/audio_query",
            params={"text": text, "speaker": VOICEVOX_SPEAKER},
            timeout=15,
        )
        q.raise_for_status()

        # 2) synthesis: render WAV dari query
        s = requests.post(
            f"{VOICEVOX_URL}/synthesis",
            params={"speaker": VOICEVOX_SPEAKER},
            json=q.json(),
            timeout=30,
        )
        s.raise_for_status()

        _TEMP_VOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TEMP_VOICE_FILE.write_bytes(s.content)
        _play_audio_file(_TEMP_VOICE_FILE)
        return "✅ Suara diputar."
    except requests.exceptions.ConnectionError:
        return ("❌ Tidak bisa terhubung ke VOICEVOX. "
                "Pastikan aplikasi VOICEVOX terbuka (server di :50021).")
    except Exception as e:
        return f"❌ TTS gagal: {e}"



# ─── Tool definitions (dikirim ke API) ────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Baca isi file teks. Gunakan untuk melihat kode, config, atau dokumen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path absolut atau relatif ke file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Tulis atau buat file baru. Untuk membuat file baru atau menimpa seluruh isi file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path file yang akan ditulis"},
                    "content": {"type": "string", "description": "Isi file yang akan ditulis"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit bagian tertentu dari file: ganti old_str dengan new_str. Gunakan untuk patch kode tanpa menulis ulang seluruh file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path file yang akan diedit"},
                    "old_str": {"type": "string", "description": "Teks yang akan diganti (harus unik dalam file)"},
                    "new_str": {"type": "string", "description": "Teks pengganti"}
                },
                "required": ["path", "old_str", "new_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Tampilkan isi direktori (file dan subfolder).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path direktori, default '.'"},
                    "recursive": {"type": "boolean", "description": "Tampilkan subfolder secara rekursif, default false"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": "Cari teks/pattern dalam file di suatu direktori.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":   {"type": "string", "description": "Teks atau regex yang dicari"},
                    "directory": {"type": "string", "description": "Direktori yang dicari, default '.'"},
                    "extension": {"type": "string", "description": "Filter ekstensi, contoh: .py .js (opsional)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Cari informasi di internet menggunakan DuckDuckGo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query pencarian"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_dir",
            "description": "Buat direktori baru (beserta parent jika perlu).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path direktori yang akan dibuat"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Buka aplikasi atau file di sistem operasi. Bisa membuka aplikasi (Chrome, Notepad, VSCode, dll), folder, atau URL di browser default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Nama aplikasi (chrome, notepad, code, explorer), path file/folder, atau URL (https://...)"},
                    "args":   {"type": "array",  "items": {"type": "string"}, "description": "Argumen tambahan opsional, contoh: nama file yang dibuka"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Jalankan perintah shell/terminal (misal: pip install, npm run build, git status, pytest). Selalu meminta konfirmasi user sebelum eksekusi karena bisa berdampak luas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Perintah shell yang akan dijalankan"},
                    "cwd":     {"type": "string", "description": "Direktori tempat menjalankan command, relatif terhadap working directory. Default direktori kerja saat ini"},
                    "timeout": {"type": "integer", "description": "Batas waktu eksekusi dalam detik, default 60"}
                },
                "required": ["command"]
            }
        }
    },
]

# ─── Tool implementations ─────────────────────────────────────────────────────

def tool_read_file(path: str) -> str:
    p, err = check_path(path)
    if err: return err
    if not p.exists():    return f"❌ File tidak ditemukan: {p}"
    if not p.is_file():   return f"❌ Bukan file: {p}"
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        return f"❌ File terlalu besar ({size:,} bytes). Maksimum {MAX_FILE_BYTES:,} bytes."
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        lines   = content.splitlines()
        numbered = "\n".join(f"{i+1:>4} | {l}" for i, l in enumerate(lines))
        return f"📄 {p} ({len(lines)} baris)\n\n{numbered}"
    except Exception as e:
        return f"❌ Gagal membaca: {e}"


def tool_write_file(path: str, content: str) -> str:
    p, err = check_path(path)
    if err: return err
    exists = p.exists()
    action = "menimpa" if exists else "membuat"
    if not yn(f"AI ingin {action} file: {p}"):
        return "❌ Dibatalkan oleh user."
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        return f"✅ File {'ditimpa' if exists else 'dibuat'}: {p} ({lines} baris)"
    except Exception as e:
        return f"❌ Gagal menulis: {e}"


def tool_edit_file(path: str, old_str: str, new_str: str) -> str:
    p, err = check_path(path)
    if err: return err
    if not p.exists(): return f"❌ File tidak ditemukan: {p}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"❌ Gagal membaca: {e}"
    count = content.count(old_str)
    if count == 0:  return "❌ Teks yang dicari tidak ditemukan dalam file."
    if count > 1:   return f"❌ Teks ditemukan {count}x (harus unik). Perjelas konteks teks yang akan diganti."
    preview_old = old_str[:120].replace("\n", "↵ ")
    preview_new = new_str[:120].replace("\n", "↵ ")
    print(f"\n  {DIM}Dari:{R} {RED}{preview_old}{R}")
    print(f"  {DIM}Ke  :{R} {GREEN}{preview_new}{R}")
    if not yn(f"AI ingin edit file: {p}"):
        return "❌ Dibatalkan oleh user."
    try:
        new_content = content.replace(old_str, new_str, 1)
        p.write_text(new_content, encoding="utf-8")
        return f"✅ File diedit: {p}"
    except Exception as e:
        return f"❌ Gagal menulis: {e}"


def tool_list_dir(path: str = ".", recursive: bool = False) -> str:
    p, err = check_path(path)
    if err: return err
    if not p.exists():   return f"❌ Path tidak ditemukan: {p}"
    if not p.is_dir():   return f"❌ Bukan direktori: {p}"
    try:
        entries = []
        if recursive:
            for item in sorted(p.rglob("*")):
                rel  = item.relative_to(p)
                icon = "📁" if item.is_dir() else "📄"
                size = f"  {item.stat().st_size:>8,} B" if item.is_file() else ""
                entries.append(f"{icon} {rel}{size}")
        else:
            for item in sorted(p.iterdir()):
                icon = "📁" if item.is_dir() else "📄"
                size = f"  {item.stat().st_size:>8,} B" if item.is_file() else ""
                entries.append(f"{icon} {item.name}{size}")
        if not entries: return f"📂 {p} (kosong)"
        return f"📂 {p} ({len(entries)} item)\n\n" + "\n".join(entries)
    except Exception as e:
        return f"❌ Gagal: {e}"


def tool_search_in_files(pattern: str, directory: str = ".", extension: str = "") -> str:
    base, err = check_path(directory)
    if err: return err
    if not base.exists(): return f"❌ Direktori tidak ditemukan: {base}"
    results = []
    glob = f"*{extension}" if extension else "*"
    try:
        files = [f for f in base.rglob(glob) if f.is_file() and f.stat().st_size < MAX_FILE_BYTES]
        for fpath in sorted(files)[:50]:
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        rel = fpath.relative_to(base)
                        results.append(f"{rel}:{i}: {line.strip()}")
            except Exception:
                continue
        if not results:
            return f"🔍 Tidak ada hasil untuk '{pattern}' di {base}"
        out = "\n".join(results[:80])
        if len(out) > MAX_SEARCH_CHARS:
            out = out[:MAX_SEARCH_CHARS] + "\n... (terpotong)"
        return f"🔍 {len(results)} hasil untuk '{pattern}':\n\n{out}"
    except Exception as e:
        return f"❌ Error pencarian: {e}"


def tool_web_search(query: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    # ── 1. Coba HTML scrape (hasil lebih kaya) ──
    try:
        url  = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        raw     = resp.text
        results = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
        titles  = re.findall(r'class="result__a"[^>]*>(.*?)</a>', raw, re.DOTALL)
        def clean(s): return re.sub(r'<[^>]+>', '', s).strip()
        items = []
        for t, r in zip(titles[:5], results[:5]):
            ct, cr = clean(t), clean(r)
            if ct or cr:
                items.append(f"• {ct}\n  {cr}")
        if items:
            return f"🌐 Hasil web search: '{query}'\n\n" + "\n\n".join(items)
    except requests.exceptions.RequestException:
        pass  # lanjut ke fallback

    # ── 2. Fallback: DuckDuckGo Instant Answer API (lebih jarang diblokir) ──
    try:
        url  = "https://api.duckduckgo.com/"
        resp = requests.get(url, params={
            "q": query, "format": "json", "no_html": "1", "skip_disambig": "1"
        }, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        items = []
        if data.get("AbstractText"):
            items.append(f"• {data.get('Heading', query)}\n  {data['AbstractText']}")
        for topic in data.get("RelatedTopics", [])[:5]:
            if isinstance(topic, dict) and topic.get("Text"):
                items.append(f"• {topic['Text']}")
        if items:
            return f"🌐 Hasil web search: '{query}'\n\n" + "\n\n".join(items[:5])
        return f"🌐 Tidak ada hasil untuk: {query}"
    except requests.exceptions.ConnectionError as e:
        return (f"❌ Web search gagal: tidak bisa terhubung ke internet "
                f"(cek koneksi/firewall/DNS).\nDetail: {e}")
    except requests.exceptions.Timeout:
        return "❌ Web search gagal: timeout, coba lagi."
    except Exception as e:
        return f"❌ Web search gagal: {e}"


def tool_create_dir(path: str) -> str:
    p, err = check_path(path)
    if err: return err
    if p.exists(): return f"ℹ️ Direktori sudah ada: {p}"
    if not yn(f"AI ingin membuat direktori: {p}"):
        return "❌ Dibatalkan oleh user."
    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"✅ Direktori dibuat: {p}"
    except Exception as e:
        return f"❌ Gagal: {e}"


def tool_open_app(target: str, args: list = None) -> str:
    import shutil, platform
    args = args or []
    system = platform.system()  # Windows / Darwin / Linux

    # Alias nama populer → perintah nyata
    ALIASES = {
        # Windows
        "notepad":    "notepad.exe",
        "explorer":   "explorer.exe",
        "cmd":        "cmd.exe",
        "powershell": "powershell.exe",
        "calc":       "calc.exe",
        "paint":      "mspaint.exe",
        "taskmgr":    "taskmgr.exe",
        # Cross-platform
        "chrome":     "google-chrome" if system != "Windows" else "chrome",
        "firefox":    "firefox",
        "code":       "code",
        "vscode":     "code",
        "cursor":     "cursor",
        "sublime":    "subl",
        "vim":        "vim",
        "nano":       "nano",
    }

    resolved = ALIASES.get(target.lower(), target)

    # Kalau URL → buka di browser default
    if target.startswith("http://") or target.startswith("https://"):
        import webbrowser
        webbrowser.open(target)
        return f"✅ Membuka URL di browser: {target}"

    # Kalau path file/folder
    p = Path(target).expanduser()
    if p.exists():
        if system == "Windows":
            os.startfile(str(p))
        elif system == "Darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return f"✅ Membuka: {p}"

    # Buka sebagai aplikasi
    try:
        if system == "Windows":
            cmd = ["start", "", resolved] + args
            subprocess.Popen(cmd, shell=True)
        elif system == "Darwin":
            cmd = ["open", "-a", resolved] + args
            subprocess.Popen(cmd)
        else:
            cmd = [resolved] + args
            if not shutil.which(resolved):
                return f"❌ Aplikasi tidak ditemukan: {resolved}"
            subprocess.Popen(cmd)
        return f"✅ Membuka aplikasi: {resolved}" + (f" dengan argumen: {args}" if args else "")
    except Exception as e:
        return f"❌ Gagal membuka '{target}': {e}"


MAX_CMD_OUTPUT = 5_000  # batas karakter output command

def tool_run_command(command: str, cwd: str = "", timeout: int = 60) -> str:
    # Tentukan & validasi cwd
    if cwd:
        run_dir, err = check_path(cwd)
        if err: return err
        if not run_dir.is_dir():
            return f"❌ Direktori tidak ditemukan: {run_dir}"
    else:
        run_dir = ALLOWED_ROOT

    timeout = min(max(int(timeout or 60), 1), 300)  # clamp 1-300 detik

    print(f"\n  {DIM}Perintah:{R} {YELLOW}{command}{R}")
    print(f"  {DIM}Direktori:{R} {run_dir}")
    if not yn(f"AI ingin menjalankan perintah shell di atas"):
        return "❌ Dibatalkan oleh user."

    try:
        result = subprocess.run(
            command, shell=True, cwd=str(run_dir),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        out = (result.stdout or "") + (result.stderr or "")
        out = out.strip() or "(tidak ada output)"
        if len(out) > MAX_CMD_OUTPUT:
            out = out[:MAX_CMD_OUTPUT] + f"\n... (terpotong, total {len(out):,} karakter)"
        status = "✅" if result.returncode == 0 else f"⚠️ exit code {result.returncode}"
        return f"{status}\n$ {command}\n\n{out}"
    except subprocess.TimeoutExpired:
        return f"❌ Perintah timeout setelah {timeout} detik: {command}"
    except Exception as e:
        return f"❌ Gagal menjalankan perintah: {e}"


TOOL_MAP = {
    "read_file":       lambda a: tool_read_file(**a),
    "write_file":      lambda a: tool_write_file(**a),
    "edit_file":       lambda a: tool_edit_file(**a),
    "list_dir":        lambda a: tool_list_dir(**a),
    "search_in_files": lambda a: tool_search_in_files(**a),
    "web_search":      lambda a: tool_web_search(**a),
    "create_dir":      lambda a: tool_create_dir(**a),
    "open_app":        lambda a: tool_open_app(**a),
    "run_command":     lambda a: tool_run_command(**a),
}

# ─── API (Ollama & OpenRouter) ────────────────────────────────────────────────

def call_api_stream(model: str, messages: list, api_key: str = "", use_tools: bool = True):
    """Generator: yield dict chunk dari respons stream.
    Format output dinormalisasi agar process_response tidak perlu tahu providernya:
      chunk["message"]["content"]    — potongan teks
      chunk["message"]["tool_calls"] — list tool calls (opsional)
      chunk["done"]                  — True ketika stream selesai
    """
    if PROVIDER == "openrouter":
        yield from _stream_openrouter(model, messages, api_key, use_tools)
    else:
        yield from _stream_ollama(model, messages, use_tools)


def _stream_ollama(model: str, messages: list, use_tools: bool = True):
    """Stream dari Ollama — NDJSON, format asli sudah cocok."""
    body = {
        "model":    model,
        "messages": messages,
        "stream":   True,
        "options":  {"num_predict": 4096},
    }
    if use_tools:
        body["tools"] = TOOLS

    resp = requests.post(OLLAMA_URL, json=body, timeout=120, stream=True)
    if not resp.ok:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    for line in resp.iter_lines():
        if not line:
            continue
        try:
            yield json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue


def _stream_openrouter(model: str, messages: list, api_key: str, use_tools: bool = True):
    """Stream dari OpenRouter — SSE format (data: {...}).
    Normalisasi output ke format yang sama dengan Ollama."""
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY belum diset. Jalankan /apikey untuk mengisinya.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/my-ai-cli",   # opsional, untuk analytics OR
        "X-Title":       "My AI CLI",
    }
    body = {
        "model":    model,
        "messages": messages,
        "stream":   True,
        "max_tokens": 4096,
    }
    if use_tools:
        body["tools"] = TOOLS

    resp = requests.post(OPENROUTER_URL, json=body, headers=headers, timeout=120, stream=True)
    if not resp.ok:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:400]}")

    # Kumpulkan tool_call fragments (OpenRouter kirim delta per-chunk)
    accumulated_tool_calls: dict = {}  # index → {id, type, function:{name, arguments}}

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8").strip()
        if line == "data: [DONE]":
            # Flush tool_calls yang sudah terkumpul
            if accumulated_tool_calls:
                tcs = _finalize_tool_calls(accumulated_tool_calls)
                yield {"message": {"content": "", "tool_calls": tcs}, "done": False}
            yield {"message": {"content": ""}, "done": True}
            return
        if not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        choice = (data.get("choices") or [{}])[0]
        delta  = choice.get("delta", {})

        # Teks biasa
        text_piece = delta.get("content") or ""

        # Tool call deltas (OpenAI-style streaming tool calls)
        tc_deltas = delta.get("tool_calls") or []
        for tc_delta in tc_deltas:
            idx = tc_delta.get("index", 0)
            if idx not in accumulated_tool_calls:
                accumulated_tool_calls[idx] = {
                    "id":       tc_delta.get("id", ""),
                    "type":     "function",
                    "function": {"name": "", "arguments": ""},
                }
            acc = accumulated_tool_calls[idx]
            fn  = tc_delta.get("function", {})
            if fn.get("name"):
                acc["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                acc["function"]["arguments"] += fn["arguments"]
            if tc_delta.get("id"):
                acc["id"] = tc_delta["id"]

        finish_reason = choice.get("finish_reason")
        is_done = finish_reason in ("stop", "tool_calls", "length")

        yield {"message": {"content": text_piece}, "done": False}

        if is_done:
            if accumulated_tool_calls:
                tcs = _finalize_tool_calls(accumulated_tool_calls)
                yield {"message": {"content": "", "tool_calls": tcs}, "done": False}
            yield {"message": {"content": ""}, "done": True}
            return


def _finalize_tool_calls(accumulated: dict) -> list:
    """Ubah dict index→fragment menjadi list tool_calls standar (seperti Ollama)."""
    result = []
    for idx in sorted(accumulated):
        tc  = accumulated[idx]
        raw = tc["function"]["arguments"]
        try:
            args = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            args = {"_raw": raw}
        result.append({
            "id":   tc.get("id", f"call_{idx}"),
            "type": "function",
            "function": {
                "name":      tc["function"]["name"],
                "arguments": args,
            },
        })
    return result


def process_response(api_key: str, model: str, messages: list) -> str:
    """Agentic loop dengan streaming: print teks live, handle tool_calls."""
    while True:
        full_content = ""
        tool_calls    = []
        printed_label = False

        for chunk in call_api_stream(model, messages, api_key):
            msg = chunk.get("message", {})

            # Streaming teks → print langsung token-per-token
            piece = msg.get("content", "")
            if piece:
                if not printed_label:
                    print(f"\n{CYAN}{BOLD}AI ▸{R} ", end="", flush=True)
                    printed_label = True
                print(piece, end="", flush=True)
                full_content += piece

            # tool_calls biasanya muncul utuh di chunk terakhir
            if msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]

            if chunk.get("done"):
                break

        if printed_label:
            print()  # newline setelah selesai streaming teks

        # Susun pesan assistant lengkap untuk history
        assistant_msg = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        # Kalau tidak ada tool call → selesai, kembalikan teks (sudah diprint)
        if not tool_calls:
            if VOICE_ENABLED and full_content.strip():
                status = speak_text(full_content)
                if status.startswith("❌"):
                    print(f"  {DIM}{status}{R}")
            return ""  # konten sudah diprint live, tidak perlu diprint ulang

        # Eksekusi semua tool calls
        for tc in tool_calls:
            # Ollama: tc["function"]["name"] dan tc["function"]["arguments"] (dict langsung)
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except Exception:
                    fn_args = {}

            # Tampilkan apa yang AI lakukan
            print(f"\n  {MAGENTA}🔧 {fn_name}{R}({DIM}{', '.join(f'{k}={repr(v)[:60]}' for k,v in fn_args.items())}{R})")

            if fn_name in TOOL_MAP:
                result = TOOL_MAP[fn_name](fn_args)
            else:
                result = f"❌ Tool tidak dikenal: {fn_name}"

            # Tampilkan preview hasil
            preview = result[:200].replace("\n", " ")
            print(f"  {DIM}→ {preview}{'...' if len(result)>200 else ''}{R}\n")

            # Kirim hasil tool ke messages (format Ollama)
            messages.append({
                "role":    "tool",
                "content": result,
            })


# ─── Config (.env) ────────────────────────────────────────────────────────────

def load_config() -> dict:
    return {
        "api_key":       os.environ.get("OPENROUTER_API_KEY", ""),
        "provider":      os.environ.get("MY_AI_PROVIDER", ""),       # "ollama" / "openrouter"
        "default_model": os.environ.get("MY_AI_DEFAULT_MODEL", ""),
    }

def save_config(data: dict):
    env_file = Path(__file__).parent / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        for key, val in data.items():
            env_key = {
                "api_key":       "OPENROUTER_API_KEY",
                "provider":      "MY_AI_PROVIDER",
                "default_model": "MY_AI_DEFAULT_MODEL",
            }.get(key, key.upper())
            found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{env_key}="):
                    lines[i] = f"{env_key}={val}"
                    found = True
                    break
            if not found:
                lines.append(f"{env_key}={val}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass

# ─── History ──────────────────────────────────────────────────────────────────

def load_history() -> list:
    if HISTORY_FILE.exists():
        try: return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except: return []
    return []

def save_history(history: list):
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

def show_history(history: list):
    if not history:
        print(f"\n{YELLOW}Belum ada history.{R}\n"); return
    print(f"\n{BOLD}{'─'*56}{R}")
    print(f"{BOLD}  📋 History{R}")
    print(f"{BOLD}{'─'*56}{R}")
    for i, e in enumerate(history[-15:], 1):
        ts  = e.get("timestamp","")[:16].replace("T"," ")
        msg = e.get("user_first","")[:38]
        mdl = e.get("model","").split("/")[-1][:20]
        print(f"  {DIM}{i:>2}.{R} {WHITE}{msg:<38}{R}  {DIM}{ts}  {mdl}{R}")
    print(f"{BOLD}{'─'*56}{R}\n")


def list_sessions(history: list) -> list:
    """Tampilkan daftar sesi yang bisa di-resume, return list referensi sesi."""
    if not history:
        print(f"\n{YELLOW}Belum ada sesi tersimpan.{R}\n"); return []
    shown = history[-15:]
    print(f"\n{BOLD}{'─'*56}{R}")
    print(f"{BOLD}  💬 Sesi tersimpan (ketik nomor untuk resume){R}")
    print(f"{BOLD}{'─'*56}{R}")
    for i, e in enumerate(shown, 1):
        ts    = e.get("timestamp","")[:16].replace("T"," ")
        msg   = e.get("user_first","")[:38]
        mdl   = e.get("model","").split("/")[-1][:20]
        nmsg  = len([m for m in e.get("messages",[]) if m.get("role") in ("user","assistant")])
        print(f"  {CYAN}{i:>2}{R}. {WHITE}{msg:<38}{R}  {DIM}{ts}  {mdl}  ({nmsg} pesan){R}")
    print(f"{BOLD}{'─'*56}{R}")
    return shown


def load_session_messages(entry: dict, system_msg: dict) -> list:
    """Bangun ulang list `messages` dari entry history, prefix dengan system_msg."""
    restored = [system_msg]
    for m in entry.get("messages", []):
        # Skip pesan tool/tool_calls yang bisa membingungkan model saat resume
        if m.get("role") == "tool":
            continue
        if m.get("role") == "assistant" and m.get("tool_calls") and not m.get("content"):
            continue
        restored.append({k: v for k, v in m.items() if k in ("role", "content")})
    return restored

# ─── Setup prompts ────────────────────────────────────────────────────────────

def fetch_ollama_models() -> list:
    """Ambil daftar model yang sudah di-pull di Ollama lokal."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if not resp.ok: return []
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def pilih_model(default_model: str = "") -> tuple:
    global PROVIDER

    if PROVIDER == "openrouter":
        options = dict(MODELS_OPENROUTER)
        label   = "OpenRouter"
        note    = "(cloud — butuh API key)"
    else:
        detected = fetch_ollama_models()
        if detected:
            options = {str(i): (name, name) for i, name in enumerate(detected, 1)}
            note    = "(terdeteksi dari 'ollama list')"
        else:
            options = dict(MODELS_OLLAMA)
            note    = "(fallback — Ollama tidak terdeteksi)"
        label = "Ollama"

    custom_key  = str(len(options) + 1)
    default_key = next((k for k, (mid, _) in options.items() if mid == default_model), "1")

    print(f"{BOLD}Pilih model {CYAN}[{label}]{R}:{R}")
    print(f"  {DIM}{note}{R}")
    for k, (mid, desc) in options.items():
        marker = f"  {GREEN}← tersimpan{R}" if mid == default_model else ""
        print(f"  {CYAN}{k}{R}. {desc}{marker}")
    print(f"  {CYAN}{custom_key}{R}. Ketik model ID sendiri\n")

    while True:
        p = input(f"{DIM}Pilih [1-{custom_key}, default={default_key}]: {R}").strip() or default_key
        if p in options:
            mid, desc = options[p]
            save_config({"default_model": mid})
            print(f"\n{GREEN}✓ {desc}{R}\n"); return mid, desc
        elif p == custom_key:
            mid = input("Model ID (contoh: mistralai/mixtral-8x7b-instruct): ").strip()
            if mid:
                save_config({"default_model": mid})
                print(f"\n{GREEN}✓ {mid}{R}\n"); return mid, mid
        else:
            print(f"{RED}Tidak valid.{R}")


def save_to_txt(messages: list, persona: str, model: str):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fn = Path(f"my_ai_chat_{ts}.txt")
    lines = [f"My AI Chat — {ts}\nPersona: {persona}\nModel: {model}\n{'─'*50}\n"]
    for m in messages:
        role = m.get("role","")
        if role == "system": continue
        if role == "tool":
            lines.append(f"[Tool Result]:\n{m.get('content','')}\n")
        elif role == "assistant":
            content = m.get("content") or ""
            tc = m.get("tool_calls")
            if tc: lines.append(f"AI (tool call): {json.dumps(tc, ensure_ascii=False)[:200]}\n")
            if content: lines.append(f"AI:\n{content}\n")
        else:
            lines.append(f"Kamu:\n{m.get('content','')}\n")
    fn.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{GREEN}✓ Disimpan ke: {fn.resolve()}{R}\n")

def help_text():
    print(f"""
{BOLD}Perintah:{R}
  {CYAN}/clear{R}    — Reset konteks percakapan
  {CYAN}/history{R}  — Lihat history chat
  {CYAN}/sessions{R} — Lihat & lanjutkan (resume) sesi lama
  {CYAN}/voice{R}    — Aktifkan/nonaktifkan suara AI (VOICEVOX). /voice on|off|list|set <id>
  {CYAN}/persona{R}  — Ganti persona
  {CYAN}/model{R}    — Ganti model AI
  {CYAN}/cwd{R}      — Tampilkan & ganti working directory
  {CYAN}/tools{R}    — Lihat tools yang tersedia
  {CYAN}/save{R}     — Export percakapan ke .txt
  {CYAN}/apikey{R}   — Set/ganti OpenRouter API key (tidak diperlukan untuk Ollama)
  {CYAN}/info{R}     — Info provider, model & persona aktif
  {CYAN}/help{R}     — Pesan ini
  {CYAN}/exit{R}     — Keluar

{BOLD}Tips:{R}
  Kamu bisa langsung bilang ke AI:
  {DIM}"baca file main.py"{R}
  {DIM}"cari semua fungsi yang pakai requests di folder ini"{R}
  {DIM}"buat file config.json dengan isi ..."{R}
  {DIM}"cari di internet cara install flask"{R}

{BOLD}Provider:{R}
  {CYAN}Ollama{R}      — Jalankan model lokal. Butuh: ollama serve
  {CYAN}OpenRouter{R}  — 300+ model cloud (termasuk gratis). Butuh: API key dari openrouter.ai/keys
""")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global PROVIDER

    print(f"\n{BOLD}{CYAN}{'═'*54}{R}")
    print(f"{BOLD}{CYAN}   🤖  My AI CLI  —  Ollama / OpenRouter + Tools{R}")
    print(f"{BOLD}{CYAN}{'═'*54}{R}\n")

    cfg = load_config()

    # ── Pilih provider ──────────────────────────────────────────────────────
    saved_provider = cfg.get("provider", "")
    ollama_ok      = False

    # Cek apakah Ollama bisa dijangkau
    try:
        ping = requests.get("http://localhost:11434/api/tags", timeout=3)
        ollama_ok = ping.ok
    except Exception:
        pass

    print(f"{BOLD}Pilih provider:{R}")
    ollama_mark = f"  {GREEN}← terdeteksi{R}" if ollama_ok else f"  {DIM}(tidak berjalan){R}"
    or_mark     = f"  {GREEN}← tersimpan{R}" if saved_provider == "openrouter" else ""
    ol_mark     = (f"  {GREEN}← tersimpan{R}" if saved_provider == "ollama" else "") + ollama_mark
    print(f"  {CYAN}1{R}. Ollama  (lokal){ol_mark}")
    print(f"  {CYAN}2{R}. OpenRouter (cloud){or_mark}\n")

    default_prov = "1" if (saved_provider == "ollama" or not saved_provider) else "2"
    prov_input   = input(f"{DIM}Pilih [1/2, default={default_prov}]: {R}").strip() or default_prov

    if prov_input == "2":
        PROVIDER = "openrouter"
        save_config({"provider": "openrouter"})
        print(f"\n{GREEN}✓ Provider: OpenRouter{R}")

        # Minta / tampilkan API key
        api_key = cfg.get("api_key", "")
        if api_key:
            masked = api_key[:8] + "…" + api_key[-4:]
            print(f"  {DIM}API key tersimpan: {masked}{R}")
            ganti = input(f"  Ganti API key? [y/N]: ").strip().lower()
            if ganti == "y":
                api_key = input(f"  Masukkan OPENROUTER_API_KEY baru: ").strip()
                if api_key:
                    save_config({"api_key": api_key})
                    print(f"  {GREEN}✓ API key disimpan.{R}")
        else:
            print(f"\n  {YELLOW}⚠ OPENROUTER_API_KEY belum diset.{R}")
            print(f"  Daftar gratis di {CYAN}https://openrouter.ai/keys{R}")
            api_key = input(f"  Masukkan API key (kosongkan=skip): ").strip()
            if api_key:
                save_config({"api_key": api_key})
                print(f"  {GREEN}✓ API key disimpan ke .env{R}")
            else:
                print(f"  {DIM}Lanjut tanpa API key (akan error saat memanggil model).{R}")
        print()
    else:
        PROVIDER = "ollama"
        save_config({"provider": "ollama"})
        api_key  = ""
        if ollama_ok:
            print(f"{GREEN}✓ Provider: Ollama (terhubung){R}")
        else:
            print(f"{YELLOW}⚠ Provider: Ollama — server tidak terdeteksi. Jalankan: ollama serve{R}")
            if not yn("Tetap lanjutkan?"):
                return
        print()

    default_model = cfg.get("default_model", "")
    model_id, model_desc = pilih_model(default_model)
    persona_nama, sys_prompt = PERSONA_NAMA, PERSONA_PROMPT

    cwd = Path(".").resolve()
    set_allowed_root(cwd)
    print(f"{DIM}Working directory: {cwd}{R}")
    print(f"{DIM}Ketik /help untuk melihat perintah.{R}\n")

    system_msg = {
        "role": "system",
        "content": (
            f"{sys_prompt}\n\n"
            f"Working directory saat ini: {cwd}\n"
            "Kamu memiliki akses ke tools: read_file, write_file, edit_file, "
            "list_dir, search_in_files, web_search, create_dir.\n"
            "Gunakan tools ini secara proaktif untuk membantu user. "
            "Sebelum menulis atau mengedit file, selalu jelaskan apa yang akan kamu lakukan. "
            "Untuk tugas besar, pecah menjadi langkah-langkah kecil."
        )
    }
    messages     = [system_msg]
    all_history  = load_history()
    current_session_idx = None  # index di all_history untuk sesi yang sedang aktif (None = sesi baru)

    while True:
        try:
            user_input = input(f"\n{BOLD}Kamu ▸{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n{YELLOW}Sampai jumpa!{R}\n"); break

        if not user_input: continue

        if user_input.startswith("/"):
            cmd = user_input.lower().split()[0]

            if cmd == "/exit":
                print(f"\n{YELLOW}Sampai jumpa!{R}\n"); break

            elif cmd == "/clear":
                messages = [system_msg]
                current_session_idx = None
                print(f"\n{GREEN}✓ Konteks dihapus.{R}")

            elif cmd == "/history":
                show_history(all_history)

            elif cmd == "/sessions":
                shown = list_sessions(all_history)
                if shown:
                    sel = input(f"  {DIM}Nomor sesi (kosongkan=batal): {R}").strip()
                    if sel.isdigit() and 1 <= int(sel) <= len(shown):
                        entry = shown[int(sel)-1]
                        messages = load_session_messages(entry, system_msg)
                        # cari index asli di all_history
                        current_session_idx = all_history.index(entry)
                        n = len([m for m in messages if m["role"] in ("user","assistant")])
                        print(f"\n{GREEN}✓ Sesi dilanjutkan ({n} pesan dimuat).{R}")
                    else:
                        print(f"\n{YELLOW}Dibatalkan.{R}")

            elif cmd == "/persona":
                print(f"\n{DIM}Persona tunggal aktif: {CYAN}{PERSONA_NAMA}{R}\n")

            elif cmd == "/model":
                default_model = cfg.get("default_model", "")
                model_id, model_desc = pilih_model(default_model)
                messages = [system_msg]
                current_session_idx = None
                print(f"{DIM}Konteks direset. Provider: {'OpenRouter' if PROVIDER == 'openrouter' else 'Ollama'}{R}")

            elif cmd == "/cwd":
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1:
                    new_cwd = Path(parts[1]).expanduser().resolve()
                    if new_cwd.is_dir():
                        os.chdir(new_cwd); cwd = new_cwd
                        set_allowed_root(cwd)
                        system_msg["content"] = re.sub(
                            r"Working directory saat ini: .*",
                            f"Working directory saat ini: {cwd}",
                            system_msg["content"]
                        )
                        print(f"\n{GREEN}✓ Pindah ke: {cwd}{R}")
                    else:
                        print(f"\n{RED}❌ Direktori tidak ditemukan: {new_cwd}{R}")
                else:
                    print(f"\n  Working directory: {CYAN}{cwd}{R}")
                    new_path = input(f"  Ganti ke (kosongkan=batal): ").strip()
                    if new_path:
                        new_cwd = Path(new_path).expanduser().resolve()
                        if new_cwd.is_dir():
                            os.chdir(new_cwd); cwd = new_cwd
                            set_allowed_root(cwd)
                            system_msg["content"] = re.sub(
                                r"Working directory saat ini: .*",
                                f"Working directory saat ini: {cwd}",
                                system_msg["content"]
                            )
                            print(f"{GREEN}✓ Pindah ke: {cwd}{R}")
                        else:
                            print(f"{RED}❌ Tidak ditemukan.{R}")

            elif cmd == "/tools":
                print(f"\n{BOLD}Tools tersedia:{R}")
                for t in TOOLS:
                    fn = t["function"]
                    print(f"  {CYAN}{fn['name']}{R} — {fn['description']}")
                print()

            elif cmd == "/save":
                chat = [m for m in messages if m["role"] != "system"]
                if chat: save_to_txt(messages, persona_nama, model_id)
                else: print(f"\n{YELLOW}Belum ada percakapan.{R}")

            elif cmd == "/apikey":
                if PROVIDER == "ollama":
                    print(f"\n{YELLOW}Ollama berjalan lokal, tidak memerlukan API key.{R}")
                    print(f"{DIM}Untuk menggunakan OpenRouter, restart dan pilih provider OpenRouter.{R}\n")
                else:
                    parts = user_input.split(maxsplit=1)
                    if len(parts) > 1:
                        new_key = parts[1].strip()
                    else:
                        masked = api_key[:8] + "…" + api_key[-4:] if api_key else "(belum diset)"
                        print(f"\n  API key saat ini: {DIM}{masked}{R}")
                        new_key = input(f"  Masukkan OPENROUTER_API_KEY baru (kosongkan=batal): ").strip()
                    if new_key:
                        api_key = new_key
                        save_config({"api_key": api_key})
                        print(f"\n{GREEN}✓ API key disimpan.{R}\n")
                    else:
                        print(f"\n{YELLOW}Dibatalkan.{R}\n")

            elif cmd == "/voice":
                global VOICE_ENABLED, VOICEVOX_SPEAKER
                parts = user_input.split(maxsplit=1)
                arg = parts[1].lower() if len(parts) > 1 else ""

                if arg == "on":
                    if not voicevox_check():
                        print(f"\n{RED}❌ VOICEVOX tidak terdeteksi di {VOICEVOX_URL}.{R}")
                        print(f"{DIM}Buka aplikasi VOICEVOX terlebih dahulu, lalu coba lagi.{R}\n")
                    else:
                        VOICE_ENABLED = True
                        print(f"\n{GREEN}✓ Voice aktif. Setiap balasan AI akan dibacakan.{R}\n")
                elif arg == "off":
                    VOICE_ENABLED = False
                    print(f"\n{GREEN}✓ Voice nonaktif.{R}\n")
                elif arg == "list":
                    speakers = voicevox_speakers()
                    if not speakers:
                        print(f"\n{RED}❌ Tidak bisa ambil daftar speaker. VOICEVOX terbuka?{R}\n")
                    else:
                        print(f"\n{BOLD}Speaker VOICEVOX tersedia:{R}")
                        for sid, name in speakers:
                            mark = f"  {GREEN}← aktif{R}" if sid == VOICEVOX_SPEAKER else ""
                            print(f"  {CYAN}{sid:>3}{R}  {name}{mark}")
                        print(f"\n{DIM}Ganti dengan: /voice set <id>{R}\n")
                elif arg.startswith("set"):
                    sub = user_input.split()
                    if len(sub) >= 3 and sub[2].isdigit():
                        VOICEVOX_SPEAKER = int(sub[2])
                        print(f"\n{GREEN}✓ Speaker diubah ke ID {VOICEVOX_SPEAKER}.{R}")
                        print(f"{DIM}Lihat nama: /voice list{R}\n")
                    else:
                        print(f"\n{YELLOW}Pakai: /voice set <id_speaker>{R}\n")
                else:
                    status = f"{GREEN}AKTIF{R}" if VOICE_ENABLED else f"{DIM}nonaktif{R}"
                    connected = f"{GREEN}terhubung{R}" if voicevox_check() else f"{RED}tidak terhubung{R}"
                    print(f"\n  Voice  : {status}")
                    print(f"  VOICEVOX: {connected}  {DIM}({VOICEVOX_URL}){R}")
                    print(f"  Speaker : {VOICEVOX_SPEAKER}")
                    print(f"\n{DIM}Pakai: /voice on | off | list | set <id>{R}\n")

            elif cmd == "/info":
                voice_status = f"{GREEN}ON{R}" if VOICE_ENABLED else f"{DIM}OFF{R}"
                prov_label   = f"{CYAN}OpenRouter{R}" if PROVIDER == "openrouter" else f"{CYAN}Ollama{R}"
                print(f"\n  Provider: {prov_label}")
                print(f"  Model  : {CYAN}{model_desc}{R}  {DIM}({model_id}){R}")
                print(f"  Persona: {CYAN}{PERSONA_NAMA}{R}")
                print(f"  CWD    : {CYAN}{cwd}{R}")
                print(f"  Voice  : {voice_status}\n")

            elif cmd == "/help":
                help_text()
            else:
                print(f"\n{RED}Tidak dikenal. Ketik /help.{R}")
            continue

        # ── Kirim ke API ──
        messages.append({"role": "user", "content": user_input})

        try:
            process_response(api_key, model_id, messages)
        except ValueError as e:
            print(f"\n{RED}❌ {e}{R}\n"); messages.pop(); continue
        except requests.exceptions.ConnectionError:
            if PROVIDER == "openrouter":
                print(f"\n{RED}❌ Tidak bisa terhubung ke OpenRouter. Cek koneksi internet.{R}\n")
            else:
                print(f"\n{RED}❌ Tidak bisa terhubung ke Ollama. Pastikan 'ollama serve' berjalan.{R}\n")
            messages.pop(); continue
        except Exception as e:
            print(f"\n{RED}❌ Error: {e}{R}\n"); messages.pop(); continue

        print()

        # Simpan history
        chat_msgs = [m for m in messages if m["role"] not in ("system","tool")
                     and not m.get("tool_calls")]
        first_user = next((m["content"] for m in chat_msgs if m["role"]=="user"), "")
        entry = {
            "timestamp":  datetime.datetime.now().isoformat(),
            "persona":    persona_nama,
            "model":      model_id,
            "user_first": first_user,
            "messages":   [m for m in messages if m["role"] != "system"],
        }
        if current_session_idx is not None and 0 <= current_session_idx < len(all_history):
            entry["user_first"] = all_history[current_session_idx].get("user_first", first_user)
            all_history[current_session_idx] = entry
        elif len(chat_msgs) <= 2:
            all_history.append(entry)
            current_session_idx = len(all_history) - 1
        elif all_history:
            all_history[-1] = entry
        save_history(all_history)

if __name__ == "__main__":
    main()