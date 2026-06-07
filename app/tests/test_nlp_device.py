from __future__ import annotations

from app.services.nlp_device import MIN_GPU_BATCH, gpu_batch_enabled, nlp_device_setting


def test_gpu_batch_disabled_for_small_batches(monkeypatch):
    monkeypatch.setenv("NLP_DEVICE", "auto")
    monkeypatch.setattr("app.services.nlp_device.cuda_available", lambda: True)
    assert gpu_batch_enabled(MIN_GPU_BATCH - 1) is False


def test_gpu_batch_enabled_for_large_batches(monkeypatch):
    monkeypatch.setenv("NLP_DEVICE", "auto")
    monkeypatch.setattr("app.services.nlp_device.cuda_available", lambda: True)
    assert gpu_batch_enabled(MIN_GPU_BATCH) is True
    assert gpu_batch_enabled(50) is True


def test_gpu_batch_honors_cpu_override(monkeypatch):
    monkeypatch.setenv("NLP_DEVICE", "cpu")
    monkeypatch.setattr("app.services.nlp_device.cuda_available", lambda: True)
    assert gpu_batch_enabled(50) is False


def test_nlp_device_setting_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("NLP_DEVICE", raising=False)
    assert nlp_device_setting() == "auto"
