"""Extractors for raw sources (summary logs, filename lists, recorder metadata).

Rationale:
- Separate I/O parsing from transformations to keep stages testable and composable.
- Readers can be reused across different pipelines or CLI tools.
"""
