"""
Claude Code Status Line with TTS Context Alerts

Wraps ccstatusline and monitors context percentage.
When context drops below threshold, speaks a warning using Edge TTS.

Usage: Configure in ~/.claude/settings.json:
  "statusLine": {
    "type": "command",
    "command": "python C:\\Users\\gary.miner\\voice-dictation\\src\\claude_status_tts.py",
    "padding": 0
  }
"""

import sys
import json
import subprocess
import time
import os

# Configuration
WARN_THRESHOLD = 20  # Percentage at which to warn
CRITICAL_THRESHOLD = 10  # Percentage for critical warning
COOLDOWN_SECONDS = 120  # Don't repeat warning within this time

SPEAK_SCRIPT = os.path.join(os.path.dirname(__file__), "speak.py")

# State tracking
last_warn_time = 0
last_critical_time = 0

def speak_async(message):
    """Fire and forget TTS - don't block status line updates."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE

    subprocess.Popen(
        [sys.executable, SPEAK_SCRIPT, message],
        startupinfo=startupinfo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def check_context_alert(remaining_pct):
    """Check if we should speak a context warning."""
    global last_warn_time, last_critical_time

    current_time = time.time()

    if remaining_pct is None:
        return

    # Critical warning (10%)
    if remaining_pct <= CRITICAL_THRESHOLD:
        if current_time - last_critical_time > COOLDOWN_SECONDS:
            speak_async(f"Critical: Only {remaining_pct} percent context remaining. Consider compacting soon.")
            last_critical_time = current_time
            last_warn_time = current_time  # Also reset warn to avoid double-speak
        return

    # Standard warning (20%)
    if remaining_pct <= WARN_THRESHOLD:
        if current_time - last_warn_time > COOLDOWN_SECONDS:
            speak_async(f"Context alert: {remaining_pct} percent remaining.")
            last_warn_time = current_time

def main():
    """Read status JSON from stdin, check for context alerts, pass through to ccstatusline."""
    # Start ccstatusline as subprocess to handle actual display
    ccstatusline = subprocess.Popen(
        ["npx", "-y", "ccstatusline@latest"],
        stdin=subprocess.PIPE,
        shell=True
    )

    try:
        for line in sys.stdin:
            # Pass through to ccstatusline for display
            if ccstatusline.stdin:
                ccstatusline.stdin.write(line.encode())
                ccstatusline.stdin.flush()

            # Parse and check for context alerts
            try:
                data = json.loads(line.strip())
                remaining_pct = data.get("remaining_percentage")
                check_context_alert(remaining_pct)
            except (json.JSONDecodeError, KeyError):
                pass  # Not all lines are JSON

    except KeyboardInterrupt:
        pass
    finally:
        if ccstatusline.stdin:
            ccstatusline.stdin.close()
        ccstatusline.wait()

if __name__ == "__main__":
    main()
