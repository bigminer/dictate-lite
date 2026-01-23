@echo off
echo %date% %time% Launching >> "C:\Users\gary.miner\voice-dictation\hook.log"
start "Voice Dictation" cmd /k "cd /d C:\Users\gary.miner\voice-dictation && python dictate.py"
