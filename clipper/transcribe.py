"""Local speech-to-text with word timestamps (audio never leaves this machine)."""
import subprocess

import numpy as np
from faster_whisper import WhisperModel


def _load_audio(path: str) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(path: str, model_size: str = "small", language: str | None = None) -> dict:
    # model_size may also be a local folder, for networks that block model downloads.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        _load_audio(path), word_timestamps=True, vad_filter=True, language=language
    )

    result_segments, words = [], []
    for segment in segments:
        result_segments.append({
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text.strip(),
        })
        for word in segment.words or []:
            words.append({
                "start": round(word.start, 2),
                "end": round(word.end, 2),
                "word": word.word.strip(),
            })

    return {"language": info.language, "segments": result_segments, "words": words}
