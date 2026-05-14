import sys
from pathlib import Path

# Make `src/rsdlc/` importable without needing `pip install -e .` or PYTHONPATH.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rsdlc.ui import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
