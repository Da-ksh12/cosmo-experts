"""Medical document intelligence: validate -> extract text (embedded or OCR) -> segment -> classify ->
extract fields with source traceability. Everything runs locally; nothing leaves the machine.

Rules enforced here:
* exact source value and unit are preserved, never converted;
* reference ranges are only those printed in the source;
* "outside range" is computed ONLY against a printed range and labelled as such;
* anything without a source page/text is never emitted as an extracted fact."""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

from ..config import MAX_PDF_PAGES, MAX_UPLOAD_BYTES

PROCESSING_VERSION = "docpipe-1.0.0"
ALLOWED = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
           "txt": "text/plain", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class DocumentError(Exception):
    """Carries a patient-safe message."""


_TESS_CANDIDATES = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                    "/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"]
_tess_cmd: str | None = None


def tesseract_cmd() -> str | None:
    """Locate Tesseract: COSMO_TESSERACT_CMD, PATH, then well-known install locations."""
    global _tess_cmd
    if _tess_cmd:
        return _tess_cmd
    import os
    for c in (os.environ.get("COSMO_TESSERACT_CMD"), shutil.which("tesseract"), *_TESS_CANDIDATES):
        if c and os.path.isfile(c):
            _tess_cmd = c
            return c
    return None


def ocr_available() -> bool:
    return tesseract_cmd() is not None


def ocr_languages() -> list[str]:
    cmd = tesseract_cmd()
    if not cmd:
        return []
    import subprocess
    out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split("\n")[1:]
    return [x.strip() for x in out if x.strip()]


@dataclass
class Page:
    page_no: int
    text: str
    method: str            # embedded | ocr | plain | ocr_unavailable
    confidence: float | None = None


@dataclass
class Field:
    name: str
    value: str
    unit: str
    ref_range: str
    source_page: int
    source_text: str
    confidence: float
    flag: str              # "" | "printed_flag_high" | "printed_flag_low" | "outside_printed_range" | "within_printed_range"
    review_status: str = "unreviewed"


@dataclass
class Result:
    doc_type: str
    doc_date: str
    pages: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    needs_review: bool = True
    version: str = PROCESSING_VERSION


# ---------- validation ----------

def sniff(data: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise DocumentError("This file type is not supported. Please upload a PDF, JPG, PNG, DOCX or TXT file.")
    head = data[:8]
    ok = {"pdf": head.startswith(b"%PDF"), "jpg": head.startswith(b"\xff\xd8"), "jpeg": head.startswith(b"\xff\xd8"),
          "png": head.startswith(b"\x89PNG"), "docx": head.startswith(b"PK"),
          "txt": b"\x00" not in data[:1024]}[ext]
    if not ok:
        raise DocumentError("The file contents do not match its file type. It may be damaged. Please upload a fresh copy.")
    return ext


def validate(data: bytes, filename: str) -> str:
    if not data:
        raise DocumentError("This file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError(f"This file is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return sniff(data, filename)


# ---------- text extraction ----------

def _preprocess(pil_image):
    """Orientation fix (Tesseract OSD), upscale small scans, grayscale + light denoise. Returns (image, rotation_degrees)."""
    import cv2
    import numpy as np
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()
    img = np.array(pil_image.convert("L"))
    if min(img.shape) < 1200:
        k = 1500 / min(img.shape)
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    rot = 0
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0", output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0)) if float(osd.get("orientation_conf", 0)) >= 2.0 else 0
    except Exception:
        rot = 0   # OSD fails on sparse pages; keep the original orientation rather than guess
    if rot:
        img = np.rot90(img, k=-(rot // 90))   # OSD "rotate" = degrees clockwise needed to upright the page
        img = np.ascontiguousarray(img)
    img = cv2.fastNlMeansDenoising(img, None, 8, 7, 21) if img.std() < 60 else img
    from PIL import Image
    return Image.fromarray(img), rot


def _ocr_image(pil_image) -> tuple[str, float | None]:
    import pytesseract
    img, _ = _preprocess(pil_image)
    cfg = "--psm 6"
    d = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(d["conf"], d["text"]) if w.strip() and float(c) >= 0]
    text = pytesseract.image_to_string(img, config=cfg)
    return text, (sum(confs) / len(confs) / 100 if confs else None)


def _page_lines(page) -> str:
    """Rebuild visual lines from word boxes so table rows stay on one line (plain get_text splits cells apart)."""
    words = page.get_text("words")
    if not words:
        return ""
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines, cur, cy = [], [], None
    for w in words:
        yc, h = (w[1] + w[3]) / 2, max(w[3] - w[1], 1)
        if cy is not None and abs(yc - cy) > 0.6 * h:
            lines.append(cur); cur = []
        cur.append(w); cy = yc if not cur[:-1] else cy
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        text, prev = "", None
        for w in ln:
            if prev is not None:
                text += "  " if w[0] - prev[2] > 9 else " "
            text += w[4]; prev = w
        out.append(text)
    return "\n".join(out)


def _extract_pdf(data: bytes) -> list[Page]:
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise DocumentError("We couldn't open this PDF. It may be damaged. Please upload a fresh copy.")
    if doc.needs_pass or doc.is_encrypted:
        raise DocumentError("This PDF is password-protected. Please remove the password and upload it again.")
    if doc.page_count == 0:
        raise DocumentError("This PDF has no pages.")
    if doc.page_count > MAX_PDF_PAGES:
        raise DocumentError(f"This PDF has too many pages (limit {MAX_PDF_PAGES}). Please upload the relevant pages.")
    pages = []
    for i, page in enumerate(doc, start=1):
        text = _page_lines(page) or page.get_text("text") or ""
        if len(text.strip()) >= 30:
            pages.append(Page(i, text, "embedded", 1.0))
            continue
        if ocr_available():
            from PIL import Image
            pix = page.get_pixmap(dpi=250)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            t, c = _ocr_image(img)
            pages.append(Page(i, t, "ocr", c))
        else:
            pages.append(Page(i, text, "ocr_unavailable", None))
    return pages


def _extract_image(data: bytes) -> list[Page]:
    from PIL import Image, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise DocumentError("We couldn't open this image. Please upload a clear JPG or PNG.")
    if not ocr_available():
        return [Page(1, "", "ocr_unavailable", None)]
    t, c = _ocr_image(img.convert("RGB"))
    return [Page(1, t, "ocr", c)]


def _extract_docx(data: bytes) -> list[Page]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except Exception:
        raise DocumentError("We couldn't read this Word file. Please save it as PDF and upload again.")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ElementTree.fromstring(xml)
    paras = ["".join(t.text or "" for t in p.iter("{%s}t" % ns["w"])) for p in root.iter("{%s}p" % ns["w"])]
    return [Page(1, "\n".join(paras), "plain", 1.0)]


def extract_pages(data: bytes, ext: str) -> list[Page]:
    if ext == "pdf":
        return _extract_pdf(data)
    if ext in ("jpg", "jpeg", "png"):
        return _extract_image(data)
    if ext == "docx":
        return _extract_docx(data)
    text = data.decode("utf-8", errors="replace")
    return [Page(1, text, "plain", 1.0)]


# ---------- classification ----------

TYPE_KEYWORDS = {
    "laboratory_report": ["reference range", "ref. range", "reference interval", "specimen", "haemoglobin", "hemoglobin",
                          "tsh", "ferritin", "vitamin", "cbc", "thyroid", "serum", "mg/dl", "ng/ml", "g/dl", "lab"],
    "prescription": ["rx", "tab.", "tablet", "capsule", "cap.", "syrup", "dose", "bd", "od ", "tds", "sig:", "prescription"],
    "histopathology_report": ["histopathology", "biopsy", "microscopic", "gross description", "specimen received", "dermis", "epidermis"],
    "consultation_note": ["chief complaint", "history of", "examination", "assessment", "plan:", "follow up", "follow-up", "advice", "impression"],
    "treatment_history": ["treatment history", "previous treatment", "sessions", "procedure done", "prp", "laser", "peel"],
}


def classify(text: str) -> tuple[str, float]:
    low = text.lower()
    scores = {k: sum(1 for w in ws if w in low) for k, ws in TYPE_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] < 2:
        return "other_uncertain", 0.0
    total = sum(scores.values()) or 1
    return best, scores[best] / total


DATE_PATTERNS = [
    (r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", "ymd"),
    (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", "dmy"),
    (r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})\b", "dMy"),
]
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def find_date(text: str) -> str:
    """Return ISO date of the first date near a date-like label, else first date; '' if none.
    Day-first is assumed for numeric dates and the ambiguity is reported by the caller's warnings."""
    labelled = re.search(r"(report(?:ed)?|collected|collection|sample|date)[^\n\d]{0,20}([^\n]{6,20})", text, re.I)
    candidates = [labelled.group(2)] if labelled else []
    for chunk in candidates + [text]:
        for pat, kind in DATE_PATTERNS:
            m = re.search(pat, chunk, re.I)
            if not m:
                continue
            try:
                if kind == "ymd":
                    y, mo, d = map(int, m.groups())
                elif kind == "dmy":
                    d, mo, y = map(int, m.groups())
                else:
                    d, y = int(m.group(1)), int(m.group(3))
                    mo = MONTHS[m.group(2).lower()[:3]]
                if 1 <= mo <= 12 and 1 <= d <= 31 and 1900 < y < 2100:
                    return f"{y:04d}-{mo:02d}-{d:02d}"
            except ValueError:
                continue
    return ""


# ---------- field extraction ----------

NUM = r"[<>]?\s*\d+(?:[.,]\d+)?"
UNIT = r"(?:x?\s?10\s?(?:[\^*]\s?\d+|[²³⁶⁹])\s?/\s?[A-Za-zµμ]+|[A-Za-zµμ%/][A-Za-z0-9µμ%/^.\-\*]*(?:\s?/\s?[A-Za-z0-9µμ.]+)?)"
LAB_LINE = re.compile(
    rf"^\s*(?P<name>[A-Za-z][A-Za-z0-9 ().,/\-%']{{1,48}}?)\s{{1,}}[:\-]?\s*"
    rf"(?P<value>{NUM})\s*(?P<unit>{UNIT})?\s*"
    rf"(?P<flag>\b(?:H|L|HIGH|LOW|High|Low|HH|LL)\b)?\s*"
    rf"(?P<ref>(?:\(?\s*{NUM}\s*(?:-|–|to)\s*{NUM}\s*\)?)|(?:[<>]=?\s*{NUM}))?\s*"
    rf"(?P<flag2>\b(?:H|L|HIGH|LOW|High|Low|HH|LL)\b)?\s*$"
)
SKIP_NAMES = re.compile(r"^(page|date|age|phone|tel|mobile|patient|name|id|uhid|sample|ref|reg|dr|lab)\b", re.I)


KNOWN_UNITS = ["g/dL", "mg/dL", "ng/mL", "pg/mL", "ug/dL", "µg/dL", "ng/dL", "mIU/L", "uIU/mL", "µIU/mL", "mmol/L", "U/L", "IU/L", "%", "fL", "pg", "g/L",
               "10^3/uL", "10^6/uL", "10^9/L", "10^12/L", "mEq/L", "mm/hr", "ug/L", "nmol/L", "pmol/L"]
_CANON = {re.sub(r"[^a-z0-9%/^]", "", u.lower().replace("µ", "u").replace("l", "1").replace("i", "1")): u for u in KNOWN_UNITS}   # l/I/1 are interchangeable to OCR


def snap_unit(unit: str) -> tuple[str, bool]:
    """Correct well-known OCR confusions in a unit (l/I/1, 10*3 -> 10^3) ONLY when the result is an exact entry of the unit lexicon.
    Returns (unit, changed). Unknown units are left exactly as read."""
    if not unit:
        return unit, False
    u = unit.replace(" ", "").replace("μ", "µ")
    u = re.sub(r"^x?10[*^]?([369]|12)/", lambda m: f"10^{m.group(1)}/", u)
    u = u.replace("10³", "10^3").replace("10⁶", "10^6").replace("10⁹", "10^9")
    key = re.sub(r"[^a-z0-9%/^]", "", u.lower().replace("µ", "u").replace("l", "1").replace("i", "1"))
    hit = _CANON.get(key)
    return (hit, hit != unit) if hit else (unit, False)


def _num(s: str) -> float | None:
    s = s.replace(",", ".").strip("<> ").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _range_bounds(ref: str):
    nums = re.findall(r"\d+(?:[.,]\d+)?", ref)
    if re.search(r"-|–|to", ref) and len(nums) >= 2:
        return _num(nums[0]), _num(nums[1])
    if ref.strip().startswith("<") and nums:
        return None, _num(nums[0])
    if ref.strip().startswith(">") and nums:
        return _num(nums[0]), None
    return None, None


def extract_fields(pages: list[Page]) -> list[Field]:
    out: list[Field] = []
    for p in pages:
        for raw in p.text.splitlines():
            line = re.sub(r"\s{2,}", "  ", raw.rstrip())
            m = LAB_LINE.match(line)
            if not m:
                continue
            name = m.group("name").strip(" .:-")
            if SKIP_NAMES.match(name) or len(name) < 2:
                continue
            if any(re.fullmatch(r"\d+(?:[.,]\d+)?", tok) for tok in name.split()):
                continue    # a bare number inside a test name means the line was mis-read; abstain rather than emit a wrong row
            value, unit = m.group("value").strip(), (m.group("unit") or "").strip()
            snapped = False
            if p.method == "ocr":
                unit, snapped = snap_unit(unit)
            ref = (m.group("ref") or "").strip()
            if not ref and not unit:
                continue   # a bare "word number" is too ambiguous to call a result
            printed = (m.group("flag") or m.group("flag2") or "").upper()
            flag = ""
            if printed in ("H", "HIGH", "HH"):
                flag = "printed_flag_high"
            elif printed in ("L", "LOW", "LL"):
                flag = "printed_flag_low"
            elif ref and not value.startswith(("<", ">")):
                lo, hi = _range_bounds(ref)
                v = _num(value)
                if v is not None and (lo is not None or hi is not None):
                    flag = "outside_printed_range" if ((lo is not None and v < lo) or (hi is not None and v > hi)) else "within_printed_range"
            conf = 0.9 if p.method in ("embedded", "plain") else min(0.9, (p.confidence or 0.5)) * 0.85
            if not ref:
                conf -= 0.1
            if snapped:
                conf -= 0.1     # corrected for a known OCR confusion; the raw line stays in source_text for review
            out.append(Field(name, value, unit, ref, p.page_no, line.strip(), round(conf, 2), flag))
    return out


def process(data: bytes, filename: str) -> Result:
    ext = validate(data, filename)
    pages = extract_pages(data, ext)
    full = "\n".join(p.text for p in pages)
    warnings: list[str] = []
    if any(p.method == "ocr_unavailable" for p in pages):
        warnings.append("This looks like a scanned document, and text recognition (OCR) is not installed on the server, "
                        "so it could not be read. Please upload a text-based PDF or ask the clinic to enable OCR.")
    low_ocr = [p.page_no for p in pages if p.method == "ocr" and (p.confidence or 0) < 0.6]
    if low_ocr:
        warnings.append(f"Text on page(s) {', '.join(map(str, low_ocr))} was hard to read. Please check it or upload a clearer copy.")
    if not full.strip() and not warnings:
        warnings.append("We could not find any readable text in this document.")
    dtype, _ = classify(full)
    fields = extract_fields(pages)
    return Result(dtype, find_date(full), pages, fields, warnings, needs_review=True)


# ---------- comparison ----------

def _norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def compare(rows: list[dict]) -> list[dict]:
    """rows: field dicts plus doc_date/document_id. Series are comparable only when the normalised name
    AND the exact unit text match. No conversions are performed; incomparable series are reported as such."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_norm(r["name"]), []).append(r)
    out = []
    for name, items in groups.items():
        if len(items) < 2:
            continue
        units = {i["unit"] for i in items}
        items = sorted(items, key=lambda i: i.get("doc_date") or "")
        comparable = len(units) == 1 and all(i.get("doc_date") for i in items)
        entry = {"name": items[0]["name"], "comparable": comparable, "conversion_performed": False,
                 "points": [{k: i.get(k) for k in ("document_id", "doc_date", "value", "unit", "ref_range", "source_page", "source_text")}
                            for i in items]}
        if not comparable:
            entry["reason"] = ("Units differ between reports, so values are not directly comparable here." if len(units) > 1
                               else "One or more reports have no readable date.")
        else:
            vals = [_num(i["value"]) for i in items]
            if all(v is not None for v in vals):
                entry["observed_change"] = {"first": items[0]["value"], "last": items[-1]["value"], "unit": items[0]["unit"],
                                            "direction": "higher" if vals[-1] > vals[0] else "lower" if vals[-1] < vals[0] else "unchanged"}
            entry["note"] = ("This shows how the printed numbers differ. Whether the change matters depends on the test method, "
                             "timing and your health context, which your doctor must interpret.")
        out.append(entry)
    return out
