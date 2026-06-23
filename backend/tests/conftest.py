"""Shared pytest fixtures and path setup."""
import os
import sys

# Ensure the backend package root is importable regardless of where pytest runs.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
