"""Makes the test pictures for e2e/photo-analysis.mjs from ONE real face photo (supplied by the caller; nothing here is committed with a face in it).

    python make_photo_fixtures.py <face.jpg> <out_dir>

  face_exif6.jpg    the same photo stored rotated a quarter turn, with the EXIF orientation tag (6) that makes every viewer show it upright: tests orientation handling
  face.webp         the same photo as WebP (a format the server does not read: the app converts it to JPEG of the same pixels before sending)
  corrupt.jpg       the first part of the JPEG only: it cannot be decoded
  not_a_photo.jpg   a text file with a .jpg name
  grid_800x600.png  CONTROLLED picture for the viewer alignment tests: flat grey with a pure-green rectangle and a pure-blue rectangle at known pixel positions (the e2e test pins where the
                    viewer draws a marker for a given normalised box against where these pixels really appear on screen). This is a test double of a photo, not a model evaluation.
  grid_600x900.png  the same idea for a tall photo
"""
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def main(src: str, out: str) -> None:
    o = Path(out)
    o.mkdir(parents=True, exist_ok=True)
    face = Image.open(src).convert("RGB")

    stored = face.rotate(90, expand=True)                  # counter-clockwise pixels; tag 6 says "rotate 90 clockwise to display" -> upright again
    exif = Image.Exif()
    exif[0x0112] = 6
    stored.save(o / "face_exif6.jpg", quality=92, exif=exif)

    face.save(o / "face.webp", quality=90)

    buf = io.BytesIO()
    face.save(buf, "JPEG", quality=90)
    (o / "corrupt.jpg").write_bytes(buf.getvalue()[: int(len(buf.getvalue()) * 0.35)])
    (o / "not_a_photo.jpg").write_text("This is not a photo. It is plain text with a .jpg name.\n")

    boxes = {}
    for name, (w, h), green, blue in (("grid_800x600.png", (800, 600), (200, 150, 360, 270), (560, 360, 680, 480)),
                                      ("grid_600x900.png", (600, 900), (120, 180, 300, 360), (360, 600, 480, 780))):
        im = Image.new("RGB", (w, h), (128, 128, 128))
        d = ImageDraw.Draw(im)
        d.rectangle([green[0], green[1], green[2] - 1, green[3] - 1], fill=(0, 255, 0))
        d.rectangle([blue[0], blue[1], blue[2] - 1, blue[3] - 1], fill=(0, 0, 255))
        im.save(o / name)
        n = lambda b: {"x": b[0] / w, "y": b[1] / h, "w": (b[2] - b[0]) / w, "h": (b[3] - b[1]) / h}
        boxes[name] = {"size": [w, h], "green": n(green), "blue": n(blue)}
    (o / "grid_boxes.json").write_text(json.dumps(boxes, indent=2))
    print(json.dumps({"face": list(face.size), "files": sorted(p.name for p in o.iterdir())}))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
