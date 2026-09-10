"""Validate downloaded fault-control evidence and make a compact review image.

Demonstration only: this does not change or substitute for the tested harness.
Requires Pillow. Input directories are baseline/, skip-page/, drop-progress/,
each containing the two original GitHub Actions artifact directories.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HARNESS = "59bda6bc38fa074d2b2b3b2b03dd877c7fc5464d"
CONTROLS = {
    "baseline": (HARNESS, "passed", None),
    "skip-page": ("24ce277c697bc9326de67bb031ef30d53974209d", "failed",
                  "Round-trip navigation changed content: page-1 / page-1-back"),
    "drop-progress": ("cad733b6b405e6d91c9bd8a3a061a867bfffe0c7", "failed",
                      "Expected one persisted EPUB progress file"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = []
    sources = {}
    for control, (sha, status, error) in CONTROLS.items():
        for device in ("x3", "x4"):
            matches = list((args.artifacts / control).glob(f"simulator-{device}-*"))
            assert len(matches) == 1, (control, device, matches)
            source = matches[0]
            sources[control, device] = source
            assert (source / "firmware-sha.txt").read_text().strip() == sha
            assert (source / "harness-sha.txt").read_text().strip() == HARNESS
            assert "[SUCCESS]" in (source / "build.log").read_text()
            result = json.loads((source / "smoke/result.json").read_text())
            assert result["status"] == status, result
            assert result.get("error") == error, result
            fixture = (source / "smoke/fs_/books/smoke.epub").read_bytes()
            records.append({"control": control, "device": device, "firmware_sha": sha,
                            "harness_sha": HARNESS, "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
                            "build": "passed", "result": result})
    assert len({record["fixture_sha256"] for record in records}) == 1
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "results.json").write_text(json.dumps(records, indent=2) + "\n")

    canvas = Image.new("RGB", (960, 868), "#eeeeee")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=19)
    for x, control, title in ((0, "baseline", "Working firmware: returns to page 1"),
                              (480, "skip-page", "Injected skip: returns to page 2")):
        source = sources[control, "x4"] / "smoke/page-1-back.png"
        with Image.open(source) as image:
            assert image.size == (480, 800)
            canvas.paste(image.convert("RGB"), (x, 68))
        draw.text((x + 10, 10), title, font=font, fill="black")
        draw.text((x + 10, 37), "Same test: NEXT, then PREVIOUS", font=font, fill="black")
    draw.line((479, 0, 479, 867), fill="#888888", width=2)
    canvas.save(args.output / "navigation-control.png")
    print(f"Verified {len(records)} device/control results; one unchanged fixture and harness")


if __name__ == "__main__":
    main()
