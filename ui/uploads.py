"""Data-source strip: which BNP snapshot is loaded, plus the upload / import controls.

Sits above the tabs. One visible upload button, one Import button. The file's purpose
(BNP report vs HA-portfolio reference workbook) is detected from its contents, not
chosen from a radio. The worksheet picker appears only for Excel files. The raw
preview stays in a collapsed section.
"""
from io import BytesIO

import pandas as pd
from dash import Input, Output, State, dcc, html, dash_table, no_update

from data.ingest.upload import decode, suggested_date, import_report

SOURCE_LINE_ID = 'data-source-line'


def sheet_names(payload: bytes, filename: str) -> list:
    """Worksheet names. For xlsx/xlsm read the zip index directly (milliseconds) instead
    of parsing the whole workbook; other Excel formats fall back to pandas."""
    name = (filename or '').lower()
    if name.endswith('.csv'):
        return []
    if name.endswith(('.xlsx', '.xlsm')):
        import re
        import zipfile
        with zipfile.ZipFile(BytesIO(payload)) as z:
            xml = z.read('xl/workbook.xml').decode('utf-8', 'replace')
        names = re.findall(r'<sheet\s[^>]*?\bname="([^"]*)"', xml)
        import html as _html
        return [_html.unescape(n) for n in names]
    with pd.ExcelFile(BytesIO(payload)) as book:
        return book.sheet_names


def describe_source(data: dict) -> str:
    """One line naming the loaded BNP snapshot, from ui.app.summary()."""
    if data.get('as_of_date') in (None, 'none'):
        return 'No BNP report loaded yet. Upload one to start.'
    return (f"Loaded: BNP report as of {data['as_of_date']} "
            f"({data['trades']} trades, {data['positions']} positions). "
            f"All tabs use this snapshot unless you change the As-of date.")


def layout(data: dict = None):
    data = data or {}
    return html.Div(className='source-strip', children=[
        html.Div(className='source-row', children=[
            html.Div(id=SOURCE_LINE_ID, className='source-line', children=describe_source(data)),
            dcc.Upload(id='report-file', className='source-upload',
                       children=html.Button('Upload BNP report', className='btn btn--big'),
                       accept='.csv,.xlsx,.xlsm,.xls', multiple=False, max_size=25 * 1024 * 1024),
        ]),
        html.Div(id='report-busy', className='source-busy'),
        dcc.Loading(type='dot', color='#1f5fbf', children=html.Div(id='report-stage', className='source-row source-row--stage', hidden=True, children=[
            html.Span(id='report-description', className='source-file'),
            html.Div(id='report-sheet-wrap', hidden=True, children=[
                html.Label('Worksheet'),
                dcc.Dropdown(id='report-sheet', placeholder='Excel worksheet', clearable=False,
                             style={'width': '220px'}),
            ]),
            html.Div([
                html.Label('Snapshot date (T-1 of the file name; correct it after a holiday)'),
                dcc.DatePickerSingle(id='report-date'),
            ]),
            html.Button('Import', id='report-import', n_clicks=0, className='btn btn--big'),
            dcc.Store(id='report-purpose', data='bnp'),
        ])),
        dcc.Loading(type='dot', color='#1f5fbf', children=html.Div(id='report-result', role='status', className='source-result')),
        html.Details(id='report-preview-details', className='details details--compact', children=[
            html.Summary('Preview file (loads when opened)'),
            dcc.Loading(type='dot', color='#1f5fbf', children=html.Div(id='report-preview')),
        ]),
    ])


def register(app, get_db_path):
    # Runs in the browser the instant a file is picked, before the upload round-trip.
    app.clientside_callback(
        "function(contents, filename){ if(!contents){return '';} "
        "var mb = Math.round(contents.length * 0.75 / 1048576 * 10) / 10; "
        "return 'Reading ' + filename + ' (' + mb + ' MB) ...'; }",
        Output('report-busy', 'children'), Input('report-file', 'contents'), State('report-file', 'filename'))

    @app.callback(Output('report-sheet', 'options'), Output('report-sheet', 'value'),
                  Output('report-sheet-wrap', 'hidden'),
                  Output('report-date', 'date'), Output('report-description', 'children'),
                  Output('report-purpose', 'data'), Output('report-stage', 'hidden'),
                  Output('report-result', 'children', allow_duplicate=True),
                  Output('report-busy', 'children', allow_duplicate=True),
                  Input('report-file', 'contents'),
                  State('report-file', 'filename'), prevent_initial_call=True)
    def selected(contents, filename):
        try:
            payload = decode(contents)
            sheets = sheet_names(payload, filename)
            reference = 'Portfolio' in sheets and 'All FX trades' in sheets
            date = suggested_date(filename)
            if reference:
                note = 'This is the HA-portfolio workbook. It is a formula reference only: nothing is imported. Open "Preview file" to inspect it.'
            elif date:
                note = f'Ready. Check the snapshot date ({date}) then press Import.'
            else:
                note = 'File name does not look like HA_PNL_YYYYMMDD. Set the snapshot date, then press Import.'
            return (sheets, sheets[0] if sheets else None, not sheets, date, filename,
                    'reference' if reference else 'bnp', False, note, '')
        except Exception as exc:
            return [], None, True, None, '', 'bnp', True, f'Cannot read file: {exc}', ''

    @app.callback(Output('report-import', 'disabled'), Input('report-purpose', 'data'))
    def purpose(value):
        return value != 'bnp'

    @app.callback(Output('report-preview', 'children'), Input('report-preview-details', 'open'),
                  Input('report-sheet', 'value'), State('report-file', 'contents'),
                  State('report-file', 'filename'), prevent_initial_call=True)
    def preview(is_open, sheet, contents, filename):
        if not is_open:
            return no_update
        if not contents:
            return 'Choose a file to preview it.'
        try:
            payload = decode(contents)
            if filename.lower().endswith('.csv'):
                frame = pd.read_csv(BytesIO(payload), nrows=50).fillna('')
            elif not sheet:
                return 'Select a worksheet.'
            elif filename.lower().endswith(('.xlsx', '.xlsm')):
                import openpyxl
                book = openpyxl.load_workbook(BytesIO(payload), read_only=True, data_only=False)
                try:
                    rows = list(book[sheet].iter_rows(max_row=50, max_col=40, values_only=True))
                    frame = pd.DataFrame(rows).fillna('')
                    frame.columns = [openpyxl.utils.get_column_letter(i + 1) for i in range(len(frame.columns))]
                finally:
                    book.close()
            else:
                frame = pd.read_excel(BytesIO(payload), sheet_name=sheet, nrows=50).fillna('')
            frame.columns = [str(c) for c in frame.columns]
            frame = frame.astype(str)
            return html.Div([
                html.P('First 50 rows. XLSX/XLSM formulas are shown as written; they are not recalculated. Reference preview does not change app data.'),
                dash_table.DataTable(data=frame.to_dict('records'),
                    columns=[{'name': c, 'id': c} for c in frame.columns], page_size=10,
                    style_table={'overflowX': 'auto'},
                    style_cell={'textAlign': 'left', 'maxWidth': '360px', 'overflow': 'hidden', 'textOverflow': 'ellipsis'}),
            ])
        except Exception as exc:
            return f'Cannot preview file: {exc}'

    @app.callback(Output('report-result', 'children'), Output(SOURCE_LINE_ID, 'children'),
                  Output('cash-ladder-date', 'date'), Output('pnl-date', 'date'),
                  Input('report-import', 'n_clicks'),
                  State('report-file', 'contents'), State('report-file', 'filename'),
                  State('report-date', 'date'), State('report-sheet', 'value'),
                  State('report-purpose', 'data'), prevent_initial_call=True)
    def submit(clicks, contents, filename, as_of, sheet, purpose):
        if purpose != 'bnp':
            return 'Reference workbook only; no trades imported.', no_update, no_update, no_update
        if not as_of:
            return 'Choose the report snapshot date before importing.', no_update, no_update, no_update
        try:
            message = import_report(decode(contents), filename, as_of, get_db_path(), sheet)
        except Exception as exc:
            return f'Import failed; no report data saved. {exc}', no_update, no_update, no_update
        from ui.app import load_summary
        return message, describe_source(load_summary(get_db_path())), as_of, as_of
