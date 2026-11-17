# ATHENA — Native Windows App (minimal black identity + N4 Voice)

A genuine, Chrome-free native Windows desktop app (C# + WPF, .NET 8)
that chats with the existing ATHENA backend — the Cloudflare Worker
that used to be VESPER. N1 built the minimal shell; N2 expanded it
into a Command Centre; N3 added a floating Dynamic Island; **N4
added native voice**: female British speech output (SAPI, Hazel
preferred) and microphone input. **The redesign replaces the
Command Centre and the Island with a minimal black identity**:
a pure black screen, ATHENA as an **arc reactor** in the centre
— steel rings, a segmented coil ring, a glowing blue-white core —
with subtle red + blue light behind it, a discreet status line,
and a bottom message input. The reactor lights up bright the
moment ATHENA replies, pulses gently while she thinks, and rests
dim when idle. The chat itself is transparent — message bubbles
and the input bar have no filled background, so replies sit
straight on the black with only a thin border. The six capability
sections (Conversations, Memory Vault, Tools, Devices, Activity,
Settings) are reachable from the "···" menu. The reactor stays
visible during conversations, smaller, at the top — click it to
return to the idle screen. Companion integration and owner auth
(N5) come later.

## What N1 is (and is not)

- **Is:** a native WPF window that sends messages to `POST /api/chat`
  and shows the replies. Same backend, same model (GPT-OSS-20B), same
  D1 memory, same 13 tools, same conversation history as the website.
- **Is not:** a new backend, a new memory system, a rewrite of the
  Windows Companion, or a replacement for the website. The website
  and the Companion keep working untouched.

## Authentication — read this first

The backend uses a shared site key (`X-Vesper-Key`). **N1 does not
embed that key in source code or in the executable.** Instead:

1. On first run the app asks you to paste the site key (the
   `VESPER_KEY` value in `vesper-cloud.html`).
2. The key is validated with a request that costs no model call and
   creates no rows: an empty chat request returns HTTP 400 when the
   key is good and 401 when it is wrong (P2-M1 rev7).
3. The key is then stored encrypted with Windows DPAPI (current-user
   scope) in `%APPDATA%\Athena\config.json`, next to a random
   conversation id. DPAPI means only your Windows user account can
   decrypt it, and only on this machine.

**Limitation, stated plainly:** the key is still the shared site
key — the same one the website embeds in its page. N1 stores it
better (encrypted at rest instead of in a page), but it does not
replace it. Proper per-user authentication (login → short-lived
tokens) is N5. If the key ever leaks, rotate it in D1 (`settings`
table, key `api_key`) and re-enter it in the app.

## Layout

```
athena-app/
  README.md            — this file
  build.sh             — publishes out/Athena.exe (win-x64, self-contained)
  src/
    Athena.Core/       — no Windows dependency: API contract, SSE parser,
                         message validation, chat client, DPAPI store
    AthenaApp/         — WPF app: App, MainWindow (chat), SetupWindow
                         (first-run key entry), icon
  tests/
    Athena.Tests/      — console test harness (no test framework needed)
```

## Build (from this Linux VM — cross-build, like the Companion)

```bash
bash athena-app/build.sh
# → athena-app/out/Athena.exe  (~70 MB, self-contained, no runtime to install)
```

Requirements: .NET SDK 8.0.428 at `~/.dotnet`, NuGet local-only
source `~/nuget-local/` (the WindowsDesktop ref pack covers WPF).
WPF + trimming is unsupported, so the build is untrimmed — same as
the Companion.

## Run on Windows

1. Copy `Athena.exe` to the laptop and run it (SmartScreen will flag
   the unsigned exe: "More info" → "Run anyway" — same as the
   Companion until a signing certificate is decided in N6).
2. Paste the site key when asked, click "Validate & save".
3. Chat. Enter sends. The status line under the wordmark shows
   Connected / Offline / key problems, and what ATHENA is doing
   (Listening, Thinking, Speaking). Close the window to quit;
   nothing runs in the background (no tray icon yet — that comes
   with N6).

## Tests

```bash
# Unit + mocked-HTTP tests (run anywhere):
dotnet run --project athena-app/tests/Athena.Tests

# Live backend tests (need the site key as an env var; uses a
# throwaway conversation id, makes normal application requests):
ATHENA_TEST_KEY=<key> dotnet run --project athena-app/tests/Athena.Tests
```

DPAPI round-trip tests run on Windows only and are skipped on Linux
with a note. The WPF window itself can only be launched on Windows —
the UI launch test belongs on the real laptop.

## Known limitations (N4)

- Voice depends on what Windows has installed on the laptop:
  the voice list is enumerated at runtime and the default is
  picked by policy (saved voice → Hazel → female en-GB → any
  en-GB → any female → first installed). Hazel may or may not
  be present; Sonia Online needs network. If no voices exist,
  voice output is honestly unavailable, not faked.
- Speech recognition needs a recognizer (en-GB preferred,
  en-US fallback) and a working microphone. If either is
  missing, the Mic button is disabled with an explanation.
  Recognised text always goes into the message box for the
  user to check — nothing is ever auto-sent, and the mic is
  never on unless tapped. No wake-word, no background
  listening.
- The reactor lighting shows only states real events produce;
  N4 wires Listening/Speaking to real voice events, and a live
  reply flashes the reactor bright. Working/Approval
  still need Companion device activity (N5). All glow changes are
  slow fades (0.8–1.6s) — no flashing.
- The wordmark and the chat share one AppState, so the menu sections
  show the same conversation and backend — no second client, no
  second memory.
- No tray icon, no startup-on-boot, no notifications (N6). No
  device commands from this app yet — the Companion still owns
  those (N5).
- The exe is unsigned (SmartScreen warning, see above).
- Conversation history is server-side per conversation id; the app
  keeps the session's messages in memory only.
- If the laptop is offline, the app shows Offline and sending fails
  with a plain error — it does not queue messages (N6).

## What remains for N5

Companion integration + owner authentication: device status,
approvals and activity inside the menu sections, DPAPI
protection for device credentials, and per-user login to
replace the shared site key. Family access waits until after
that.
