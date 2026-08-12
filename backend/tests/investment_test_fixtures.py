from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape


def _xlsx(rows: list[list[str]]) -> bytes:
    sheet_rows = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row, start=1):
            n = c_index
            letters = ""
            while n:
                n, rem = divmod(n - 1, 26)
                letters = chr(65 + rem) + letters
            cells.append(f'<c r="{letters}{r_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="in" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def synthetic_investment_account_xlsx(*, robo: bool) -> bytes:
    rows = [
        ["Date", "Ticker", "Type", "Quantity", "Price per share", "Total Amount", "Currency", "FX Rate"],
        ["2026-01-10", "", "Cash top-up", "", "", "10", "EUR", "1"],
        ["2026-01-11", "TEST", "Buy - Market", "0.1", "100", "10", "EUR", "1"],
    ]
    if robo:
        rows.append(["2026-01-18", "", "Robo management fee", "", "", "-0.05", "EUR", "1"])
    return _xlsx(rows)


def synthetic_investment_pnl_xlsx() -> bytes:
    return _xlsx([
        ["Income from Sells"],
        ["Date acquired", "Date sold", "Ticker", "Security name", "ISIN", "Country", "Quantity", "Cost basis", "Gross proceeds", "Gross PnL", "Currency"],
        ["2025-01-01", "2026-01-01", "TEST", "Synthetic ETF", "XX0000000000", "XX", "1", "10", "12", "2", "EUR"],
        ["Other income & fees"],
        ["Date", "Symbol", "Security name", "ISIN", "Country", "Gross amount", "Withholding tax", "Net Amount", "Currency"],
        ["2026-02-01", "TEST", "Synthetic ETF dividend", "XX0000000000", "XX", "1", "0.1", "0.9", "EUR"],
    ])
