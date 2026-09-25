"""ui/tabs/formatting.py: the shared display helpers of the screens redesign (2026-09-25):
money in k / m / bn, a title with its definitions on hover, a short marker with its sentence
on hover, and the tab's "Data issues (N)" drawer. Display only."""
import math

from dash import html

from ui.tabs import formatting as fmt

MINUS = "\u2212"


# ----------------------------------------------------------------------------- short_money
def test_short_money_three_significant_figures_in_k_m_bn():
    assert fmt.short_money(51_018) == "51.0k"
    assert fmt.short_money(1_650_590) == "1.65m"
    assert fmt.short_money(395) == "395"
    assert fmt.short_money(0) == "0"
    assert fmt.short_money(1_234.5) == "1.23k"
    assert fmt.short_money(2.4e9) == "2.40bn"
    assert fmt.short_money(1.5e12) == "1,500bn"


def test_short_money_rounds_before_choosing_the_unit():
    assert fmt.short_money(999_600) == "1.00m"      # never "1000k"
    assert fmt.short_money(999.6) == "1.00k"
    assert fmt.short_money(0.4) == "0" and fmt.short_money(-0.4) == "0"   # float noise, no negative zero
    assert fmt.short_money(1e-9) == "0"


def test_short_money_negatives_take_a_real_minus_or_parentheses_with_the_symbol_after_the_sign():
    assert fmt.short_money(-51_018, "$") == MINUS + "$51.0k"
    assert fmt.short_money(-51_018, "$", parens=True) == "($51.0k)"
    assert fmt.short_money(-0.6) == MINUS + "1"
    assert fmt.short_money(51_018, "$", parens=True) == "$51.0k"


def test_short_money_blank_for_missing_and_text_kept():
    assert fmt.short_money(None) == ""
    assert fmt.short_money(float("nan")) == ""
    assert fmt.short_money("n/a") == "n/a"
    assert fmt.short_money("1,000") == "1.00k"


def test_sig_digits_gives_mantissa_and_thousands_group():
    assert fmt.sig_digits(51_018.0) == ("51.0", 3)
    assert fmt.sig_digits(1_650_590.0) == ("1.65", 6)
    assert fmt.sig_digits(999_600.0) == ("1.00", 6)


# ----------------------------------------------------------------------------- about
def test_about_puts_the_definitions_on_hover_of_the_title_not_in_a_paragraph():
    node = fmt.about("Open spreads", "One row per spread: its legs summed from value_book.")
    assert isinstance(node, html.H4)
    assert node.title == "One row per spread: its legs summed from value_book."
    title, mark = node.children
    assert title == "Open spreads"
    assert isinstance(mark, html.Span) and mark.className == "about-mark"
    assert mark.title == node.title and mark.children == fmt.INFO_MARK
    assert "about-title" in node.className


def test_about_level_extra_props_and_plain_heading_without_text():
    node = fmt.about("Book", "defs", level="h3", id="book-title", className="tight")
    assert isinstance(node, html.H3) and node.id == "book-title"
    assert node.className == "about-title tight"
    plain = fmt.about("Book")
    assert isinstance(plain, html.H4) and plain.children == "Book"
    assert getattr(plain, "title", None) is None


# ----------------------------------------------------------------------------- marker
def test_marker_is_a_short_span_with_its_sentence_on_hover():
    m = fmt.marker("excl. 3", "3 of 12 trades unpriced: no FUTURE_PX on 2026-09-25")
    assert isinstance(m, html.Span)
    assert m.children == "excl. 3" and m.title.startswith("3 of 12") and m.className == "marker"
    assert fmt.marker("") is None and fmt.marker(None, "why") is None
    assert fmt.marker("n/a", "no spot", className="marker--warn").className == "marker marker--warn"


# ----------------------------------------------------------------------------- issues_drawer
def test_issues_drawer_counts_items_and_starts_collapsed():
    d = fmt.issues_drawer(["CLZ26: no price on 2026-09-25", ("T-12", "no USDCNH spot"), "", None])
    assert isinstance(d, html.Details) and d.className == "issues-drawer"
    assert not d.open
    summary, body = d.children
    assert isinstance(summary, html.Summary) and summary.children == "Data issues (2)"
    assert isinstance(body, html.Ul) and len(body.children) == 2
    first, second = body.children
    assert first.children == "CLZ26: no price on 2026-09-25"
    label, _, sentence = second.children
    assert label.children == "T-12" and label.className == "issue-label" and sentence == "no USDCNH spot"


def test_issues_drawer_none_when_nothing_to_show_and_its_title_and_id():
    assert fmt.issues_drawer([]) is None
    assert fmt.issues_drawer(None) is None
    assert fmt.issues_drawer(["", None, ("", "")]) is None
    d = fmt.issues_drawer(["x"], title="Not included", open=True, id="risk-issues")
    assert d.children[0].children == "Not included (1)" and d.open and d.id == "risk-issues"


def test_issues_drawer_passes_a_component_through():
    link = html.A("see Data", href="#")
    d = fmt.issues_drawer([link])
    assert d.children[1].children[0].children is link


def test_existing_format_cell_unchanged():
    assert fmt.format_cell(-1234.4) == "(1,234)"
    assert fmt.format_cell(math.nan) == ""


# ----------------------------------------------------------------------------- tab_link
# A tab's name as a link that switches tab (user, 2026-09-25); ui/app.py's one callback
# listens on every id of type TAB_LINK_TYPE.
def test_tab_link_is_a_quiet_button_with_a_pattern_matching_id():
    link = fmt.tab_link("\u2192 Curve", "curve", "book-metals")
    assert isinstance(link, html.Button)
    assert link.children == "\u2192 Curve"
    assert link.id == {"type": fmt.TAB_LINK_TYPE, "tab": "curve", "idx": "book-metals"}
    assert fmt.TAB_LINK_TYPE == "tab-link"
    assert link.className == "tab-link" and link.type == "button"
    assert link.n_clicks == 0                      # never a click on the first render
    assert link.title == "Open the Curve tab"      # the arrow is not the tab's name


def test_tab_link_title_class_and_component_label():
    link = fmt.tab_link("Risk", "risk", "curve-caption", title="Per-underlyer figures", className="x")
    assert link.title == "Per-underlyer figures" and link.className == "tab-link x"
    comp = fmt.tab_link(html.B("Data"), "market-data", "book-marks")
    assert comp.title == "Open the market-data tab"
    assert fmt.tab_link_id("ladder", 3) == {"type": "tab-link", "tab": "ladder", "idx": "3"}


def test_tab_links_differ_by_idx_and_by_tab():
    a = fmt.tab_link("Curve", "curve", "book-metals")
    b = fmt.tab_link("Curve", "curve", "book-energy")
    c = fmt.tab_link("Risk", "risk", "book-metals")
    ids = [tuple(sorted(x.id.items())) for x in (a, b, c)]
    assert len(set(ids)) == 3
