# ADR-12: Reuse the Glue ingestion pipeline

Status: Accepted (confirmed by the analytics platform owner)

## Context
Customer events are ingested by the AWS Glue job in `pipelines/glue/customer_events_job.py`
(provisioned in `infra/main.tf`). New event types keep arriving from product teams.

## Decision
Extend the existing AWS Glue ingestion pipeline. Do not introduce a new Lambda ingestion service without an approved exception.

New event types are added with `register_event_type()` and validated by the shared
`validate_event()` in `lib/events/schema.py`.

## Consequences
One ingestion path, one schema validator, one set of infrastructure and alerts. Requests for
an exception (for example, a latency requirement Glue cannot meet) go to the platform owner
with supporting evidence.
