# docker/laya/Dockerfile — laya decision-engine sidecar.
#
# Runs `laya-serve` (https://github.com/NandhaKishorM/laya, Apache-2.0) in its own
# container so the Odysseus image stays torch-free. laya[serve] pulls torch +
# transformers (CPU wheels by default); this isolation is the whole point of the
# sidecar. Checkpoints download from Hugging Face on first boot into a mounted
# cache volume (see compose.laya.yaml).
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    LAYA_HOST=0.0.0.0 \
    LAYA_PORT=8000

# Pin the laya version for reproducibility. Bump deliberately.
RUN pip install "laya[serve]==0.3.21"

EXPOSE 8000

# Honors LAYA_* env (LAYA_API_KEY, LAYA_PRELOAD, LAYA_MODELS, LAYA_DEVICE,
# LAYA_MAX_CONCURRENT, LAYA_REVISION, ...) passed by compose.
CMD ["laya-serve"]
