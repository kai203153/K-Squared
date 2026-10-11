"""AWS Glue job that ingests customer events into the analytics lake."""
from lib.events.schema import EventSchema, validate_event

EVENT_TYPES: dict[str, EventSchema] = {}


def register_event_type(name: str, schema: EventSchema) -> None:
    """Add a new customer event type to the existing Glue ingestion pipeline."""
    EVENT_TYPES[name] = schema


class CustomerEventsJob:
    """Reads raw events from S3, validates them, and writes partitioned Parquet."""

    def run(self, events: list[dict]) -> list[dict]:
        valid = []
        for event in events:
            schema = EVENT_TYPES[event["type"]]
            validate_event(event, schema)
            valid.append(event)
        return valid
