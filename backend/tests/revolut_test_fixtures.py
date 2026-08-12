from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

import pymupdf as fitz

HEADERS = [
    "Type", "Product", "Started Date", "Completed Date", "Description",
    "Amount", "Fee", "Currency", "State", "Balance",
]


def _synthetic_revolut_xlsx() -> bytes:
    rows = [
        HEADERS,
        ["Card Payment", "Current", "2026-01-01", "2026-01-01", "Q8", "-10.00", "0", "EUR", "COMPLETED", "30.00"],
        ["Topup", "Current", "2026-01-02", "2026-01-02", "Top-up by *9999", "100.00", "0", "EUR", "COMPLETED", "130.00"],
        ["Transfer", "Current", "2026-01-02", "2026-01-02", "To Robo portfolio", "-0.50", "0", "EUR", "COMPLETED", "129.50"],
        ["Charge", "Current", "2026-01-03", "2026-01-03", "Metal plan fee", "0", "9.99", "EUR", "COMPLETED", "119.51"],
        ["Charge Refund", "Current", "2026-01-04", "2026-01-04", "Plan termination refund", "0", "-9.99", "EUR", "COMPLETED", "129.50"],
        ["Card Payment", "Current", "2026-01-05", "", "Ignored merchant", "-2.00", "0", "EUR", "REVERTED", ""],
    ]
    sheet_rows = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row, start=1):
            n = c_index
            letters = ""
            while n:
                n, rem = divmod(n - 1, 26)
                letters = chr(65 + rem) + letters
            cells.append(
                f'<c r="{letters}{r_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
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


def _synthetic_revolut_pdf() -> bytes:
    content = """EUR Statement
Generated on the Jan 5, 2026
Revolut Bank UAB (Belgian Branch)
FAKE OWNER
Balance summary
Product
Opening balance
Money out
Money in
Closing
balance
Account (Current Account)
EUR 40.00
EUR 20.49
EUR 109.99
EUR 129.50
Account (Current Account)
Junior Tester
EUR 0.00
EUR 5.00
EUR 10.00
EUR 5.00
Total
EUR 40.00
EUR 25.49
EUR 119.99
EUR 134.50
Account transactions from January 1, 2026 to January 5, 2026
Value date
Date of
reception
Description
Money out
Money in
Balance
Jan 1, 2026
Jan 1, 2026
Q8
EUR 10.00
EUR 30.00
Transaction Id: 11111111-1111-1111-1111-111111111111
To: Q8 Test, Test
Card: 400000******0001
Jan 2, 2026
Jan 2, 2026
Top-up by *9999
EUR 100.00
EUR 130.00
Transaction Id: 22222222-2222-2222-2222-222222222222
From: *9999
Jan 2, 2026
Jan 2, 2026
To Robo portfolio
EUR 0.50
EUR 129.50
Transaction Id: 33333333-3333-3333-3333-333333333333
Jan 3, 2026
Jan 3, 2026
Metal plan fee
EUR 9.99
EUR 119.51
Transaction Id: 44444444-4444-4444-4444-444444444444
Jan 4, 2026
Jan 4, 2026
Plan termination refund
EUR 9.99
EUR 129.50
Transaction Id: 55555555-5555-5555-5555-555555555555
Reverted from January 1, 2026 to January 5, 2026
Jan 5, 2026
Ignored merchant
EUR 2.00
Transaction Id: 66666666-6666-6666-6666-666666666666
Junior Tester's account transactions from January 1, 2026 to January 5, 2026
Value date
Date of
reception
Description
Money out
Money in
Balance
Jan 1, 2026
Jan 1, 2026
Transfer from FAKE OWNER
EUR 10.00
EUR 10.00
Transaction Id: 77777777-7777-7777-7777-777777777777
From: FAKE OWNER
Jan 2, 2026
Jan 2, 2026
Cinema
EUR 5.00
EUR 5.00
Transaction Id: 88888888-8888-8888-8888-888888888888
To: Cinema Test, Test
Card: 400000******0002
IBAN
BE00123456789012
BIC
REVOBEB2
"""
    document = fitz.open()
    page = document.new_page(width=595, height=5000)
    page.insert_text((30, 30), content, fontsize=7)
    data = document.tobytes()
    document.close()
    return data

