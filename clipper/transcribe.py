"""Local speech-to-text with word timestamps (audio never leaves this machine)."""
import subprocess

import numpy as np
from faster_whisper import WhisperModel

_models: dict = {}


def _model(size: str) -> WhisperModel:
    # Loading takes seconds; live jobs transcribe every few minutes, so keep it.
    if size not in _models:
        _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _models[size]


def _load_audio(path: str, start: float | None = None, duration: float | None = None) -> np.ndarray:
    command = ["ffmpeg", "-v", "error"]
    if start:
        command += ["-ss", f"{start:.2f}"]
    command += ["-i", path]
    if duration:
        command += ["-t", f"{duration:.2f}"]
    command += ["-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(path: str, model_size: str = "small", language: str | None = None,
               start: float | None = None, duration: float | None = None) -> dict:
    """Transcribe a file (or a time range of it). Returned times are positions in the file."""
    offset = start or 0.0
    # model_size may also be a local folder, for networks that block model downloads.
    segments, info = _model(model_size).transcribe(
        _load_audio(path, start, duration), word_timestamps=True, vad_filter=True, language=language
    )

    result_segments, words = [], []
    for segment in segments:
        result_segments.append({
            "start": round(segment.start + offset, 2),
            "end": round(segment.end + offset, 2),
            "text": segment.text.strip(),
        })
        for word in segment.words or []:
            words.append({
                "start": round(word.start + offset, 2),
                "end": round(word.end + offset, 2),
                "word": word.word.strip(),
            })

    return {"language": info.language, "segments": result_segments, "words": words}
