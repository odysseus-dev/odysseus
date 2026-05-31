"""PDF AcroForm field detection and extraction.

Used to decide whether an uploaded PDF should be treated as a fillable form
(routed to the pdf_form document type) versus a regular text PDF (routed
through document_processor._process_pdf).
"""

import logging
import re
from typing import Any

# PyMuPDF is an OPTIONAL dependency (AGPL-3.0), required ONLY for the PDF
# form-filling feature implemented in this module. The MIT core imports fine
# without it; calling these functions without PyMuPDF raises a clear error.
# See pyproject.toml [project.optional-dependencies].
try:
    import fitz  # PyMuPDF — optional, AGPL-3.0
except ImportError:  # pragma: no cover
    fitz = None

logger = logging.getLogger(__name__)

_PYMUPDF_MISSING = (
    "PDF form features require PyMuPDF, an optional dependency. Install it with "
    "`uv pip install \".[optional]\"` (or `pip install \".[optional]\"`; note: PyMuPDF is AGPL-3.0)."
)


def _require_fitz():
    """Raise a clear error if the optional PyMuPDF dependency is absent."""
    if fitz is None:
        raise RuntimeError(_PYMUPDF_MISSING)
    return fitz


def _widget_type_names() -> dict:
    return {
        fitz.PDF_WIDGET_TYPE_UNKNOWN: "unknown",
        fitz.PDF_WIDGET_TYPE_BUTTON: "button",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
        fitz.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
        fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    }

# Text widgets that are really signature placeholders. Covers DocuSign-style
# "_es_:signature" and the bare "signed N" / "Signature" patterns common in
# UK conveyancing forms (TA6, TA10). Uses substring match deliberately —
# false positives like "assigned" are rare in form-field names.
_SIGNATURE_NAME_RE = re.compile(r'sign(?:ed|ature)', re.IGNORECASE)


def has_form_fields(path: str) -> bool:
    """Return True if the PDF looks like a *fillable form* — not just a
    content PDF that happens to carry a stray widget.

    Excel-exported PDFs (Japanese estimates, invoices, etc.) often ship with
    one or two orphan AcroForm widgets (a signature stamp box, a leftover
    text field from the source template) even when they're really
    content-only documents. Treating those as forms routes them through the
    form-fill chat prompt that ASKS the user which field to edit instead of
    discussing the content — which is exactly the bug we're trying to avoid.

    Heuristic: require at least 3 non-signature widgets. Signature-only
    PDFs (e.g. a contract with one sign-here box) read as content, and tiny
    stray-widget counts no longer hijack the chat. Genuine UK conveyancing
    forms (TA6, TA10) and similar carry dozens of widgets and still trip
    this threshold easily.
    """
    _require_fitz()
    try:
        doc = fitz.open(path)
    except Exception as e:
        logger.warning(f"Could not open PDF {path} for form detection: {e}")
        return False
    try:
        non_signature_count = 0
        for page in doc:
            for w in page.widgets() or []:
                if w.field_type != fitz.PDF_WIDGET_TYPE_SIGNATURE:
                    non_signature_count += 1
                    if non_signature_count >= 3:
                        return True
        return False
    finally:
        doc.close()


def _infer_label(page: "fitz.Page", rect: "fitz.Rect", page_words: list) -> str:
    """Best-effort label inference from text near a widget.

    Strategy: prefer text immediately to the left on the same line,
    then text immediately above. Returns the closest non-empty match
    or "" if nothing useful is found. AcroForm field_label is rarely
    populated in real-world forms, so this fallback matters.
    """
    candidates_left = []
    candidates_above = []
    line_tol = max(2.0, rect.height * 0.6)

    for w in page_words:
        wx0, wy0, wx1, wy1, text = w[0], w[1], w[2], w[3], w[4]
        if not text.strip():
            continue
        # Same line, to the left
        if abs((wy0 + wy1) / 2 - (rect.y0 + rect.y1) / 2) < line_tol and wx1 <= rect.x0 + 1:
            candidates_left.append((rect.x0 - wx1, wx0, text))
        # Above, horizontally overlapping
        elif wy1 <= rect.y0 + 1 and not (wx1 < rect.x0 or wx0 > rect.x1):
            candidates_above.append((rect.y0 - wy1, wx0, text))

    def _join_nearest(cands, gap_limit):
        if not cands:
            return ""
        cands.sort(key=lambda c: (c[0], c[1]))
        nearest_dist = cands[0][0]
        if nearest_dist > gap_limit:
            return ""
        same = [c for c in cands if c[0] - nearest_dist < line_tol]
        same.sort(key=lambda c: c[1])
        return " ".join(c[2] for c in same).strip()

    label = _join_nearest(candidates_left, gap_limit=200.0)
    if label:
        return label
    return _join_nearest(candidates_above, gap_limit=40.0)


def _widget_on_state(w) -> str:
    try:
        return w.on_state() or ""
    except Exception:
        return ""


def extract_fields(path: str) -> list[dict[str, Any]]:
    """Enumerate form fields, one entry per unique field name.

    Multiple checkbox widgets sharing a field name are treated as a single
    "choice" field whose options are each widget's on-state — that's the
    PDF idiom for radio-style "Included / Excluded / None" rows.

    Returns dicts with: name, type, label, value, options, page (1-indexed),
    rect (x0,y0,x1,y1) for the first widget in the group, required.
    """
    _require_fitz()
    names = _widget_type_names()
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    try:
        doc = fitz.open(path)
    except Exception as e:
        logger.error(f"Could not open PDF {path} for field extraction: {e}")
        return []

    try:
        for page_index, page in enumerate(doc):
            widgets = page.widgets() or []
            if not widgets:
                continue
            words = page.get_text("words")
            for w in widgets:
                name = w.field_name or ""
                if not name:
                    continue
                wtype = names.get(w.field_type, "unknown")
                label = (getattr(w, "field_label", None) or "").strip()
                if not label:
                    label = _infer_label(page, w.rect, words)
                value = w.field_value if w.field_value is not None else ""
                on_state = _widget_on_state(w) if wtype == "checkbox" else ""

                if name not in grouped:
                    # AdobeSign-style signature placeholders are stored as
                    # plain text widgets but named with `_es_:signature`.
                    if wtype == "text" and _SIGNATURE_NAME_RE.search(name):
                        wtype = "signature"
                    order.append(name)
                    grouped[name] = {
                        "name": name,
                        "type": wtype,
                        "label": label,
                        "value": value,
                        "options": list(w.choice_values) if w.choice_values else (
                            [on_state] if on_state else []
                        ),
                        "page": page_index + 1,
                        "rect": [w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1],
                        "required": bool((w.field_flags or 0) & 2),
                        "_on_states": [on_state] if on_state else [],
                    }
                else:
                    g = grouped[name]
                    if not g["label"] and label:
                        g["label"] = label
                    if value and not g["value"]:
                        g["value"] = value
                    if on_state and on_state not in g["_on_states"]:
                        g["_on_states"].append(on_state)
                        if on_state not in g["options"]:
                            g["options"].append(on_state)
                    # If a checkbox name appears more than once with different on-states,
                    # promote it to a choice field.
                    if wtype == "checkbox" and len(g["_on_states"]) > 1:
                        g["type"] = "choice"
    finally:
        doc.close()

    out = []
    for name in order:
        g = grouped[name]
        g.pop("_on_states", None)
        out.append(g)
    return out


def stamp_signatures(
    pdf_path: str,
    output_path: str,
    stamps: dict[str, bytes],
) -> int:
    """Stamp PNG signature images into the PDF at each named field's rect.

    `stamps` is {field_name: png_bytes}. Each named field is found in the
    AcroForm; the image is drawn into the field's rectangle preserving aspect
    ratio. The widget itself is left intact (still a form field) so it can be
    re-edited later if needed; the stamp is rendered on top.

    Returns the number of stamps written. Pass the source PDF (or an
    already-filled output from fill_fields) and a fresh output_path.
    """
    if not stamps:
        return 0
    _require_fitz()
    doc = fitz.open(pdf_path)
    written = 0
    try:
        for page in doc:
            for w in page.widgets() or []:
                name = w.field_name
                if name not in stamps:
                    continue
                png = stamps[name]
                if not png:
                    continue
                try:
                    page.insert_image(w.rect, stream=png, keep_proportion=True, overlay=True)
                    written += 1
                except Exception as e:
                    logger.warning(f"Failed to stamp signature into {name}: {e}")
        doc.save(output_path, incremental=False, deflate=True)
    finally:
        doc.close()
    return written


def stamp_annotations(
    pdf_path: str,
    output_path: str,
    annotations: list[dict],
    signature_pngs: dict[str, bytes] | None = None,
) -> int:
    """Burn freeform annotations (text, check, signature) onto a PDF.

    Each annotation has page-percentage coords (x, y, w, h: 0–100), a `kind`
    in {text, check, signature}, a string value, and a line_height for text.
    Returns the number of annotations stamped.
    """
    if not annotations:
        return 0
    _require_fitz()
    signature_pngs = signature_pngs or {}
    doc = fitz.open(pdf_path)
    written = 0
    try:
        for ann in annotations:
            try:
                page_no = int(ann.get("page") or 1)
                if page_no < 1 or page_no > doc.page_count:
                    continue
                page = doc[page_no - 1]
                pw, ph = page.rect.width, page.rect.height
                x = float(ann.get("x", 0)) / 100.0 * pw
                y = float(ann.get("y", 0)) / 100.0 * ph
                w = float(ann.get("w", 0)) / 100.0 * pw
                h = float(ann.get("h", 0)) / 100.0 * ph
                rect = fitz.Rect(x, y, x + w, y + h)
                kind = ann.get("kind", "text")
                value = ann.get("value", "")

                if kind == "text":
                    if not value:
                        continue
                    line_height = float(ann.get("line_height") or 1.3)
                    lines = value.split("\n")
                    # Fixed point size — keeps text consistent across boxes
                    # regardless of how each was resized. Per HTML metrics the
                    # baseline of a line box sits at fontsize × (lh + 0.6) / 2
                    # from the line-box top (half the leading above the glyph,
                    # half below, ascent ≈ 0.8 × fontsize).
                    fontsize = 11.0
                    # Stride between lines is tuned to match what the editor
                    # shows: the editor's textarea renders text larger than
                    # 11pt (cqh-based ≈ 1.5% of page-image height ≈ 17pt for
                    # Letter), so its rows are spaced wider than 11 × lh on
                    # the page. Multiply the export stride to compensate.
                    line_box = fontsize * line_height * 1.2
                    # First baseline at one ascent below the box top — closest
                    # match to where the editor's first line of text appears.
                    yy = y + fontsize * 0.85
                    # Match the textarea's 4px left padding (~3 PDF points).
                    xx = x + 3.0
                    for line in lines:
                        try:
                            page.insert_text(
                                (xx, yy),
                                line,
                                fontsize=fontsize,
                                color=(0, 0, 0),
                            )
                        except Exception as e:
                            logger.warning(f"insert_text failed for annotation: {e}")
                        yy += line_box
                    written += 1

                elif kind == "check":
                    # Draw a checkmark stroke that fills the box.
                    cx = x + w / 2.0
                    cy = y + h / 2.0
                    size = min(w, h) * 0.85
                    p1 = fitz.Point(cx - size * 0.40, cy + size * 0.05)
                    p2 = fitz.Point(cx - size * 0.10, cy + size * 0.30)
                    p3 = fitz.Point(cx + size * 0.45, cy - size * 0.30)
                    shape = page.new_shape()
                    shape.draw_polyline([p1, p2, p3])
                    shape.finish(
                        color=(0, 0, 0),
                        width=max(1.0, size * 0.13),
                        lineCap=1,
                        lineJoin=1,
                    )
                    shape.commit()
                    written += 1

                elif kind == "signature":
                    if not isinstance(value, str) or not value.startswith("signature:"):
                        continue
                    sid = value[len("signature:"):].strip()
                    png = signature_pngs.get(sid)
                    if not png:
                        continue
                    try:
                        page.insert_image(rect, stream=png, keep_proportion=True, overlay=True)
                        written += 1
                    except Exception as e:
                        logger.warning(f"signature stamp failed: {e}")
            except Exception as e:
                logger.warning(f"Failed to stamp annotation {ann.get('id')}: {e}")
                continue
        doc.save(output_path, incremental=False, deflate=True)
    finally:
        doc.close()
    return written


def fill_fields(source_path: str, output_path: str, values: dict[str, Any]) -> int:
    """Write values back into the AcroForm and save a new PDF.

    Returns the number of fields updated. Unknown field names are ignored.
    Layout of the source PDF is preserved.
    """
    _require_fitz()
    doc = fitz.open(source_path)
    updated = 0
    try:
        for page in doc:
            for w in page.widgets() or []:
                name = w.field_name
                if name not in values:
                    continue
                new_value = values[name]
                if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                    on_state = _widget_on_state(w)
                    if isinstance(new_value, bool):
                        # Single checkbox: bool semantics
                        w.field_value = (on_state or "Yes") if new_value else "Off"
                    else:
                        # Choice/radio group: only the widget whose on_state matches
                        # gets that on_state; the rest go Off.
                        chosen = "" if new_value is None else str(new_value).strip()
                        w.field_value = on_state if on_state and on_state == chosen else "Off"
                else:
                    w.field_value = "" if new_value is None else str(new_value)
                w.update()
                updated += 1
        doc.save(output_path, incremental=False, deflate=True)
    finally:
        doc.close()
    return updated
