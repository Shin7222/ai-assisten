#!/usr/bin/env python3
"""
My AI CLI — OpenRouter + Tool Calling
Fitur: baca/tulis file, edit kode, web search, akses folder
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

HISTORY_FILE     = Path.home() / ".my_ai_history.json"
OPENROUTER_URL   = "https://api.openai.com/v1/chat/completions"
MAX_FILE_BYTES   = 100_000   # batas baca file ~100 KB
MAX_SEARCH_CHARS = 3_000     # batas karakter hasil search

MODELS = {
    "1": ("anthropic/claude-3.5-haiku",             "Claude 3.5 Haiku"),
    "2": ("anthropic/claude-sonnet-4",               "Claude Sonnet 4"),
    "3": ("anthropic/claude-opus-4",                 "Claude Opus 4"),
    "4": ("google/gemini-2.0-flash-exp:free",        "Gemini 2.0 Flash (gratis)"),
    "5": ("meta-llama/llama-3.3-70b-instruct:free",  "Llama 3.3 70B (gratis)"),
}

PERSONA_NAMA   = "My AI"
PERSONA_PROMPT = (
    "Kamu adalah My AI, asisten pribadi yang serba bisa. Kemampuanmu meliputi:\n"
    "- Asisten umum: menjawab pertanyaan, menulis, merangkum, brainstorming.\n"
    "- Developer: membaca, menulis, mengedit, dan menjelaskan kode di berbagai bahasa.\n"
    "- File manager: mengorganisasi, mencari, dan memodifikasi file dan folder.\n"
    "- Researcher: mencari informasi terkini di internet lalu merangkumnya.\n\n"
    "Gunakan tools yang tersedia secara proaktif tanpa perlu diminta eksplisit. "
    "Selalu jelaskan dengan singkat apa yang sedang kamu lakukan sebelum mengeksekusi tool. "
    "Untuk tugas besar, pecah menjadi langkah-langkah kecil dan laporkan progresnya."
)

# ─── Warna ────────────────────────────────────────────────────────────────────

R="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
CYAN="\033[96m"; GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"; MAGENTA="\033[95m"; WHITE="\033[97m"

def c(text, color): return f"{color}{text}{R}"
def yn(prompt): return input(f"{YELLOW}⚠ {prompt} [y/N]: {R}").strip().lower() == "y"

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
]

# ─── Tool implementations ─────────────────────────────────────────────────────

def tool_read_file(path: str) -> str:
    p = Path(path).expanduser().resolve()
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
    p = Path(path).expanduser().resolve()
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
    p = Path(path).expanduser().resolve()
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
    p = Path(path).expanduser().resolve()
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
    base = Path(directory).expanduser().resolve()
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
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        url     = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        resp    = requests.get(url, headers=headers, timeout=10)
        raw     = resp.text
        results = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
        titles  = re.findall(r'class="result__a"[^>]*>(.*?)</a>', raw, re.DOTALL)
        def clean(s): return re.sub(r'<[^>]+>', '', s).strip()
        items   = []
        for t, r in zip(titles[:5], results[:5]):
            items.append(f"• {clean(t)}\n  {clean(r)}")
        if not items:
            return f"🌐 Tidak ada hasil untuk: {query}"
        return f"🌐 Hasil web search: '{query}'\n\n" + "\n\n".join(items)
    except Exception as e:
        return f"❌ Web search gagal: {e}"


def tool_create_dir(path: str) -> str:
    p = Path(path).expanduser().resolve()
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


TOOL_MAP = {
    "read_file":       lambda a: tool_read_file(**a),
    "write_file":      lambda a: tool_write_file(**a),
    "edit_file":       lambda a: tool_edit_file(**a),
    "list_dir":        lambda a: tool_list_dir(**a),
    "search_in_files": lambda a: tool_search_in_files(**a),
    "web_search":      lambda a: tool_web_search(**a),
    "create_dir":      lambda a: tool_create_dir(**a),
    "open_app":        lambda a: tool_open_app(**a),
}

# ─── OpenRouter API ───────────────────────────────────────────────────────────

def call_api(api_key: str, model: str, messages: list, use_tools: bool = True) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://my-ai-cli",
        "X-Title":       "My AI CLI",
    }
    body = {"model": model, "messages": messages, "max_tokens": 4096}
    if use_tools:
        body["tools"] = TOOLS

    resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=120)
    if resp.status_code == 401: raise ValueError("API key tidak valid.")
    if resp.status_code == 402: raise ValueError("Saldo OpenRouter habis.")
    if resp.status_code == 429: raise ValueError("Rate limit. Tunggu sebentar.")
    if not resp.ok:             raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def process_response(api_key: str, model: str, messages: list) -> str:
    """Agentic loop: terus panggil API sampai tidak ada tool call."""
    while True:
        data    = call_api(api_key, model, messages)
        choice  = data["choices"][0]
        message = choice["message"]
        finish  = choice.get("finish_reason", "")

        # Tambah respons assistant ke messages
        messages.append(message)

        # Kalau tidak ada tool call → kembalikan teks
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return message.get("content") or ""

        # Eksekusi semua tool calls
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
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

            # Kirim hasil tool ke messages
            messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })


# ─── Config (.env) ────────────────────────────────────────────────────────────

def load_config() -> dict:
    return {
        "api_key":       os.environ.get("OPENROUTER_API_KEY", ""),
        "default_model": os.environ.get("MY_AI_DEFAULT_MODEL", ""),
    }

def save_config(data: dict):
    env_file = Path(__file__).parent / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        for key, val in data.items():
            env_key = {
                "api_key":       "OPENROUTER_API_KEY",
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
    try: HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
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

# ─── Setup prompts ────────────────────────────────────────────────────────────

def pilih_model(default_model: str = "") -> tuple:
    # Cari nomor default dari config
    default_key = "1"
    for k,(mid,_) in MODELS.items():
        if mid == default_model:
            default_key = k; break

    print(f"{BOLD}Pilih model:{R}")
    for k,(mid,desc) in MODELS.items():
        marker = f"  {GREEN}← tersimpan{R}" if mid == default_model else ""
        print(f"  {CYAN}{k}{R}. {desc}  {DIM}({mid}){R}{marker}")
    print(f"  {CYAN}6{R}. Ketik model ID sendiri\n")
    while True:
        p = input(f"{DIM}Pilih [1-6, default={default_key}]: {R}").strip() or default_key
        if p in MODELS:
            mid, desc = MODELS[p]
            save_config({"default_model": mid})
            print(f"\n{GREEN}✓ {desc}{R}\n"); return mid, desc
        elif p == "6":
            mid = input("Model ID: ").strip()
            if mid:
                save_config({"default_model": mid})
                print(f"\n{GREEN}✓ {mid}{R}\n"); return mid, mid
        else: print(f"{RED}Tidak valid.{R}")


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
  {CYAN}/persona{R}  — Ganti persona
  {CYAN}/model{R}    — Ganti model AI
  {CYAN}/cwd{R}      — Tampilkan & ganti working directory
  {CYAN}/tools{R}    — Lihat tools yang tersedia
  {CYAN}/save{R}     — Export percakapan ke .txt
  {CYAN}/apikey{R}   — Ganti & simpan API key
  {CYAN}/info{R}     — Info model & persona aktif
  {CYAN}/help{R}     — Pesan ini
  {CYAN}/exit{R}     — Keluar

{BOLD}Tips:{R}
  Kamu bisa langsung bilang ke AI:
  {DIM}"baca file main.py"{R}
  {DIM}"cari semua fungsi yang pakai requests di folder ini"{R}
  {DIM}"buat file config.json dengan isi ..."{R}
  {DIM}"cari di internet cara install flask"{R}
""")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{CYAN}{'═'*54}{R}")
    print(f"{BOLD}{CYAN}   🤖  My AI CLI  —  OpenRouter + Tools{R}")
    print(f"{BOLD}{CYAN}{'═'*54}{R}\n")

    cfg     = load_config()
    api_key = os.environ.get("OPENROUTER_API_KEY","").strip() or cfg.get("api_key","")
    if not api_key:
        print(f"{YELLOW}API key belum disimpan.{R}")
        print(f"{DIM}Daftar gratis: https://openrouter.ai/keys{R}\n")
        api_key = input("Masukkan OPENROUTER_API_KEY: ").strip()
        if not api_key:
            print(f"{RED}❌ API key diperlukan.{R}"); return
        save_config({"api_key": api_key})
        env_path = Path(__file__).parent / ".env"
        print(f"{GREEN}✓ API key disimpan di {env_path}{R}\n")
    else:
        print(f"{DIM}API key loaded ✓{R}")

    default_model = cfg.get("default_model", "")
    model_id, model_desc = pilih_model(default_model)
    persona_nama, sys_prompt = PERSONA_NAMA, PERSONA_PROMPT

    cwd = Path(".").resolve()
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
                print(f"\n{GREEN}✓ Konteks dihapus.{R}")

            elif cmd == "/history":
                show_history(all_history)

            elif cmd == "/persona":
                print(f"\n{DIM}Persona tunggal aktif: {CYAN}{PERSONA_NAMA}{R}\n")

            elif cmd == "/model":
                default_model = cfg.get("default_model", "")
                model_id, model_desc = pilih_model(default_model)
                messages = [system_msg]
                print(f"{DIM}Konteks direset.{R}")

            elif cmd == "/cwd":
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1:
                    new_cwd = Path(parts[1]).expanduser().resolve()
                    if new_cwd.is_dir():
                        os.chdir(new_cwd); cwd = new_cwd
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
                new_key = input(f"  Masukkan API key baru: ").strip()
                if new_key:
                    api_key = new_key
                    save_config({"api_key": new_key})
                    print(f"\n{GREEN}✓ API key diperbarui dan disimpan.{R}\n")
                else:
                    print(f"\n{YELLOW}Dibatalkan.{R}\n")

            elif cmd == "/info":
                print(f"\n  Model  : {CYAN}{model_desc}{R}  {DIM}({model_id}){R}")
                print(f"  Persona: {CYAN}{PERSONA_NAMA}{R}")
                print(f"  CWD    : {CYAN}{cwd}{R}\n")

            elif cmd == "/help":
                help_text()
            else:
                print(f"\n{RED}Tidak dikenal. Ketik /help.{R}")
            continue

        # ── Kirim ke API ──
        messages.append({"role": "user", "content": user_input})
        print(f"\n{DIM}Berpikir...{R}", end="\r", flush=True)

        try:
            reply = process_response(api_key, model_id, messages)
        except ValueError as e:
            print(f"\n{RED}❌ {e}{R}\n"); messages.pop(); continue
        except requests.exceptions.ConnectionError:
            print(f"\n{RED}❌ Tidak bisa terhubung. Cek internet.{R}\n"); messages.pop(); continue
        except Exception as e:
            print(f"\n{RED}❌ Error: {e}{R}\n"); messages.pop(); continue

        print(" " * 30, end="\r")
        if reply:
            print(f"\n{CYAN}{BOLD}AI ▸{R} {reply}\n")

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
        if len(chat_msgs) <= 2: all_history.append(entry)
        elif all_history:       all_history[-1] = entry
        save_history(all_history)

if __name__ == "__main__":
    main()