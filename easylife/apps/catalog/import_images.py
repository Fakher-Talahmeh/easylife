"""Pictures embedded in the import workbook itself.

Two ways of putting a picture "in the column" exist in Excel and both are read:
  * a floating picture (Insert > Picture, or paste) whose top-left corner sits on a cell - read through openpyxl;
  * a picture placed IN a cell ("Place in Cell" / IMAGE in Excel 365) - stored as a "rich value", read from the package XML.
"""

import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

MAX_XML_BYTES = 20 * 1024 * 1024  # one XML part of the package; larger is refused, not parsed

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_VM_CELL = re.compile(rb'<c\b[^>]*?\bvm="(\d+)"[^>]*>', re.S)
_CELL_REF = re.compile(rb'\br="([A-Z]+\d+)"')


@dataclass
class Embedded:
    row: int  # Excel row number (1-based)
    col: int  # 0-based column index
    data: bytes
    order: int  # stable order within the workbook


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _xml(z, name):
    """Parse one part of the package; None when it does not exist. Oversized parts are refused (not parsed)."""
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise ValueError(f"{name} is too large")
    return ET.fromstring(z.read(info))


def _rels(z, part):
    """{rId: absolute path inside the package} for the relationships of `part` (e.g. 'xl/workbook.xml')."""
    folder, _, base = part.rpartition("/")
    root = _xml(z, f"{folder}/_rels/{base}.rels" if folder else f"_rels/{base}.rels")
    out = {}
    if root is None:
        return out
    for rel in root:
        target = rel.get("Target", "")
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            parts = (folder.split("/") if folder else []) + target.split("/")
            stack = []
            for p in parts:
                if p == "..":
                    if stack:
                        stack.pop()
                elif p and p != ".":
                    stack.append(p)
            path = "/".join(stack)
        out[rel.get("Id")] = path
    return out


def floating_pictures(ws):
    """Pictures openpyxl found in the sheet, as Embedded rows; (pictures, problems) - problems are pictures without a cell."""
    found, unplaced = [], 0
    for n, img in enumerate(getattr(ws, "_images", [])):
        anchor = getattr(img, "anchor", None)
        start = getattr(anchor, "_from", None)
        if start is None:  # an absolute position: it belongs to no cell
            unplaced += 1
            continue
        try:
            data = img._data()
        except Exception:
            unplaced += 1
            continue
        found.append(Embedded(row=start.row + 1, col=start.col, data=data, order=n))
    return found, unplaced


def in_cell_pictures(xlsx_bytes, sheet_title):
    """Pictures placed inside cells of the sheet (Excel 365 "Place in Cell"). Returns [] when the file has none."""
    import io

    out = []
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
        names = set(z.namelist())
        if "xl/metadata.xml" not in names or "xl/richData/rdrichvalue.xml" not in names:
            return out

        # sheet title -> sheet part
        wb = _xml(z, "xl/workbook.xml")
        wb_rels = _rels(z, "xl/workbook.xml")
        sheet_part = None
        for el in wb.iter():
            if _local(el.tag) == "sheet" and el.get("name") == sheet_title:
                sheet_part = wb_rels.get(el.get(f"{{{_NS_REL}}}id"))
        if sheet_part is None or sheet_part not in names:
            return out

        # rich value n -> media part
        rel_root = _xml(z, "xl/richData/richValueRel.xml")
        rel_targets = _rels(z, "xl/richData/richValueRel.xml")
        rel_index = [rel_targets.get(el.get(f"{{{_NS_REL}}}id")) for el in rel_root if _local(el.tag) == "rel"] if rel_root is not None else []

        structures = []
        struct_root = _xml(z, "xl/richData/rdrichvaluestructure.xml")
        if struct_root is not None:
            for s in struct_root:
                structures.append([k.get("n") for k in s if _local(k.tag) == "k"])
        rich_media = []
        for rv in _xml(z, "xl/richData/rdrichvalue.xml"):
            keys = structures[int(rv.get("s", 0))] if int(rv.get("s", 0)) < len(structures) else []
            values = [v.text for v in rv if _local(v.tag) == "v"]
            try:
                at = keys.index("_rvRel:LocalImageIdentifier")
                rich_media.append(rel_index[int(values[at])])
            except (ValueError, IndexError, TypeError):
                rich_media.append(None)

        # vm (1-based value-metadata index) -> rich value n
        meta = _xml(z, "xl/metadata.xml")
        future, value_meta = [], []
        for el in meta:
            if _local(el.tag) == "futureMetadata" and el.get("name") == "XLRICHVALUE":
                for bk in el:
                    rvb = next((x for x in bk.iter() if _local(x.tag) == "rvb"), None)
                    future.append(int(rvb.get("i")) if rvb is not None else None)
            elif _local(el.tag) == "valueMetadata":
                for bk in el:
                    rc = next((x for x in bk.iter() if _local(x.tag) == "rc"), None)
                    value_meta.append(int(rc.get("v")) if rc is not None else None)

        def media_for(vm):
            try:
                rich = future[value_meta[vm - 1]]
                return rich_media[rich]
            except (IndexError, TypeError):
                return None

        info = z.getinfo(sheet_part)
        if info.file_size > MAX_XML_BYTES * 5:
            raise ValueError("sheet is too large")
        sheet_xml = z.read(info)
        for n, m in enumerate(_VM_CELL.finditer(sheet_xml)):
            ref = _CELL_REF.search(m.group(0))
            media = media_for(int(m.group(1)))
            if ref is None or media is None or media not in names:
                continue
            letters, row = coordinate_from_string(ref.group(1).decode())
            out.append(Embedded(row=row, col=column_index_from_string(letters) - 1, data=z.read(media), order=100000 + n))
    return out
