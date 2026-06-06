#!/usr/bin/env python3
"""
My AI CLI — Google AI Studio (Gemini API) + Tool Calling
Fitur: baca/tulis file, edit kode, web search, akses folder
"""

import os, json, datetime, subprocess, re, sys, shutil, platform
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    import requests
except ImportError:
    print("❌ Jalankan: pip install requests")
    sys.exit(1)

# Hubungkan ke Google GenAI SDK resmi jika terinstall
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ Jalankan: pip install google-genai")
    sys.exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────

HISTORY_FILE     = Path.home() / ".my_ai_history.json"
MAX_FILE_BYTES   = 100_000   # batas baca file ~100 KB
MAX_SEARCH_CHARS = 3_000     # batas karakter hasil search

MODELS = {
    "1": ("gemini-2.5-flash",        "Gemini 2.5 Flash (Sangat Cepat & Efisien)"),
    "2": ("gemini-2.5-pro",          "Gemini 2.5 Pro (Penalaran Tinggi / Coding)"),
}

PERSONA_NAMA   = "My AI (Gemini)"
PERSONA_PROMPT = (
    "Kamu adalah My AI, asisten pribadi berbasis Gemini yang serba bisa. Kemampuanmu meliputi:\n"
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

# ─── Tool Implementations ─────────────────────────────────────────────────────

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
    args = args or []
    system = platform.system()
    ALIASES = {
        "notepad": "notepad.exe", "explorer": "explorer.exe", "cmd": "cmd.exe",
        "chrome": "google-chrome" if system != "Windows" else "chrome",
        "firefox": "firefox", "code": "code", "vscode": "code"
    }
    resolved = ALIASES.get(target.lower(), target)

    if target.startswith("http://") or target.startswith("https://"):
        import webbrowser
        webbrowser.open(target)
        return f"✅ Membuka URL di browser: {target}"

    p = Path(target).expanduser()
    if p.exists():
        if system == "Windows": os.startfile(str(p))
        elif system == "Darwin": subprocess.Popen(["open", str(p)])
        else: subprocess.Popen(["xdg-open", str(p)])
        return f"✅ Membuka: {p}"

    if system != "Windows" and not shutil.which(resolved):
        return f"❌ Aplikasi tidak ditemukan di sistem: {resolved}"

    try:
        if system == "Windows":
            cmd = [resolved] + args if shutil.which(resolved) else ["cmd.exe", "/c", "start", "", resolved] + args
            subprocess.Popen(cmd, shell=True if not shutil.which(resolved) else False)
        elif system == "Darwin":
            subprocess.Popen(["open", "-a", resolved] + args)
        else:
            subprocess.Popen([resolved] + args)
        return f"✅ Membuka aplikasi: {resolved}"
    except Exception as e:
        return f"❌ Gagal membuka '{target}': {e}"

# Python dict map untuk fungsi lokal
TOOL_MAP = {
    "read_file": tool_read_file, "write_file": tool_write_file, "edit_file": tool_edit_file,
    "list_dir": tool_list_dir, "search_in_files": tool_search_in_files,
    "web_search": tool_web_search, "create_dir": tool_create_dir, "open_app": tool_open_app
}

# ─── Gemini API (Google GenAI SDK) ────────────────────────────────────────────

def dapatkan_gemini_tools():
    """Konversi fungsi Python menjadi format objek Deklarasi Tool Gemini."""
    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="read_file",
                description="Baca isi file teks untuk melihat kode atau dokumen.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={"path": types.Schema(type="STRING", description="Path file")},
                    required=["path"]
                )
            ),
            types.FunctionDeclaration(
                name="write_file",
                description="Tulis atau buat file baru / menimpa seluruh isi file.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "path": types.Schema(type="STRING", description="Path file"),
                        "content": types.Schema(type="STRING", description="Isi teks file")
                    },
                    required=["path", "content"]
                )
            ),
            types.FunctionDeclaration(
                name="edit_file",
                description="Edit string tertentu (old_str) menjadi string baru (new_str) di dalam file.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "path": types.Schema(type="STRING", description="Path file"),
                        "old_str": types.Schema(type="STRING", description="Teks lama yang unik"),
                        "new_str": types.Schema(type="STRING", description="Teks pengganti baru")
                    },
                    required=["path", "old_str", "new_str"]
                )
            ),
            types.FunctionDeclaration(
                name="list_dir",
                description="Tampilkan isi berkas dan folder di suatu direktori.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "path": types.Schema(type="STRING", description="Path direktori, default '.'"),
                        "recursive": types.Schema(type="BOOLEAN", description="Scan rekursif subfolder")
                    }
                )
            ),
            types.FunctionDeclaration(
                name="search_in_files",
                description="Cari potongan teks atau pattern di dalam berkas direktori.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "pattern": types.Schema(type="STRING", description="Kata/regex yang dicari"),
                        "directory": types.Schema(type="STRING", description="Direktori target, default '.'"),
                        "extension": types.Schema(type="STRING", description="Filter ekstensi ex: .py")
                    },
                    required=["pattern"]
                )
            ),
            types.FunctionDeclaration(
                name="web_search",
                description="Cari informasi terbaru atau berita terkini di internet.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={"query": types.Schema(type="STRING", description="Query pencarian")},
                    required=["query"]
                )
            ),
            types.FunctionDeclaration(
                name="create_dir",
                description="Buat direktori/folder baru.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={"path": types.Schema(type="STRING", description="Path folder baru")},
                    required=["path"]
                )
            ),
            types.FunctionDeclaration(
                name="open_app",
                description="Buka aplikasi sistem, folder lokal, atau tautan URL browser.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "target": types.Schema(type="STRING", description="Nama aplikasi, URL, atau path"),
                        "args": types.Schema(type="ARRAY", items=types.Schema(type="STRING"), description="Argumen opsional")
                    },
                    required=["target"]
                )
            ),
        ])
    ]

# ─── Config Configuration ─────────────────────────────────────────────────────

def load_config() -> dict:
    return {
        "api_key":       os.environ.get("GEMINI_API_KEY", ""),
        "default_model": os.environ.get("MY_AI_DEFAULT_MODEL", ""),
    }

def save_config(data: dict):
    env_file = Path(__file__).parent / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        for key, val in data.items():
            env_key = "GEMINI_API_KEY" if key == "api_key" else "MY_AI_DEFAULT_MODEL"
            found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{env_key}="):
                    lines[i] = f"{env_key}={val}"
                    found = True
                    break
            if not found: lines.append(f"{env_key}={val}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except: pass

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{GREEN}══════════════════════════════════════════════════════{R}")
    print(f"{BOLD}{GREEN}    🤖  My AI CLI  —  Official Gemini API Edition{R}")
    print(f"{BOLD}{GREEN}══════════════════════════════════════════════════════{R}\n")

    cfg = load_config()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or cfg.get("api_key", "")
    
    if not api_key:
        print(f"{YELLOW}API key Google AI Studio belum disimpan.{R}")
        print(f"{DIM}Dapatkan key gratis di: https://aistudio.google.com/{R}\n")
        api_key = input("Masukkan GEMINI_API_KEY: ").strip()
        if not api_key:
            print(f"{RED}❌ API key diperlukan.{R}"); return
        save_config({"api_key": api_key})
        print(f"{GREEN}✓ API key disimpan di file .env{R}\n")

    # Inisialisasi Client resmi Google GenAI
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"{RED}Gagal menginisialisasi client Gemini: {e}{R}"); return

    # Pemilihan Model
    default_model = cfg.get("default_model", "gemini-2.5-flash")
    print(f"{BOLD}Pilih model Gemini:{R}")
    for k, (mid, desc) in MODELS.items():
        marker = f"   {GREEN}← aktif{R}" if mid == default_model else ""
        print(f"   {CYAN}{k}{R}. {desc} {DIM}({mid}){R}{marker}")
    
    p = input(f"{DIM}Pilih [1-2, default=1]: {R}").strip()
    model_id = MODELS.get(p, MODELS["1"])[0] if p else default_model
    save_config({"default_model": model_id})

    cwd = Path(".").resolve()
    print(f"\n{DIM}Working directory: {cwd}{R}")
    print(f"{DIM}Ketik /exit untuk keluar, /help untuk bantuan.{R}\n")

    # Siapkan Config Objek untuk Tool Calling & System Instruction
    config_gemini = types.GenerateContentConfig(
        system_instruction=f"{PERSONA_PROMPT}\n\nWorking directory saat ini: {cwd}",
        tools=dapatkan_gemini_tools(),
        temperature=0.0
    )

    # Memulai Sesi Percakapan Multi-turn (Chat) bawaan SDK Gemini
    chat = client.chats.create(model=model_id, config=config_gemini)

    while True:
        try:
            user_input = input(f"\n{BOLD}Kamu ▸{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n{YELLOW}Sampai jumpa!{R}\n"); break

        if not user_input: continue

        if user_input.startswith("/"):
            cmd = user_input.lower().split()[0]
            if cmd == "/exit": break
            elif cmd == "/clear":
                chat = client.chats.create(model=model_id, config=config_gemini)
                print(f"\n{GREEN}✓ Sesi chat direset.{R}")
            elif cmd == "/apikey":
                new_key = input("Masukkan API key Google AI Studio baru: ").strip()
                if new_key:
                    save_config({"api_key": new_key})
                    print(f"{GREEN}✓ Kunci diperbarui. Silakan restart program.{R}")
            elif cmd == "/help":
                print("\nPerintah: /clear (reset chat), /apikey (ganti key), /exit (keluar)\n")
            else:
                print(f"{RED}Perintah tidak dikenal.{R}")
            continue

        print(f"{DIM}Gemini sedang berpikir & mengeksekusi...{R}", end="\r", flush=True)

        try:
            # Mengirim pesan ke Gemini
            response = chat.send_message(user_input)
            
            # loop penanganan fungsi/tool call jika Gemini meminta eksekusi fungsi lokal
            while response.function_calls:
                for function_call in response.function_calls:
                    name = function_call.name
                    args = function_call.args
                    
                    print(f"\n  {MAGENTA}🔧 {name}{R}({DIM}{', '.join(f'{k}={repr(v)}' for k,v in args.items())}{R})")
                    
                    # Eksekusi fungsi lokal berdasarkan permintaan Gemini
                    if name in TOOL_MAP:
                        try:
                            result = TOOL_MAP[name](**args)
                        except Exception as e:
                            result = f"❌ Kegagalan eksekusi internal: {e}"
                    else:
                        result = f"❌ Tool '{name}' tidak ditemukan."
                    
                    preview = str(result)[:150].replace("\n", " ")
                    print(f"  {DIM}→ {preview}...{R}\n")
                    
                    # Kirim balik hasil eksekusi tool ke Gemini agar dia bisa merangkum jawabannya
                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=name,
                            response={"result": str(result)}
                        )
                    )
            
            # Bersihkan baris penanda proses dan cetak teks final dari Gemini
            print(" " * 45, end="\r")
            if response.text:
                print(f"\n{CYAN}{BOLD}AI ▸{R} {response.text}\n")

        except Exception as e:
            print(f"\n{RED}❌ Terjadi kesalahan API: {e}{R}\n")

if __name__ == "__main__":
    main()