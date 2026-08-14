#!/usr/bin/env bash
set -euo pipefail

SDK_ROOT="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [[ -z "$SDK_ROOT" || ! -d "$SDK_ROOT" ]]; then
  echo "Android SDK root is unavailable" >&2
  exit 1
fi

# Bubblewrap 1.25 validates a top-level tools/ or bin/ directory, while
# GitHub-hosted runners expose sdkmanager under cmdline-tools/*/bin.
if [[ ! -d "$SDK_ROOT/bin" ]]; then
  CMDLINE_BIN="$(find "$SDK_ROOT/cmdline-tools" -mindepth 2 -maxdepth 2 -type d -name bin | head -n 1)"
  if [[ -z "$CMDLINE_BIN" ]]; then
    echo "Unable to locate Android cmdline-tools bin directory" >&2
    exit 1
  fi
  ln -s "$CMDLINE_BIN" "$SDK_ROOT/bin"
fi

mkdir -p "$HOME/.bubblewrap"
node -e "require('fs').writeFileSync(process.env.HOME + '/.bubblewrap/config.json', JSON.stringify({jdkPath: process.env.JAVA_HOME, androidSdkPath: process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT}, null, 2))"
cat "$HOME/.bubblewrap/config.json"
bubblewrap doctor
