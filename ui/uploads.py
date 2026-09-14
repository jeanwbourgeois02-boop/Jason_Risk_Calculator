"""Report import and Excel reference preview controls."""
from io import BytesIO

import pandas as pd
from dash import Input, Output, State, dcc, html, dash_table, no_update

from data.ingest.upload import decode, suggested_date, import_report


def layout():
    return html.Div([
        html.H3('Upload a file'),
        html.P('Import trades and positions from a BNP report, or inspect an Excel calculation workbook.'),
        dcc.Upload(id='report-file', children=html.Button('Choose file or drop it here'),
                   accept='.csv,.xlsx,.xlsm,.xls', multiple=False, max_size=25 * 1024 * 1024,
                   style={'border': '1px dashed #888', 'padding': '16px'}),
        html.Div(id='report-description'),
        dcc.Dropdown(id='report-sheet', placeholder='Excel worksheet'),
        dcc.RadioItems(id='report-purpose', options=[
            {'label': 'BNP report — import trades and positions', 'value': 'bnp'},
            {'label': 'Excel calculation reference — preview only', 'value': 'reference'},
        ], value='bnp'),
        html.Label('Snapshot date (check this before importing; holidays are not inferred)'),
        dcc.DatePickerSingle(id='report-date'),
        html.Button('Import BNP report', id='report-import', n_clicks=0),
        html.Div(id='report-result', role='status'),
        html.Details([html.Summary('Preview worksheet / formulas'), html.Div(id='report-preview')]),
    ], style={'margin': '20px 0', 'maxWidth': '1100px'})


def register(app, get_db_path):
    @app.callback(Output('report-sheet', 'options'), Output('report-sheet', 'value'),
                  Output('report-date', 'date'), Output('report-description', 'children'),
                  Output('report-purpose', 'value'), Input('report-file', 'contents'),
                  State('report-file', 'filename'), prevent_initial_call=True)
    def selected(contents, filename):
        try:
            payload = decode(contents)
            sheets = []
            if not filename.lower().endswith('.csv'):
                with pd.ExcelFile(BytesIO(payload)) as book:
                    sheets = book.sheet_names
            reference = 'Portfolio' in sheets and 'All FX trades' in sheets
            return sheets, sheets[0] if sheets else None, suggested_date(filename), filename, 'reference' if reference else 'bnp'
        except Exception as exc:
            return [], None, None, f'Cannot read file: {exc}', 'bnp'

    @app.callback(Output('report-import', 'disabled'), Input('report-purpose', 'value'))
    def purpose(value):
        return value != 'bnp'

    @app.callback(Output('report-preview', 'children'), Input('report-file', 'contents'),
                  Input('report-sheet', 'value'), State('report-file', 'filename'))
    def preview(contents, sheet, filename):
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

    @app.callback(Output('report-result', 'children'), Output('cash-ladder-date', 'date'),
                  Output('pnl-date', 'date'), Input('report-import', 'n_clicks'),
                  State('report-file', 'contents'), State('report-file', 'filename'),
                  State('report-date', 'date'), State('report-sheet', 'value'),
                  State('report-purpose', 'value'), prevent_initial_call=True)
    def submit(clicks, contents, filename, as_of, sheet, purpose):
        if purpose != 'bnp':
            return 'Reference workbook only; no trades imported.', no_update, no_update
        if not as_of:
            return 'Choose the report snapshot date before importing.', no_update, no_update
        try:
            message = import_report(decode(contents), filename, as_of, get_db_path(), sheet)
            return message, as_of, as_of
        except Exception as exc:
            return f'Import failed; no report data saved. {exc}', no_update, no_update
