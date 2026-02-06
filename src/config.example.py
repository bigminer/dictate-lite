# Voice Dictation Configuration
# Edit this file to customize your settings

# Hotkey to hold for recording (release to transcribe)
# Format: modifier+key (e.g., 'alt+f', 'ctrl+shift+d', 'f9')
# AVOID common combinations like: ctrl+c, ctrl+v, ctrl+s, alt+tab, alt+f4
HOTKEY = 'alt+f'

# Whisper model size: tiny, base, small, medium, large
# Larger = more accurate but slower and uses more VRAM
# tiny (~1s), base (~2s), small (~3s), medium (~5s), large (~10s)
MODEL_SIZE = 'small'

# Language for transcription
# 'en' = English, 'auto' = auto-detect, or specific code: 'es', 'fr', 'de', 'ja', etc.
# Full list: https://github.com/openai/whisper#available-models-and-languages
LANGUAGE = 'en'

# Device: 'cuda' for GPU, 'cpu' for CPU-only
DEVICE = 'cuda'

# Compute type: 'float16' for GPU, 'int8' or 'float32' for CPU
COMPUTE_TYPE = 'float16'

# Audio device index: None = system default, or specify device number
AUDIO_DEVICE = None

# Noise reduction: True to filter background noise before transcription
# Helps with fans, AC, ambient noise - uses noisereduce library
NOISE_REDUCTION = False

# Noise gate threshold: minimum RMS level to process audio
# Skips transcription if audio is too quiet (e.g., accidental hotkey press)
# 0.0 = disabled (process everything), 0.01 = default, 0.05 = aggressive
NOISE_GATE_THRESHOLD = 0.01

# Copy transcribed text to clipboard in addition to typing it
# Useful as backup if text injection fails in some applications
USE_CLIPBOARD = True

# Custom vocabulary: words/names the model should recognize correctly
# Comma-separated list of terms (names, technical words, acronyms)
# The model will be primed to recognize these spellings
# Example: 'Claude, Anthropic, TypeScript, GitHub, API, JIRA'
VOCABULARY = ''
