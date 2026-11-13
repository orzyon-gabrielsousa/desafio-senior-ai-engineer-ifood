"""Leitor mínimo de planilhas .xlsx usando apenas a biblioteca padrão.

Um .xlsx é um zip com XML (OOXML). Para a planilha da Dona Maria basta ler
``xl/workbook.xml`` (nomes das abas), ``xl/_rels/workbook.xml.rels`` (arquivo
de cada aba), ``xl/sharedStrings.xml`` (textos compartilhados) e
``xl/worksheets/sheetN.xml`` (células).

Por que não usar openpyxl: a imagem oficial do Hermes Agent só valida o campo
``python_dependencies`` do plugin, nunca instala nada. Este plugin precisa rodar
na imagem oficial sem customização, então fica 100% em stdlib.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


def _col_index(ref: str) -> int:
    """``"C7"`` -> 2 (coluna C, base zero)."""
    match = _CELL_REF.match(ref)
    if not match:
        raise ValueError(f"Referência de célula inválida: {ref!r}")
    n = 0
    for ch in match.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out: list[str] = []
    for si in root.iter(f"{_NS_MAIN}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{_NS_MAIN}t")))
    return out


def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_NS_MAIN}t"))
    v = cell.find(f"{_NS_MAIN}v")
    if v is None or v.text is None:
        return None
    raw = v.text
    if kind == "s":
        return shared[int(raw)]
    if kind == "b":
        return raw == "1"
    if kind in ("str", "e"):
        return raw
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number


def _sheet_rows(root: ET.Element, shared: list[str]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for row in root.iter(f"{_NS_MAIN}row"):
        values: dict[int, Any] = {}
        next_col = 0
        for cell in row.findall(f"{_NS_MAIN}c"):
            ref = cell.get("r")
            col = _col_index(ref) if ref else next_col
            next_col = col + 1
            values[col] = _cell_value(cell, shared)
        if not values:
            rows.append([])
            continue
        width = max(values) + 1
        rows.append([values.get(i) for i in range(width)])
    return rows


def read_workbook(path: str | Path) -> dict[str, list[list[Any]]]:
    """Devolve ``{nome_da_aba: [[célula, ...], ...]}`` para todas as abas."""
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.iter(f"{_NS_PKG}Relationship")}
        sheets: dict[str, list[list[Any]]] = {}
        for sheet in workbook.iter(f"{_NS_MAIN}sheet"):
            target = (targets[sheet.get(f"{_NS_REL}id")] or "").lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            sheets[sheet.get("name") or f"sheet{len(sheets) + 1}"] = _sheet_rows(
                ET.fromstring(zf.read(target)), shared
            )
        return sheets


def read_table(path: str | Path, sheet: str) -> list[dict[str, Any]]:
    """Lê uma aba como tabela: primeira linha vira cabeçalho, linhas vazias são ignoradas."""
    rows = read_workbook(path)[sheet]
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    table: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not any(v not in (None, "") for v in row):
            continue
        table.append({header[i]: (row[i] if i < len(row) else None) for i in range(len(header)) if header[i]})
    return table
