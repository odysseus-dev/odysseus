# tests/test_wakeword_service.py
import numpy as np

from services.stt.wakeword import WakeWordDetector


class _FakeOwwModel:
    def __init__(self, scores):
        self._scores = list(scores)
        self.fed = []

    def predict(self, frame):
        self.fed.append(len(frame))
        s = self._scores.pop(0) if self._scores else 0.0
        return {"hey_soloway_v0.1": s}

    def reset(self):
        self.fed.clear()


def _detector(scores, threshold=0.7):
    d = WakeWordDetector.__new__(WakeWordDetector)  # skip __init__ (no real model)
    d._model = _FakeOwwModel(scores)
    d._threshold = threshold
    d._buffer = np.array([], dtype=np.int16)
    return d


def test_detects_above_threshold():
    d = _detector([0.1, 0.9])
    pcm = np.zeros(1280, dtype=np.int16).tobytes()
    assert d.feed(pcm) is False
    assert d.feed(pcm) is True


def test_below_threshold_never_fires():
    d = _detector([0.5, 0.69, 0.2])
    pcm = np.zeros(1280, dtype=np.int16).tobytes()
    assert d.feed(pcm) is False
    assert d.feed(pcm) is False
    assert d.feed(pcm) is False


def test_buffers_partial_frames():
    d = _detector([0.0, 0.9])
    half = np.zeros(640, dtype=np.int16).tobytes()
    assert d.feed(half) is False        # only 640 samples buffered — no predict yet
    assert d._model.fed == []
    assert d.feed(half) is False        # 1280 → one predict (score 0.0)
    assert d._model.fed == [1280]
