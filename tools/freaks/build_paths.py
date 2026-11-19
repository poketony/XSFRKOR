"""Paths for the standalone asset tool; analysis files are never inputs."""

from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
DATA = TOOLS / "build_data"
WORK = REPO / "rebuilt/.work"
CAMPAIGN_MODULES = Path(__file__).resolve().parent / "mini_campaign"
