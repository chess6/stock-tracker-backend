"""Safe overwrite guards for pipeline writes (Phase G5)."""

from __future__ import annotations


def fundamental_identity(record: dict) -> tuple:
    return (
        record["metric"],
        record["period_end"],
        record["dimension"],
        record.get("filing_date"),
        record.get("xbrl_concept"),
    )


def incoming_source_timestamp(record: dict) -> str | None:
    return record.get("source_updated_at") or record.get("filing_date")


def should_skip_fundamental_overwrite(
    incoming_at: str | None,
    stored_at: str | None,
    *,
    force_refresh: bool = False,
) -> bool:
    if force_refresh:
        return False
    if not stored_at or not incoming_at:
        return False
    return incoming_at <= stored_at


def partition_fundamentals_records(
    records: list[dict],
    stored: dict[tuple, str | None],
    *,
    force_refresh: bool = False,
) -> tuple[list[dict], list[dict]]:
    upsert: list[dict] = []
    skipped: list[dict] = []
    for record in records:
        key = fundamental_identity(record)
        incoming_at = incoming_source_timestamp(record)
        stored_at = stored.get(key)
        if should_skip_fundamental_overwrite(incoming_at, stored_at, force_refresh=force_refresh):
            skipped.append(
                {
                    "metric": record.get("metric"),
                    "period_end": record.get("period_end"),
                    "dimension": record.get("dimension"),
                    "filing_date": record.get("filing_date"),
                    "xbrl_concept": record.get("xbrl_concept"),
                    "incomingSourceUpdatedAt": incoming_at,
                    "storedSourceUpdatedAt": stored_at,
                    "reason": "older_or_equal_source",
                }
            )
            continue
        upsert.append(record)
    return upsert, skipped
