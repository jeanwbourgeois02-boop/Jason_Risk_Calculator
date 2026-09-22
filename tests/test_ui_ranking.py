"""ui/tabs/ranking.py: the one way every table ranks (sortable props, typed numeric
columns with a display format, sign colours by value, and a pinned footer for totals)."""
import json

from dash import dash_table

from ui.tabs import ranking as rk


def test_amount_rate_and_percent_formats_are_d3_specifiers():
    assert rk.amount()["specifier"] == "(,.0f" and rk.amount()["nully"] == ""
    assert rk.amount(2, "n/a", trim=True) == {**rk.amount(2, "n/a", trim=True), "specifier": "(,.2~f", "nully": "n/a"}
    assert rk.rate()["specifier"] == ",.6f" and rk.rate(4, trim=True)["specifier"] == ",.4~f"
    assert rk.percent()["specifier"] == "+$.1f" and rk.percent()["locale"]["symbol"] == ["", "%"]
    assert rk.count()["specifier"] == ",.0f"


def test_numeric_and_text_columns_carry_their_type_and_format():
    assert rk.numeric("USD", "usd") == {"name": "USD", "id": "usd", "type": "numeric", "format": rk.amount()}
    assert rk.numeric("Fill", "fill", rk.rate(4), editable=True)["format"]["specifier"] == ",.4f"
    assert rk.text("Pair", "pair") == {"name": "Pair", "id": "pair", "type": "text"}


def test_value_keeps_numbers_drops_blanks_and_passes_other_text_through():
    assert rk.value(None) is None and rk.value(float("nan")) is None and rk.value("") is None
    assert rk.value(3) == 3.0 and rk.value("12.5") == 12.5
    assert rk.value("n/a") == "n/a"   # an unpriced cell's text: shown raw, ranked last


def test_sortable_props_native_multi_and_persisted_only_with_an_id():
    plain = rk.sortable()
    assert plain == {"sort_action": "native", "sort_mode": "multi", "sort_as_null": list(rk.NULL_TEXTS)}
    with_id = rk.sortable("t", persisted=("filter_query",))
    assert with_id["persistence"] is True and with_id["persistence_type"] == "session"
    assert with_id["persisted_props"] == ["filter_query", "sort_by"]


def test_sign_styles_key_on_the_number_not_the_text():
    rules = rk.sign_styles(["pnl"], bold=True, nil={"color": "grey"})
    assert rules[0] == {"if": {"column_id": "pnl", "filter_query": "{pnl} < 0"}, "color": "var(--neg)", "fontWeight": "700"}
    assert rules[1]["if"]["filter_query"] == "{pnl} > 0" and rules[1]["color"] == "var(--pos)"
    assert rules[2] == {"if": {"column_id": "pnl", "filter_query": "{pnl} is nil"}, "color": "grey"}


def test_display_length_matches_what_the_table_prints():
    assert rk.display_length(-1234567, rk.amount()) == len("(1,234,567)")
    assert rk.display_length(1234.5, rk.amount(2, trim=True)) == len("1,234.5")
    assert rk.display_length(None, rk.amount(nully="n/a")) == 3
    assert rk.display_length(-3.25, rk.percent()) == len("-3.3%") or rk.display_length(-3.25, rk.percent()) == len("-3.2%")
    assert rk.display_length("KRW (NDF)") == 9
    assert rk.display_length(1.108750, rk.rate()) == len("1.108750")


def test_column_widths_span_body_and_footer():
    cols = [rk.text("Currency", "ccy"), rk.numeric("USD delta", "usd")]
    body = [{"ccy": "KRW (NDF)", "usd": -12.0}]
    footer = [{"ccy": "Net", "usd": -123456789.0}]
    rules = rk.column_widths(cols, body, footer)
    assert rules[0] == {"if": {"column_id": "ccy"}, "width": "11ch", "minWidth": "11ch", "maxWidth": "11ch"}
    assert rules[1]["width"] == f"{len('(123,456,789)') + 2}ch"
    assert rk.column_widths(cols, body, skip=["ccy"])[0]["if"]["column_id"] == "usd"


def test_with_footer_pins_totals_in_a_headerless_twin_with_the_same_widths():
    cols = [rk.text("Position", "position"), rk.numeric("USD", "usd")]
    table = dash_table.DataTable(id="t", columns=cols, data=[{"position": "JPY", "usd": 5.0}],
                                 style_cell_conditional=[{"if": {"column_id": "position"}, "textAlign": "left"}],
                                 style_data_conditional=rk.sign_styles(["usd"]), **rk.sortable("t"))
    div = rk.with_footer(table, [{"position": "FX net", "usd": -7.0}],
                         footer_style=[{"if": {"filter_query": "{position} contains 'net'"}, "fontWeight": "700"}])
    assert div.className == "ranked-table" and div.children[0] is table
    footer = div.children[1]
    assert isinstance(footer, dash_table.DataTable) and footer.id == "t-footer"
    assert footer.columns == cols and footer.data == [{"position": "FX net", "usd": -7.0}]
    assert footer.style_header == {"display": "none"} and footer.sort_action == "none"
    widths = [r for r in footer.style_cell_conditional if "width" in r]
    assert widths and widths == [r for r in table.style_cell_conditional if "width" in r]
    assert footer.style_data_conditional[-1]["fontWeight"] == "700"
    assert json.dumps(div.to_plotly_json(), default=str)  # serialisable for Dash
