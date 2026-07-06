"""Sidecar panel (BUILD_PLAN WP8; UX.md "sidecar web panel").

Read-mostly FastAPI adapter over ``infersynth.catalog`` and
``infersynth.gates``: catalog browser with rendered fragment SVGs, and a
gate-report viewer. Owns no synthesis or lint logic (UX.md anti-goals) —
everything here is presentation over the library.
"""
