"""
Edge TTS Speaker - Natural sounding text-to-speech
Usage: python speak.py "Your message here"
"""

import sys
import asyncio
import tempfile
import os
import subprocess
import time

async def speak(text, voice='en-US-BrianNeural'):
    """Speak text using Edge TTS neural voice."""
    import edge_tts

    temp_file = os.path.join(tempfile.gettempdir(), 'claude_speak.mp3')

    # Generate audio
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(temp_file)

    # Play using PowerShell and .NET without visible window
    # Escape single quotes in path by doubling them (PowerShell convention)
    safe_path = temp_file.replace("'", "''")
    ps_script = (
        "Add-Type -AssemblyName PresentationCore\n"
        "$player = New-Object System.Windows.Media.MediaPlayer\n"
        "$player.Volume = 1\n"
        "$player.Open([Uri]'" + safe_path + "')\n"
        "Start-Sleep -Milliseconds 500\n"
        "$player.Play()\n"
        "Start-Sleep -Milliseconds 500\n"
        "while ($player.Position.TotalSeconds -lt "
        "$player.NaturalDuration.TimeSpan.TotalSeconds - 0.1) {\n"
        "    Start-Sleep -Milliseconds 100\n"
        "}\n"
        "Start-Sleep -Milliseconds 300\n"
        "$player.Close()\n"
    )

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE

    subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
        capture_output=True,
        startupinfo=startupinfo
    )

def main():
    if len(sys.argv) < 2:
        print("Usage: python speak.py \"message\"")
        sys.exit(1)

    text = sys.argv[1]
    asyncio.run(speak(text))

if __name__ == '__main__':
    main()
