"""Quotations built only from the clinic's approved price list. Prices are never invented:
a quote line must reference an active, doctor-approved price item. Quotes are drafts until a doctor approves."""
from __future__ import annotations

import io
import json

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .. import db


class QuoteError(Exception):
    pass


DEFAULT_CONFIG = {"tax_percent": 0.0, "payment_options": [], "inclusions": [], "terms": ""}


def get_config() -> dict:
    """Clinic-configured quotation settings. Empty by default: nothing is shown that the clinic did not configure."""
    row = db.q1("SELECT value FROM settings WHERE key='quote_config'")
    return {**DEFAULT_CONFIG, **(json.loads(row["value"]) if row else {})}


def set_config(cfg: dict):
    db.x("INSERT OR REPLACE INTO settings VALUES('quote_config', ?)", (json.dumps({**DEFAULT_CONFIG, **cfg}),))


def build_lines(requested: list[dict]) -> dict:
    if not requested:
        raise QuoteError("Please choose at least one item.")
    lines, total, cur = [], 0.0, None
    for r in requested:
        item = db.q1("SELECT * FROM price_items WHERE id=? AND active=1 AND approved_by IS NOT NULL AND approved_by!=''", (r.get("item_id"),))
        if not item:
            raise QuoteError("One of the requested items is not on the clinic's approved price list.")
        qty = int(r.get("qty", 1))
        if qty < 1 or qty > 100:
            raise QuoteError("Quantity must be between 1 and 100.")
        cur = cur or item["currency"]
        if item["currency"] != cur:
            raise QuoteError("All items in one quotation must use the same currency.")
        lt = round(item["unit_price"] * qty, 2)
        total += lt
        lines.append({"item_id": item["id"], "name": item["name"], "description": item["description"], "qty": qty,
                      "unit_price": item["unit_price"], "line_total": lt, "category": item["category"],
                      "duration_min": item["duration_min"]})
    subtotal = round(total, 2)
    pct = float(get_config()["tax_percent"])
    tax = round(subtotal * pct / 100, 2)
    return {"lines": lines, "subtotal": subtotal, "tax_percent": pct, "tax_amount": tax, "total": round(subtotal + tax, 2), "currency": cur}


def pdf(quote: dict, patient_name: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"Cosmo Experts quotation {quote['id'][:8]}")
    st = getSampleStyleSheet()
    approved = quote["status"] == "approved"
    story = [Paragraph("Cosmo Experts", st["Title"]),
             Paragraph(f"Estimated quotation {quote['id'][:8]}", st["Heading2"]),
             Paragraph(f"Prepared for: {patient_name}", st["Normal"]),
             Paragraph("Status: <b>%s</b>" % ("Approved by the clinic" if approved else "DRAFT - not yet approved by a doctor"), st["Normal"]),
             Spacer(1, 8)]
    rows = [["Item", "Qty", "Unit price", "Amount"]]
    for l in json.loads(quote["lines"]) if isinstance(quote["lines"], str) else quote["lines"]:
        rows.append([l["name"], str(l["qty"]), f'{l["unit_price"]:.2f}', f'{l["line_total"]:.2f}'])
    if quote.get("tax_amount"):
        rows.append(["", "", "Subtotal", f'{quote["subtotal"]:.2f}'])
        rows.append(["", "", f'Tax ({quote["tax_percent"]:g}%)', f'{quote["tax_amount"]:.2f}'])
    rows.append(["", "", "Total (%s)" % quote["currency"], f'{quote["total"]:.2f}'])
    t = Table(rows, colWidths=[85 * mm, 15 * mm, 35 * mm, 35 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3d36")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -2), 0.25, colors.grey), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story += [t, Spacer(1, 10)]
    if quote.get("notes"):
        story.append(Paragraph("Notes: " + quote["notes"].replace("<", "&lt;"), st["Normal"]))
    story.append(Paragraph("This is an estimate based on the clinic's approved price list. It is not a treatment recommendation. "
                           "Any treatment must be chosen by your doctor after examination, and final charges may differ.", st["Italic"]))
    doc.build(story)
    return buf.getvalue()
