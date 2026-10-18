"""Hostile-file defences for uploaded documents. Uploads are untrusted: a report can be a weapon (active PDF content, embedded files, zip bombs,
XML entity bombs, gigantic page or image dimensions). These checks run BEFORE any text extraction or OCR.

This is parser hardening and content-type policy, not malware scanning: no antivirus engine is bundled. A deployment that needs signature-based scanning
must add one (for example ClamAV) in front of storage; see docs/SECURITY_AND_PRIVACY.md. Rejections carry patient-safe messages (no internals)."""
from __future__ import annotations

import re
import zipfile

MAX_PDF_OBJECTS = 200_000
MAX_PAGE_POINTS = 5_000                  # a poster is ~2400 pt; A4 is 595 x 842
MAX_IMAGE_PIXELS = 60_000_000
MAX_ZIP_MEMBERS = 2_000
MAX_ZIP_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100                      # uncompressed / compressed, per member
MAX_XML_BYTES = 20 * 1024 * 1024

# PDF name objects that mean "run something" or "carry another file"
_ACTIVE = re.compile(r"/(JavaScript|JS|Launch|OpenAction|AA|RichMedia|SubmitForm|ImportData|GoToE|GoToR|Sound|Movie|EmbeddedFile|EmbeddedFiles|FileAttachment|XFA)\b")


class Rejected(Exception):
    """Message is safe to show to the uploader."""


def check_pdf(doc) -> None:
    if doc.xref_length() > MAX_PDF_OBJECTS:
        raise Rejected("This PDF is too complex to process safely. Please upload a simpler copy.")
    if doc.embfile_count() > 0:
        raise Rejected("This PDF contains embedded files, which are not accepted. Please upload the report itself.")
    if doc.is_form_pdf:
        raise Rejected("This PDF is an interactive form, which is not accepted. Please print it to a new PDF and upload that.")
    cat = doc.pdf_catalog()
    if cat > 0 and _ACTIVE.search(doc.xref_object(cat, compressed=False) or ""):
        raise Rejected("This PDF contains active content (scripts or launch actions), which is not accepted. Please upload a flattened copy.")
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if obj and _ACTIVE.search(obj):
            raise Rejected("This PDF contains active content (scripts, actions or attachments), which is not accepted. Please upload a flattened copy.")
    for page in doc:
        r = page.rect
        if r.width > MAX_PAGE_POINTS or r.height > MAX_PAGE_POINTS:
            raise Rejected("This PDF has an unusually large page size. Please upload a standard-size copy.")


def check_image_size(width: int, height: int) -> None:
    if width * height > MAX_IMAGE_PIXELS or width <= 0 or height <= 0:
        raise Rejected("This image is too large to process safely. Please upload a smaller copy.")


def check_docx(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise Rejected("This Word file has too many parts. Please save it as PDF and upload that.")
    total = 0
    for i in infos:
        name = i.filename.lower()
        if name.startswith("/") or ".." in i.filename.split("/") or "\\" in i.filename:
            raise Rejected("This Word file is damaged or unsafe. Please save it as PDF and upload that.")
        if name.endswith(("vbaproject.bin", ".exe", ".dll", ".js", ".vbs", ".bat", ".ps1")) or "/embeddings/" in name or "activex" in name:
            raise Rejected("This Word file contains macros or embedded objects, which are not accepted. Please save it as PDF and upload that.")
        total += i.file_size
        if i.file_size > MAX_XML_BYTES * 2 or (i.compress_size > 0 and i.file_size / i.compress_size > MAX_ZIP_RATIO and i.file_size > 1_000_000):
            raise Rejected("This Word file is not accepted because its contents expand abnormally.")
    if total > MAX_ZIP_UNCOMPRESSED:
        raise Rejected("This Word file is too large once unpacked. Please save it as PDF and upload that.")


def check_xml(xml: bytes) -> None:
    """Refuse DTDs and entity declarations outright (entity-expansion and external-entity attacks); office XML never needs them."""
    if len(xml) > MAX_XML_BYTES:
        raise Rejected("This Word file is too large to process safely.")
    head = xml[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head or b"<!entity" in xml.lower():
        raise Rejected("This Word file is not accepted because it contains unsafe XML definitions.")
