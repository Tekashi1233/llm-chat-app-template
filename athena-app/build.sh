#!/bin/bash
# Builds the ATHENA native Windows app (.exe) for win-x64.
# No secrets are involved in this build: the app asks for the
# owner's password on first run (N5 M2) and stores the session
# token encrypted with Windows DPAPI.
# (Mirrors the Companion build; the .NET SDK is at ~/.dotnet and the
# NuGet source is local-only — see ~/TOOLS.md / Companion notes.)
set -e
set -o pipefail
cd "$(dirname "$0")/src/AthenaApp"

export DOTNET_CLI_TELEMETRY_OPTOUT=1
export PATH="$HOME/.dotnet:$PATH"
export TMPDIR="$HOME/tmp"

dotnet publish -c Release -r win-x64 --self-contained \
  -p:PublishSingleFile=true -o ../../out

echo "Built: $(pwd)/../../out/Athena.exe"
ls -la ../../out/Athena.exe
