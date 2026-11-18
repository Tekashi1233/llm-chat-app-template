#!/bin/bash
# Builds the ATHENA native Windows app (.exe) for win-x64.
# No secrets are involved in this build: the app asks for the
# owner's site key on first run (no password — owner decision
# 2026-11-18) and stores it encrypted with Windows DPAPI.
# (Mirrors the Companion build; the .NET SDK is at ~/.dotnet and the
# NuGet source is local-only — see ~/TOOLS.md / Companion notes.)
set -e
set -o pipefail
cd "$(dirname "$0")/src/AthenaApp"

export DOTNET_CLI_TELEMETRY_OPTOUT=1
export PATH="$HOME/.dotnet:$PATH"
export TMPDIR="$HOME/tmp"

# ReadyToRun (N7 speed): pre-compiles the app's IL to native code at
# publish time, so a cold start skips most JIT compilation and the
# window appears sooner. Trade-off: a larger .exe — how much larger
# is measured by the coordinator's final build against the N6 build's
# 72,010,332 bytes; no size number is claimed here.
# Deliberately NOT touched: no trimming (unsupported for WPF) and no
# InvariantGlobalization (WPF crashes on launch with it — see csproj).
dotnet publish -c Release -r win-x64 --self-contained \
  -p:PublishSingleFile=true -p:PublishReadyToRun=true -o ../../out

echo "Built: $(pwd)/../../out/Athena.exe"
ls -la ../../out/Athena.exe
