# Voice Dictation Configuration
# Edit this file to customize your settings

# Hotkey to hold for recording (release to transcribe)
# Format: modifier+key (e.g., 'alt+f', 'ctrl+shift+d', 'f9')
# AVOID common combinations like: ctrl+c, ctrl+v, ctrl+s, alt+tab, alt+f4
HOTKEY = 'alt+f'

# Whisper model size: tiny, base, small, medium, large, large-v3-turbo
# Larger = more accurate but slower and uses more VRAM
# tiny (~1s), base (~2s), small (~3s), medium (~5s), large (~10s)
# large-v3-turbo: near-large accuracy with a pruned decoder - faster than
# medium at similar VRAM (~1.6GB); best quality-per-ms option on GPU
MODEL_SIZE = 'small'

# Beam size for Whisper decoding: 1 = greedy (fastest), 5 = beam search (default)
# 1 is usually indistinguishable for clean single-speaker dictation
BEAM_SIZE = 5

# Language for transcription
# 'en' = English, 'auto' = auto-detect, or specific code: 'es', 'fr', 'de', 'ja', etc.
# Full list: https://github.com/openai/whisper#available-models-and-languages
LANGUAGE = 'en'

# Device: 'cuda' for GPU, 'cpu' for CPU-only
DEVICE = 'cuda'

# Compute type: 'float16' for GPU, 'int8' or 'float32' for CPU
COMPUTE_TYPE = 'float16'

# Saved audio device identity.
# Leave all three as None to use the current system default microphone.
# AUDIO_DEVICE stores the friendly name shown in menus.
# AUDIO_DEVICE_HOSTAPI and AUDIO_DEVICE_INDEX are auto-managed to disambiguate
# duplicate names across MME / DirectSound / WASAPI.
# AUDIO_DEVICE_UID is a derived stable fingerprint used first during resolution.
AUDIO_DEVICE = None
AUDIO_DEVICE_HOSTAPI = None
AUDIO_DEVICE_INDEX = None
AUDIO_DEVICE_UID = None

# Noise reduction: True to filter background noise before transcription
# Helps with fans, AC, ambient noise - uses noisereduce library
NOISE_REDUCTION = False

# Noise gate threshold: minimum RMS level to process audio
# Skips transcription if audio is too quiet (e.g., accidental hotkey press)
# 0.0 = disabled (process everything), 0.01 = default, 0.05 = aggressive
NOISE_GATE_THRESHOLD = 0.01

# Copy transcribed text to clipboard in addition to typing it
# Disabled by default to reduce accidental sensitive-data persistence.
USE_CLIPBOARD = False

# Log transcript text snippets to dictation.log
# Keep False unless actively debugging recognition quality.
LOG_TRANSCRIPT_TEXT = False

# Hard cap on typed characters per utterance
# Prevents accidental multi-thousand-character injections if transcription goes wrong.
MAX_TYPED_CHARS = 1000

# Log level for runtime diagnostics: DEBUG, INFO, WARNING, ERROR
# INFO is recommended for normal usage and better performance.
LOG_LEVEL = 'INFO'

# Custom vocabulary: words/names the model should recognize correctly
# Comma-separated list of terms (names, technical words, acronyms)
# The model will be primed to recognize these spellings
# Example: 'Claude, Anthropic, TypeScript, GitHub, API, JIRA'
VOCABULARY = ''

# Wake word mode: hands-free dictation activated by a wake phrase.
# Requires openwakeword: pip install openwakeword
# When enabled, the tray icon turns blue while listening for the wake word.
# Say the wake word to start recording; silence ends the segment.
WAKE_WORD_ENABLED = False
WAKE_WORD_MODEL = 'hey_jarvis_v0.1'  # Pre-trained model name or path to custom .onnx
WAKE_WORD_THRESHOLD = 0.5            # Detection confidence (0.0-1.0)
WAKE_WORD_SILENCE_TIMEOUT_S = 2.0    # Seconds of silence to end a segment
WAKE_WORD_OUTPUT_FILE = None          # Path to transcription log file (None = no file output)
