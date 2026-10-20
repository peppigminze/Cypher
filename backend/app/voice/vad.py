"""
Streaming voice-activity detection with Silero VAD v6 (MIT).

The ONNX model ships inside faster-whisper, so this adds no dependency.
faster-whisper only uses it offline on whole files; here it runs frame by
frame (32 ms) with its recurrent state (h, c) carried between calls, which
is what live endpointing needs. Cost: well under 1 ms per frame on one
CPU thread.
"""
from __future__ import annotations

import os
import threading

import numpy as np

FRAME_SAMPLES = 512  # 32 ms at 16 kHz - the size the model was trained on
CONTEXT_SAMPLES = 64

_session_lock = threading.Lock()
_session = None


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            import faster_whisper  # noqa: PLC0415
            import onnxruntime  # noqa: PLC0415

            path = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
            opts = onnxruntime.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 4
            _session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
        return _session


class SileroVAD:
    """One instance per audio stream (it is stateful)."""

    frame_samples = FRAME_SAMPLES

    def __init__(self) -> None:
        self._session = _get_session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def speech_probability(self, frame: np.ndarray) -> float:
        if frame.shape[0] != FRAME_SAMPLES:
            raise ValueError(f"VAD frame must be {FRAME_SAMPLES} samples, got {frame.shape[0]}")
        frame = frame.astype(np.float32, copy=False)
        batch = np.concatenate([self._context, frame])[None, :]
        prob, self._h, self._c = self._session.run(None, {"input": batch, "h": self._h, "c": self._c})
        self._context = frame[-CONTEXT_SAMPLES:].copy()
        return float(np.asarray(prob).reshape(-1)[0])
