---
id: ADR-12
title: Reuse the Glue ingestion pipeline
kind: adr
status: confirmed
applies_to: DE-104
source: docs/ADR-12.md
# Rule-detector patterns. A negated mention ("do not ...") of a forbid pattern is ignored;
# a negated mention of a require pattern counts as a conflict.
forbid: \b(new|separate|standalone)\b[\w\s-]{0,40}\b(lambda|ingestion service|serverless)\b
forbid: \blambda\b[\w\s-]{0,30}\b(service|function|handler|ingestion)\b
require: \b(reuse|extend)\b[\w\s-]{0,20}\bglue\b
---
Extend the existing AWS Glue ingestion pipeline. Do not introduce a new Lambda ingestion service without an approved exception.
