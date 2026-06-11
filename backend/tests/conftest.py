"""
pytest configuration — adds the backend directory to sys.path so all
imports work the same way they do when running main.py directly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
