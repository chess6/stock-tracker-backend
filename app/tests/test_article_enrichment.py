from __future__ import annotations

from app.services.article_enrichment import infer_topic_cluster, simple_sentiment, simhash_fingerprint


def test_simple_sentiment_positive():
    label, score = simple_sentiment("Company beats earnings and posts record profit")
    assert label == "positive"
    assert score is not None and score > 0


def test_infer_topic_cluster_semis():
    assert infer_topic_cluster("Semiconductor shortage hits chip supply chain") == "semis"


def test_simhash_fingerprint_stable():
    assert simhash_fingerprint("alpha beta gamma") == simhash_fingerprint("alpha beta gamma")
