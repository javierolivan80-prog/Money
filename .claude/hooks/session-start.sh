#!/bin/bash
# SessionStart hook: make sure ffmpeg/ffprobe and yt-dlp are available so the
# /watch skill (.claude/skills/watch) works right away in a fresh container.
# Idempotent (skips work if already installed) and never fails the session —
# a missing network or package manager just leaves the skill to report it.

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

set +e

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    (apt-get install -y -qq ffmpeg >/dev/null 2>&1 \
      || (apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq ffmpeg >/dev/null 2>&1)) &
    ffmpeg_pid=$!
  fi
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
  if command -v pip >/dev/null 2>&1; then
    pip install -q yt-dlp >/dev/null 2>&1 &
    ytdlp_pid=$!
  elif command -v pip3 >/dev/null 2>&1; then
    pip3 install -q yt-dlp >/dev/null 2>&1 &
    ytdlp_pid=$!
  fi
fi

[ -n "${ffmpeg_pid:-}" ] && wait "$ffmpeg_pid"
[ -n "${ytdlp_pid:-}" ] && wait "$ytdlp_pid"

exit 0
