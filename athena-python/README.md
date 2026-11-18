# ATHENA Python v1

The ATHENA desktop assistant, rebuilt in Python — same black /
arc-reactor / glass identity as the C# app, with the brain from the
Friday / MemOS repos' design: a slotted LLM provider (Cloudflare
Worker first, local Ollama fallback), a tiered router, a tool
registry with category dispatch, and a local lessons store that
learns from ATHENA's own mistakes.

## Layout (mirrors the Friday / MemOS package shape)

```
src/athena/
  agents/      AgentLoop — the turn loop (tools, reflection, cap)
  awareness/   live context (date/time/status, no network)
  background/  scheduler, reminders, decay watcher (stdlib threads)
  config/      paths, wake-word/listening policies, DPAPI key store
  execution/   multi-step plan engine (sequential/parallel)
  interfaces/  terminal/ (CLI chat for testing), desktop/ (PySide6 UI)
  llm/         Worker client, Ollama client, slotted provider, modes
  memory/      local lessons: decay, promotion, compaction, dreaming,
               calendar conflicts, transcript repair
  reasoning/   chain-of-thought helpers, reflection/self-critique
  router/      smart router (SIMPLE/MEDIUM/COMPLEX/CLARIFY tiers)
               + category dispatch (10 categories, never 60 raw tools)
  search/      hybrid search: FTS5 BM25 + hashed vectors + MMR
  security/    credential redaction for local logs
  tools/       registry (auto-discovery), local tools, screen OCR
  utils/       logging
  voice/       Edge TTS (en-GB female) + SAPI Hazel fallback,
               Vosk dictation + wake-word "athena"
```

Layering: `interfaces → router → agents → tools/memory/reasoning`,
nothing imports upward — enforced by `tests/test_layout.py`.
The brain never knows which interface is active.

## What stays ATHENA (not copied blindly)

- Brain is the existing Cloudflare Worker (Slot 1) + Ollama (Slot 2).
  The repos use Ollama/Gemini directly; we keep the Worker + D1.
- Voice stays female British (Edge TTS `en-GB-SoniaNeural`, SAPI
  Hazel offline fallback). Jarvis's voice was male Canadian — not copied.
- No paid services anywhere. No Telegram. Wake-word stays local
  Vosk (theirs is cloud speech recognition). D1 stays the memory of
  record; the local store holds lessons only.

## External services (all $0)

| Service | Use | Cost |
|---|---|---|
| Cloudflare Worker + Workers AI (free tier) | chat brain, tools, D1 memory | $0 |
| Edge TTS (speech.platform.bing.com, no key) | neural female British voice | $0 |
| Ollama (localhost:11434, optional) | local fallback brain | $0 |
| Open-Meteo (no key) | weather tool | $0 |
| Wikipedia REST (no key) | web-search tool | $0 |
| Vosk model download (alphacephei.com, ~40 MB) | dictation + wake-word model | $0 |

## Run (dev, on the laptop)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pywin32 pyautogui
python main.py            :: desktop UI
python main.py --tray     :: start hidden in the tray
python main.py --terminal :: CLI chat (testing, no UI/audio)
```

First run needs the Vosk model (auto-downloaded on first use to
`%APPDATA%\Athena\models`) and Tesseract installed for on-demand
screen OCR (free UB-Mannheim build). The site key is read from the
C# app's `%APPDATA%\Athena\config.json` — no re-entry, no
first-run key screen in v1.

## Build the exe (on Windows — PyInstaller cannot cross-build)

```bat
build.bat
:: produces dist\Athena.exe (single file, windowed)
```

A Linux PyInstaller run only proves the bundling works; it makes a
Linux binary, not a Windows exe.

## Tests

```bat
.venv\Scripts\python -m pytest tests -q
```

Real-Windows checks live in
`~/workspace/vesper-agent/ATHENA-python-release-checklist.md`.
