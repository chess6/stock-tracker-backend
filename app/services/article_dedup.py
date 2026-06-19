from __future__ import annotations

import hashlib
import re
import string
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    from rapidfuzz import fuzz  # type: ignore
except ImportError:  # pragma: no cover
    fuzz = None

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)


def compute_simhash(title: str, summary: str | None = None) -> str:
    text = f"{(title or '').lower().strip()} {(summary or '').lower().strip()}"
    text = text.translate(_PUNCTUATION_TABLE).strip()
    words = text.split()
    trigrams = [" ".join(words[i : i + 3]) for i in range(max(len(words) - 2, 1))]
    if not trigrams:
        trigrams = [text]
    v = [0] * 64
    for trigram in trigrams:
        h = int(hashlib.md5(trigram.encode()).hexdigest(), 16) & ((1 << 64) - 1)
        for i in range(64):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fingerprint = 0
    for i in range(64):
        if v[i] > 0:
            fingerprint |= 1 << i
    return f"{fingerprint:016x}"


_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "partner",
    "cmpid",
}


def _normalize_seeking_alpha_path(path: str) -> str:
    match = re.match(r"^/article/(\d+)(?:-[^/]+)?/?$", path, flags=re.IGNORECASE)
    if match:
        return f"/article/{match.group(1)}"
    return path


def canonicalize_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    path = parsed.path
    if "seekingalpha.com" in parsed.netloc.lower():
        path = _normalize_seeking_alpha_path(path)
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        query=urlencode(query),
        fragment="",
    )
    return urlunparse(normalized)


def normalize_published_at(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if "T" in stripped and re.search(r"\d{4}-\d{2}-\d{2}T", stripped):
        return stripped.replace("+00:00", "Z")
    try:
        dt = parsedate_to_datetime(stripped)
        if dt.tzinfo is None:
            return dt.replace(microsecond=0).isoformat()
        return dt.astimezone().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return stripped


def dedup_fingerprint(title: str, summary: str | None = None) -> str:
    title_norm = re.sub(r"\s+", " ", (title or "").lower()).strip()
    summary_norm = re.sub(r"\s+", " ", (summary or "").lower()).strip()[:500]
    return f"{title_norm} | {summary_norm}"


def semantic_similarity(left: str, right: str) -> float:
    if fuzz is None:
        return 100.0 if left == right else 0.0
    return float(fuzz.token_set_ratio(left, right))


def find_semantic_duplicate(
    title: str,
    summary: str | None,
    candidates: list[dict],
    *,
    threshold: int = 88,
) -> int | None:
    fingerprint = dedup_fingerprint(title, summary)
    for candidate in candidates:
        candidate_fp = dedup_fingerprint(candidate.get("title") or "", candidate.get("summary"))
        if semantic_similarity(fingerprint, candidate_fp) >= threshold:
            return candidate["id"]
    return None
