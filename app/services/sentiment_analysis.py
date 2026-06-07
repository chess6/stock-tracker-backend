from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("stock_tracker.pipeline.sentiment")

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore
except ImportError:  # pragma: no cover
    SentimentIntensityAnalyzer = None

_vader_analyzer = None
_finbert_pipeline = None
_finbert_on_device: str | None = None


@dataclass
class SentimentResult:
    label: str | None
    score: float | None
    vader_compound: float | None = None
    vader_pos: float | None = None
    vader_neu: float | None = None
    vader_neg: float | None = None
    finbert_label: str | None = None
    finbert_pos: float | None = None
    finbert_neu: float | None = None
    finbert_neg: float | None = None


def _get_vader():
    global _vader_analyzer
    if SentimentIntensityAnalyzer is None:
        return None
    if _vader_analyzer is None:
        _vader_analyzer = SentimentIntensityAnalyzer()
    return _vader_analyzer


def _parse_finbert_output(outputs: list[dict]) -> dict[str, float | str]:
    by_label = {item["label"].lower(): float(item["score"]) for item in outputs}
    label = max(by_label, key=by_label.get)
    return {
        "label": label,
        "pos": by_label.get("positive", 0.0),
        "neu": by_label.get("neutral", 0.0),
        "neg": by_label.get("negative", 0.0),
    }


def _get_finbert(*, use_gpu: bool = False):
    global _finbert_pipeline, _finbert_on_device
    from .nlp_device import cuda_available

    want_device = "cuda" if use_gpu and cuda_available() else "cpu"
    if _finbert_pipeline is not None and _finbert_on_device == want_device:
        return _finbert_pipeline
    try:
        from transformers import pipeline  # type: ignore
    except ImportError:
        return None
    try:
        device = 0 if want_device == "cuda" else -1
        _finbert_pipeline = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            top_k=None,
            truncation=True,
            max_length=512,
            device=device,
        )
        _finbert_on_device = want_device
        logger.info("FinBERT pipeline loaded on %s", want_device)
        return _finbert_pipeline
    except Exception as exc:
        logger.warning("FinBERT unavailable: %s", exc)
        return None


def analyze_vader(text: str) -> dict[str, float] | None:
    analyzer = _get_vader()
    if analyzer is None or not text.strip():
        return None
    scores = analyzer.polarity_scores(text)
    return {
        "compound": float(scores["compound"]),
        "pos": float(scores["pos"]),
        "neu": float(scores["neu"]),
        "neg": float(scores["neg"]),
    }


def analyze_finbert(text: str) -> dict[str, float | str] | None:
    pipe = _get_finbert(use_gpu=False)
    if pipe is None or not text.strip():
        return None
    try:
        return _parse_finbert_output(pipe(text[:4000])[0])
    except Exception as exc:
        logger.debug("FinBERT inference failed: %s", exc)
        return None


def analyze_finbert_batch(
    texts: list[str],
    *,
    use_gpu: bool = False,
    batch_size: int = 16,
) -> list[dict[str, float | str] | None]:
    pipe = _get_finbert(use_gpu=use_gpu)
    if pipe is None:
        return [None for _ in texts]
    snippets = [text[:4000] if text and text.strip() else "" for text in texts]
    try:
        outputs = pipe(snippets, batch_size=batch_size)
        parsed: list[dict[str, float | str] | None] = []
        for snippet, result in zip(snippets, outputs):
            if not snippet:
                parsed.append(None)
            else:
                parsed.append(_parse_finbert_output(result))
        return parsed
    except Exception as exc:
        logger.debug("FinBERT batch inference failed: %s", exc)
        return [analyze_finbert(text) for text in texts]


def _label_from_compound(compound: float) -> tuple[str, float]:
    if compound >= 0.05:
        return "positive", compound
    if compound <= -0.05:
        return "negative", compound
    return "neutral", compound


def build_sentiment_result(
    title: str,
    summary: str | None = None,
    body: str | None = None,
    *,
    finbert: dict[str, float | str] | None = None,
) -> SentimentResult:
    headline = " ".join(filter(None, [title, summary])).strip()
    article_text = " ".join(filter(None, [title, summary, body])).strip()

    vader_headline = analyze_vader(headline) if headline else None
    vader_article = analyze_vader(article_text) if article_text and article_text != headline else vader_headline
    if finbert is None:
        finbert = analyze_finbert(article_text or headline)

    compound = None
    label = None
    score = None
    if finbert:
        label = str(finbert["label"])
        if label == "positive":
            score = float(finbert["pos"])
        elif label == "negative":
            score = -float(finbert["neg"])
        else:
            score = 0.0
    elif vader_article:
        label, score = _label_from_compound(vader_article["compound"])
        compound = vader_article["compound"]
    elif vader_headline:
        label, score = _label_from_compound(vader_headline["compound"])
        compound = vader_headline["compound"]

    return SentimentResult(
        label=label,
        score=score,
        vader_compound=(vader_article or vader_headline or {}).get("compound"),
        vader_pos=(vader_article or vader_headline or {}).get("pos"),
        vader_neu=(vader_article or vader_headline or {}).get("neu"),
        vader_neg=(vader_article or vader_headline or {}).get("neg"),
        finbert_label=str(finbert["label"]) if finbert else None,
        finbert_pos=float(finbert["pos"]) if finbert else None,
        finbert_neu=float(finbert["neu"]) if finbert else None,
        finbert_neg=float(finbert["neg"]) if finbert else None,
    )


def analyze_sentiment(title: str, summary: str | None = None, body: str | None = None) -> SentimentResult:
    return build_sentiment_result(title, summary=summary, body=body)
