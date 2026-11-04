"""Public asset-pipeline entry point for this repository."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-lab/freaks/tools"))

from asset_workspace import main


if __name__ == "__main__":
    try:
        main(ROOT)
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
