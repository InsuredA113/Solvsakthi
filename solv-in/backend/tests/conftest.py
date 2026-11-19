"""Test-only environment defaults required during pytest collection.

Never copy these values into a production environment.
"""
import os

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "solvin-pytest-only-secret-key-do-not-use-in-production-2026",
)
