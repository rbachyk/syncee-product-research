"""Postgres persistence backend (replaces Baserow for scale).

Implements the same ``ScanPersistence`` + ``ReviewOps`` protocols as the Baserow/in-memory
backends, so scan/score/enrich/select/publish-prep logic is unchanged. Rows are stored as
JSONB (mirroring the flexible field model) with a few indexed key columns for the dashboard.
"""
