# Voice Dictation Configuration
# Edit this file to customize your settings

# Hotkey to hold for recording (release to transcribe)
# Format: modifier+key (e.g., 'alt+f', 'ctrl+shift+d', 'f9')
# AVOID common combinations like: ctrl+c, ctrl+v, ctrl+s, alt+tab, alt+f4
HOTKEY = 'alt+f'

# Whisper model size: tiny, base, small, medium, large
# Larger = more accurate but slower
MODEL_SIZE = 'small'

# Device: 'cuda' for GPU, 'cpu' for CPU-only
DEVICE = 'cuda'

# Compute type: 'float16' for GPU, 'int8' or 'float32' for CPU
COMPUTE_TYPE = 'float16'

# Audio device index: None = system default, or specify device number
AUDIO_DEVICE = None
