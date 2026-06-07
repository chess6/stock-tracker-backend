from __future__ import annotations

import logging
import os

logger = logging.getLogger("stock_tracker.pipeline.nlp_device")

# Below this batch size, GPU kernel overhead usually erases any speedup.
MIN_GPU_BATCH = 8


def nlp_device_setting() -> str:
    return os.getenv("NLP_DEVICE", "auto").strip().lower()


def cuda_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def gpu_batch_enabled(batch_size: int) -> bool:
    """Use GPU batched inference only when it is likely faster than CPU."""
    setting = nlp_device_setting()
    if setting == "cpu":
        return False
    if setting == "cuda" and not cuda_available():
        logger.warning("NLP_DEVICE=cuda but no CUDA device found; using CPU")
        return False
    if not cuda_available():
        return False
    if batch_size < MIN_GPU_BATCH:
        return False
    return True


def torch_device_for_batch(batch_size: int) -> str:
    return "cuda" if gpu_batch_enabled(batch_size) else "cpu"
