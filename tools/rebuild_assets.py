"""Public asset-pipeline entry point for this repository."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/freaks"))


if __name__ == "__main__":
    try:
        from asset_workspace import main

        main(ROOT)
    except ModuleNotFoundError as error:
        if error.name in {"PIL", "capstone", "fontTools", "numpy", "unicorn"}:
            raise SystemExit(f"[ERROR] {error}. Install the build dependencies: "
                             "python -m pip install -r tools/requirements.txt")
        raise
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
