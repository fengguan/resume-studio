"""A safe, structured document preview; Word remains the pagination authority."""

from docx.table import Table
from docx.text.paragraph import Paragraph

from .parsing import document_paragraphs, load_document


def preview_payload(workspace, state):
    doc = load_document(workspace.source)
    by_location = {b.location: b.id for b in workspace.resume.blocks}
    used = set()

    def walk(parent, prefix="body"):
        nodes = []
        for i, child in enumerate(parent.iter_inner_content()):
            path = f"{prefix}/{i}"
            if isinstance(child, Paragraph):
                key = by_location.get(path)
                if key:
                    used.add(key)
                    nodes.append(paragraph(child, key))
                elif child._p.xpath('.//w:br[@w:type="page"]'):
                    nodes.append({"type": "break"})
                else:
                    nodes.append({"type": "space"})
            elif isinstance(child, Table):
                rows, seen = [], set()
                for ri, row in enumerate(child.rows):
                    cells = []
                    for ci, cell in enumerate(row.cells):
                        if cell._tc in seen:
                            continue
                        seen.add(cell._tc)
                        cells.append(
                            {
                                "span": cell.grid_span,
                                "nodes": walk(cell, f"{path}/row{ri}/cell{ci}"),
                            }
                        )
                    if cells:
                        rows.append(cells)
                nodes.append({"type": "table", "rows": rows})
        return nodes

    def paragraph(para, key):
        style = para.style.name if para.style else ""
        fmt = para.paragraph_format
        return {
            "type": "paragraph",
            "id": key,
            "heading": style.startswith("Heading") or workspace.blocks[key].text.isupper(),
            "center": para.alignment == 1,
            "indent": min(72, max(0, fmt.left_indent.pt)) if fmt.left_indent else 0,
            "bullet": "List" in style or bool(para._p.xpath("./w:pPr/w:numPr")),
            "page_break": bool(fmt.page_break_before),
        }

    nodes = walk(doc)
    extras = []
    for location, para in document_paragraphs(doc):
        key = by_location.get(location)
        if key and key not in used:
            extras.append(paragraph(para, key))
    if extras:
        nodes.append({"type": "aside", "nodes": extras})
    return {
        "run_id": state.run_id,
        "version": state.version,
        "dirty": workspace.dirty(state),
        "nodes": nodes,
        "blocks": {
            b.id: {
                "original": b.text,
                "text": state.texts[b.id],
                "editable": b.id in workspace.editable,
                "location": b.location,
                "facts": state.supplements.get(b.id, ""),
                "risks": [
                    r.model_dump()
                    for r in workspace.result.risks
                    if r.block_id == b.id and r.status == "pending"
                ],
                "check": state.checks.get(b.id),
                "conversation": state.conversations.get(b.id, []),
                "can_undo": bool(state.undo.get(b.id)),
            }
            for b in workspace.resume.blocks
        },
    }
