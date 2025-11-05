"""Transformers: timestamp normalization, geotagging, features, effort calculations.

Rationale:
- Encapsulate domain logic so it’s independently testable and not tied to I/O.
- Keep pure functions where possible for determinism and easier validation.
"""
