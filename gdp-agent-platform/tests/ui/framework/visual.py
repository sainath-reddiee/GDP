"""Screenshot baselines with pixel diff (Pillow). First run writes the baseline; later runs compare.

Set AIP_UI_UPDATE_BASELINES=1 to overwrite baselines after an intentional UI change.
Dynamic content (times, counts, ids) should be masked with `mask=[locator, ...]`.
"""
import os
from pathlib import Path

from playwright.sync_api import Page

BASE = Path(__file__).resolve().parents[1] / "baselines"
OUT = Path(os.environ.get("AIP_UI_ARTIFACTS", Path(__file__).resolve().parents[1] / "artifacts")) / "visual"
UPDATE = os.environ.get("AIP_UI_UPDATE_BASELINES") == "1"
THRESHOLD = float(os.environ.get("AIP_UI_VISUAL_THRESHOLD", "0.02"))  # fraction of changed pixels


def compare(page: Page, name: str, mask=None, full_page: bool = False) -> float:
    from PIL import Image, ImageChops

    BASE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    actual = OUT / f"{name}.actual.png"
    page.screenshot(path=str(actual), full_page=full_page, mask=mask or [], animations="disabled", caret="hide")
    baseline = BASE / f"{name}.png"
    if UPDATE or not baseline.exists():
        baseline.write_bytes(actual.read_bytes())
        return 0.0
    a, b = Image.open(baseline).convert("RGB"), Image.open(actual).convert("RGB")
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0)
    changed = sum(1 for v in diff.getdata() if v) / (a.size[0] * a.size[1])
    if changed > THRESHOLD:
        diff.save(OUT / f"{name}.diff.png")
    return changed
