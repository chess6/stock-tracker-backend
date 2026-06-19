from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .embeddings_service import cosine_similarity, vector_from_json

if TYPE_CHECKING:
    from ..repositories import Repository

logger = logging.getLogger(__name__)

CLUSTER_SIMILARITY_THRESHOLD = 0.80
CLUSTER_WINDOW_HOURS = 72


def _etld_plus_one(domain: str | None) -> str | None:
    if not domain:
        return None
    parts = domain.lower().strip().split(".")
    if len(parts) < 2:
        return domain.lower()
    return ".".join(parts[-2:])


def _mean_vector(left: list[float], right: list[float], *, left_weight: int, right_weight: int) -> list[float]:
    total = left_weight + right_weight
    if total <= 0:
        return right
    return [
        ((left[idx] * left_weight) + (right[idx] * right_weight)) / total
        for idx in range(min(len(left), len(right)))
    ]


def assign_article_to_event_cluster(
    repo: Repository,
    *,
    article_id: int,
    event_type: str | None,
    headline: str | None,
    published_at: str | None,
    source_domain: str | None,
    sentiment_score: float | None,
    vector: list[float] | None,
) -> int | None:
    if not event_type or not vector:
        return None

    now = (published_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()).replace("+00:00", "Z")
    domain_key = _etld_plus_one(source_domain)
    candidates = repo.find_recent_event_clusters(event_type, hours=CLUSTER_WINDOW_HOURS)
    best_cluster_id = None
    best_similarity = None
    for candidate in candidates:
        centroid = vector_from_json(candidate.get("centroid_json") or "[]")
        if not centroid:
            continue
        similarity = cosine_similarity(vector, centroid)
        if similarity >= CLUSTER_SIMILARITY_THRESHOLD and (best_similarity is None or similarity > best_similarity):
            best_cluster_id = int(candidate["id"])
            best_similarity = similarity

    if best_cluster_id is None:
        cluster_id = repo.create_event_cluster(
            {
                "event_type": event_type,
                "headline": headline,
                "first_seen_at": now,
                "last_seen_at": now,
                "article_count": 1,
                "source_count": 1 if domain_key else 0,
                "source_domains": [domain_key] if domain_key else [],
                "consensus_sentiment": sentiment_score,
                "centroid": vector,
            }
        )
        repo.assign_article_event_cluster(article_id, cluster_id)
        logger.info(
            "event_cluster created cluster_id=%s event_type=%s article_id=%s",
            cluster_id,
            event_type,
            article_id,
        )
        return cluster_id

    candidate = next(row for row in candidates if int(row["id"]) == best_cluster_id)
    domains: list[str] = []
    if candidate.get("source_domains_json"):
        try:
            domains = json.loads(candidate["source_domains_json"])
        except json.JSONDecodeError:
            domains = []
    if domain_key and domain_key not in domains:
        domains.append(domain_key)
    old_centroid = vector_from_json(candidate.get("centroid_json") or "[]")
    article_count = int(candidate.get("article_count") or 1) + 1
    new_centroid = _mean_vector(old_centroid, vector, left_weight=article_count - 1, right_weight=1)
    prior_sentiment = candidate.get("consensus_sentiment")
    if prior_sentiment is None:
        consensus = sentiment_score
    elif sentiment_score is None:
        consensus = prior_sentiment
    else:
        consensus = ((float(prior_sentiment) * (article_count - 1)) + float(sentiment_score)) / article_count

    repo.update_event_cluster(
        best_cluster_id,
        {
            "headline": headline or candidate.get("headline"),
            "last_seen_at": now,
            "article_count": article_count,
            "source_count": len(domains),
            "source_domains": domains,
            "consensus_sentiment": consensus,
            "centroid": new_centroid,
        },
    )
    repo.assign_article_event_cluster(article_id, best_cluster_id)
    logger.info(
        "event_cluster joined cluster_id=%s similarity=%.3f article_id=%s source_count=%d",
        best_cluster_id,
        best_similarity or 0.0,
        article_id,
        len(domains),
    )
    return best_cluster_id


def build_cluster_response(
    repo: Repository,
    *,
    event_type: str | None = None,
    hours: int = 72,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    clusters, total = repo.list_event_clusters(
        event_type=event_type,
        hours=hours,
        limit=limit,
        offset=offset,
    )
    for cluster in clusters:
        cluster["articles"] = repo.get_event_cluster_members(cluster["id"], limit=10)
    return {
        "total": total,
        "returned": len(clusters),
        "limit": limit,
        "offset": offset,
        "hours": hours,
        "eventType": event_type,
        "clusters": clusters,
    }
