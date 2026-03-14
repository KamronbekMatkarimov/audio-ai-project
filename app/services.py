import os
from typing import List, Dict, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

_local_whisper_model = None


# Load environment variables from a local .env file (if present)
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _use_local_whisper() -> bool:
    """
    Decide whether to use local faster-whisper based on env config.
    New style: TRANSCRIPTION_PROVIDER=local_whisper / openai_whisper.
    Fallback: USE_LOCAL_WHISPER=1/0.
    """
    provider = os.getenv("TRANSCRIPTION_PROVIDER", "").strip().lower()
    if provider == "local_whisper":
        return True
    if provider == "openai_whisper":
        return False
    # Backwards-compatible fallback
    return _bool_env("USE_LOCAL_WHISPER", True)


def _get_local_whisper_model():
    global _local_whisper_model
    if _local_whisper_model is not None:
        return _local_whisper_model

    from faster_whisper import WhisperModel

    # Prefer new env name WHISPER_MODEL_SIZE, fallback to old WHISPER_MODEL
    model_name = (
        os.getenv("WHISPER_MODEL_SIZE")
        or os.getenv("WHISPER_MODEL")
        or "medium"
    ).strip()
    device = os.getenv("WHISPER_DEVICE", "cpu").strip()
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip()
    _local_whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _local_whisper_model


async def transcribe_uzbek_audio(filepath: str) -> str:
    """
    Use OpenAI Whisper model, which is trained on a massive multilingual
    dataset including Uzbek speech. This gives state-of-the-art quality.
    """
    if not os.path.exists(filepath):
        raise RuntimeError(f"Audio file not found on disk: {filepath}")
    if os.path.getsize(filepath) <= 0:
        raise RuntimeError("Audio file is empty (0 bytes)")

    language = os.getenv("TRANSCRIPTION_LANGUAGE", "uz").strip() or "uz"

    # Preferred: local whisper (free)
    if _use_local_whisper():
        model = _get_local_whisper_model()

        beam_size = _int_env("WHISPER_BEAM_SIZE", 5)
        best_of = _int_env("WHISPER_BEST_OF", 5)
        temperature = _float_env("WHISPER_TEMPERATURE", 0.0)
        vad_filter = _bool_env("WHISPER_VAD_FILTER", True)

        segments, info = model.transcribe(
            filepath,
            language=language,
            beam_size=beam_size,
            best_of=best_of,
            temperature=temperature,
            vad_filter=vad_filter,
        )
        text_parts = [seg.text for seg in segments]
        return "".join(text_parts).strip()

    # Fallback: OpenAI Whisper API
    if client is None:
        raise RuntimeError(
            "OPENAI_API_KEY is not set and local Whisper is disabled. Cannot transcribe."
        )
    with open(filepath, "rb") as f:
        result = await client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language=language,
        )
    return result.text


async def categorize_text(text: str, labels: List[str]) -> Dict[str, float]:
    """
    Use an OpenAI chat model for zero-shot classification of Uzbek text into
    user-defined categories. Returns probabilities for each label.
    """
    if client is None or not labels:
        return {}

    system_prompt = (
        "You are a classifier for Uzbek text. "
        "Given a text and a list of category labels (in any language), "
        "return probabilities for how well the text fits each label. "
        "Always respond with a JSON object mapping label -> probability between 0 and 1, "
        "and probabilities should sum approximately to 1."
    )

    user_prompt = (
        f"Text (Uzbek): {text}\n\n"
        f"Labels: {labels}\n\n"
        "Return only valid JSON like {\"label\": 0.5, ...}."
    )

    chat = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
    )
    content = chat.choices[0].message.content or "{}"
    try:
        import json

        data = json.loads(content)
        # Ensure only provided labels and floats
        probs = {
            str(label): float(data.get(label, 0.0)) for label in labels
        }
        total = sum(probs.values()) or 1.0
        return {k: v / total for k, v in probs.items()}
    except Exception:
        return {}

