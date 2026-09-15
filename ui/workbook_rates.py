"""Rate inputs used by the Excel-compatible P&L and cash-ladder detail."""
import sqlite3
import uuid
from dash import Input, Output, State, dcc, html, dash_table, no_update

from data.ingest.workbook_rates import rate_grid, save_rates, read_cached_rates, workday
from data.ingest.upload import decode


def layout(default_date):
    return html.Details(className='details rates-panel', children=[
        html.Summary('Workbook FX rates (inputs for the workbook mark-to-market)'),
        html.P('Type the outrights the workbook uses: current, T−1 and T−2, all for its shared valuation date. '
               'Blank cells stay blank; nothing is substituted.'),
        dcc.DatePickerSingle(id='rates-date', date=default_date),
        html.Div(id='rates-date-description'),
        dcc.Upload(id='rates-workbook', children=html.Button('Read saved rates from HA-portfolio Excel'), accept='.xlsx,.xlsm', multiple=False),
        html.Div(id='rates-workbook-status'),
        dash_table.DataTable(id='rates-grid', columns=[
            {'name': 'Pair', 'id': 'instrument_id', 'editable': False},
            {'name': 'General spot FX (optional)', 'id': 'spot', 'type': 'numeric'},
            {'name': 'Current outright', 'id': 'current', 'type': 'numeric'},
            {'name': 'T−1 outright', 'id': 'previous', 'type': 'numeric'},
            {'name': 'T−2 outright', 'id': 'previous2', 'type': 'numeric'},
        ], data=[], editable=True, style_cell={'textAlign': 'right'}, page_size=20),
        html.Button('Save workbook rates', id='rates-save', className='btn'),
        html.Div(id='rates-status', role='status'),
        dcc.Store(id='rates-revision'),
        dcc.Store(id='rates-cache'),
    ], style={'margin': '0 20px 30px', 'maxWidth': '1100px'})


def register(app, get_db_path):
    @app.callback(Output('rates-cache', 'data'), Output('rates-workbook-status', 'children'),
                  Output('rates-date', 'date'), Input('rates-workbook', 'contents'), prevent_initial_call=True)
    def workbook(contents):
        try:
            as_of, rows, missing = read_cached_rates(decode(contents))
            valid = sum(row[c] is not None for row in rows for c in ['current', 'previous', 'previous2'])
            if not valid:
                return no_update, (f'Workbook dated {as_of}: no usable saved FX rates. '
                    f'{missing} cells are missing or contain Excel/Bloomberg errors. Existing rates were kept.'), no_update
            return {'as_of': as_of, 'rows': rows}, f'Loaded {valid} saved rate cells for review; {missing} missing. Click Save to apply.', as_of
        except Exception as exc:
            return no_update, f'Cannot read workbook rates: {exc}', no_update

    @app.callback(Output('rates-grid', 'data'), Output('rates-date-description', 'children'),
                  Input('rates-date', 'date'), Input('rates-cache', 'data'))
    def grid(as_of, cached):
        if not as_of:
            return [], 'Choose a valuation observation date.'
        try:
            from ui.app import connect_readonly
            with connect_readonly(get_db_path()) as conn:
                rows = rate_grid(conn, as_of)
            if cached and cached['as_of'] == as_of:
                mapping = {r['instrument_id']: r for r in cached['rows']}
                rows = [{**r, **mapping.get(r['instrument_id'], {})} for r in rows]
            return rows, f'Shared valuation date: {workday(as_of, 5)}. T−1: {workday(as_of, -1)}; T−2: {workday(as_of, -2)}.'
        except (ValueError, sqlite3.Error) as exc:
            return [], f'Rates unavailable: {exc}'

    @app.callback(Output('rates-status', 'children'), Output('rates-revision', 'data'),
                  Input('rates-save', 'n_clicks'), State('rates-date', 'date'),
                  State('rates-grid', 'data'), prevent_initial_call=True)
    def save(clicks, as_of, rows):
        try:
            if not as_of or not rows:
                raise ValueError('Choose a date and load BNP trades first.')
            count = save_rates(get_db_path(), as_of, rows)
            return f'Saved {count} rates for {as_of}. Select this date and Workbook rates in the cash ladder or P&L view.', str(uuid.uuid4())
        except Exception as exc:
            return f'Rates not saved: {exc}', no_update
