"""Shared event schema used by every ingestion path."""
from dataclasses import dataclass


@dataclass(frozen=True)
class EventSchema:
    """Required fields and types for one customer event type."""

    name: str
    required: dict[str, type]


def validate_event(event: dict, schema: EventSchema) -> None:
    """Raise ValueError if an event is missing a required field or has the wrong type."""
    for field, kind in schema.required.items():
        if not isinstance(event.get(field), kind):
            raise ValueError(f"{schema.name}: bad or missing field {field}")
