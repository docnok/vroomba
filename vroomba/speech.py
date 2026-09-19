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

VOICE_MAP: dict[str, str] = {
    "en": "en_US-danny-low",
    "es": "es_ES-carlfm-x_low",
}

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
}


def language_instruction() -> str:
    """Return an LLM prompt fragment for the configured output language."""
    from vroomba.config import settings

    name = LANGUAGE_NAMES.get(settings.output_language, settings.output_language)
    if settings.output_language == "en":
        return ""
    return f" Always respond in {name}."

# ---------------------------------------------------------------------------
# Lazy singletons — loaded on first use
# ---------------------------------------------------------------------------

_whisper_model = None
_piper_voice = None
_piper_voice_name = None


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
    global _piper_voice, _piper_voice_name
    from vroomba.config import settings

    voice_name = VOICE_MAP.get(settings.output_language, VOICE_MAP["en"])
    if _piper_voice is None or _piper_voice_name != voice_name:
        from piper import PiperVoice

        model_path = PIPER_DATA_DIR / f"{voice_name}.onnx"
        if not model_path.exists():
            _download_piper_voice(voice_name)
        log.info("Loading piper voice %s …", voice_name)
        _piper_voice = PiperVoice.load(str(model_path))
        _piper_voice_name = voice_name
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
