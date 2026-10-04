"""Read-only environment preflight; no download, grading or paid run."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from rsi.environment import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parents[1]))
