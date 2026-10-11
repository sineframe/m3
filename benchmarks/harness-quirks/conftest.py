import sys
from pathlib import Path

# Lets eval tests (in hyphenated, non-package dirs) import the shared `_lib`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
