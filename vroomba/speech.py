"""Server-side STT (faster-whisper) and TTS (piper-tts) manager."""

import io
import logging
import tempfile
import wave
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PIPER_DATA_DIR = Path(__file__).parent.parent / ".piper-voices"

# ---------------------------------------------------------------------------
# Lazy singletons — loaded on first use
# ---------------------------------------------------------------------------

_whisper_model = None
_piper_voice = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        from vroomba.config import settings

        log.info("Loading faster-whisper model (%s, int8) …", settings.stt_model)
        _whisper_model = WhisperModel(settings.stt_model, device="cpu", compute_type="int8")
        log.info("faster-whisper ready")
    return _whisper_model


def _get_piper():
    global _piper_voice
    if _piper_voice is None:
        from piper import PiperVoice
        from vroomba.config import settings

        voice_name = settings.tts_voice
        model_path = PIPER_DATA_DIR / f"{voice_name}.onnx"
        if not model_path.exists():
            _download_piper_voice(voice_name)
        log.info("Loading piper voice %s …", voice_name)
        _piper_voice = PiperVoice.load(str(model_path))
        log.info("piper TTS ready")
    return _piper_voice


def _download_piper_voice(voice_name: str):
    """Download the piper voice model + config from HuggingFace."""
    import urllib.request

    PIPER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Voice name format: {lang}_{REGION}-{name}-{quality}
    # e.g. en_US-danny-low → en/en_US/danny/low/en_US-danny-low
    parts = voice_name.split("-")
    lang_region = parts[0]  # en_US
    lang = lang_region.split("_")[0]  # en
    name = parts[1]  # danny
    quality = parts[2] if len(parts) > 2 else "low"  # low
    base_url = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
        f"/{lang}/{lang_region}/{name}/{quality}/{voice_name}"
    )
    for suffix in (".onnx", ".onnx.json"):
        url = base_url + suffix
        dest = PIPER_DATA_DIR / (voice_name + suffix)
        if dest.exists():
            continue
        log.info("Downloading %s …", url)
        urllib.request.urlretrieve(url, dest)  # noqa: S310
        log.info("Saved %s", dest)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe raw audio bytes (WAV or WebM) → text string."""
    from vroomba.config import settings

    model = _get_whisper()

    # Write to a temp file — faster-whisper needs a file path or numpy array
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        lang = settings.stt_language or None  # empty string → auto-detect
        segments, info = model.transcribe(
            tmp.name,
            beam_size=5,
            language=lang,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()

    log.info("STT [%s prob=%.2f]: %s", info.language, info.language_probability, text)
    return text


def synthesize(text: str) -> bytes:
    """Synthesize text → WAV bytes."""
    from piper import SynthesisConfig
    from vroomba.config import settings

    voice = _get_piper()
    buf = io.BytesIO()
    syn_config = SynthesisConfig(length_scale=1.0 / settings.tts_speed) if settings.tts_speed != 1.0 else None
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file, syn_config=syn_config)
    wav_bytes = buf.getvalue()
    log.info("TTS: %d chars → %d bytes WAV", len(text), len(wav_bytes))
    return wav_bytes
