/* The column filters of every table (ui/tabs/trade_filter.py::funnel, 2026-09-29; spreadsheet-style,
   user: "the table columns - and above theres filters with the same names"): each filterable heading
   holds a <details class="book-pop" data-tf-key="<tab>:<column>"> whose summary is the funnel. The
   browser opens and closes it; this file keeps one open at a time, closes it on a click outside or
   Escape, focuses its box, lets a panel's search box narrow its tick list, and re-opens the panel the
   user had open when the table re-renders under it (a tick or a comparison re-draws the table with
   its headings). DOM only: nothing here touches Dash's state or asks the server. */
(function () {
  var wanted = null;        // the data-tf-key of the panel the user has open
  var reopening = false;

  function keyOf(d) { return d && d.getAttribute ? d.getAttribute('data-tf-key') : null; }

  function closeOthers(keep) {
    var open = document.querySelectorAll('details.book-pop[open]');
    for (var i = 0; i < open.length; i++) {
      if (open[i] !== keep) { open[i].removeAttribute('open'); }
    }
  }
  // a click anywhere outside an open panel closes it (the funnel itself toggles as usual)
  document.addEventListener('click', function (e) {
    var open = document.querySelectorAll('details.book-pop[open]');
    for (var i = 0; i < open.length; i++) {
      if (!open[i].contains(e.target)) { open[i].removeAttribute('open'); }
    }
  }, true);
  // Escape closes the open panel
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeOthers(null); }
  });
  // one panel at a time; the panel's first box takes the focus (not when it is re-opened after a re-render)
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d || !d.classList || !d.classList.contains('book-pop')) { return; }
    if (!d.open) {
      if (d.isConnected && keyOf(d) === wanted && !reopening) { wanted = null; }
      return;
    }
    wanted = keyOf(d);
    closeOthers(d);
    if (reopening) { reopening = false; return; }
    var box = d.querySelector('.book-pop-panel input[type="text"]');
    if (box) { box.focus(); }
  }, true);
  // the table re-rendered: the panel the user had open opens again; gone from the page, it is forgotten
  var observer = new MutationObserver(function () {
    if (!wanted) { return; }
    var all = document.querySelectorAll('details.book-pop[data-tf-key]');
    var found = null;
    for (var i = 0; i < all.length; i++) {
      if (keyOf(all[i]) === wanted) { found = all[i]; break; }
    }
    if (!found) { wanted = null; return; }
    if (!found.open) { reopening = true; found.setAttribute('open', ''); }
  });
  function watch() { observer.observe(document.body, {childList: true, subtree: true}); }
  if (document.body) { watch(); } else { document.addEventListener('DOMContentLoaded', watch); }
  // a tick list's search box hides the values that do not hold its text
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!t || !t.classList || !t.classList.contains('book-pop-search')) { return; }
    var q = String(t.value || '').toLowerCase();
    var panel = t.closest('.book-pop-panel');
    if (!panel) { return; }
    var list = t.nextElementSibling;
    if (!(list && list.classList && list.classList.contains('book-pop-list'))) {
      list = t.parentElement && t.parentElement.nextElementSibling;
    }
    var scope = (list && list.classList && list.classList.contains('book-pop-list')) ? list : panel;
    var items = scope.querySelectorAll('label');
    for (var i = 0; i < items.length; i++) {
      items[i].style.display = items[i].textContent.toLowerCase().indexOf(q) === -1 ? 'none' : '';
    }
  });
})();
/* The Book's legs behind each trade's caret (2026-10-01, user: the legs hidden until the trade's caret is
   clicked; a click elsewhere on the row still opens its panel). ui/tabs/book.py marks each leg row with
   data-legs-of="<trade>" and each caret with data-caret-of="<trade>"; the CSS hides the leg rows. A click
   on a caret is taken here, in the capture phase, so Dash never sees it (the row's own click, which opens
   the panel, does not fire), and the trades whose legs are open are kept for the session in one <style>
   of their own: a re-render of the table keeps them open with nothing to re-apply. DOM only. */
(function () {
  var KEY = 'book-legs-open';
  var open = {};
  try { open = JSON.parse(window.sessionStorage.getItem(KEY) || '{}') || {}; } catch (e) { open = {}; }

  function esc(name) {
    return (window.CSS && window.CSS.escape) ? window.CSS.escape(name) : String(name).split('\\').join('\\\\').split('"').join('\\"');
  }
  function apply() {
    var style = document.getElementById('book-legs-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'book-legs-style';
      (document.head || document.documentElement).appendChild(style);
    }
    var css = '';
    Object.keys(open).forEach(function (name) {
      var v = esc(name);
      css += '#book-table tr.tk-leg-row[data-legs-of="' + v + '"] { display: table-row; }\n';
      css += '#book-table .tk-legs-caret[data-caret-of="' + v + '"] { transform: rotate(90deg); }\n';
    });
    style.textContent = css;
  }
  document.addEventListener('click', function (e) {
    var caret = e.target && e.target.closest ? e.target.closest('.tk-legs-caret') : null;
    if (!caret) { return; }
    e.stopPropagation();
    e.preventDefault();
    var name = caret.getAttribute('data-caret-of') || '';
    if (open[name]) { delete open[name]; } else { open[name] = 1; }
    try { window.sessionStorage.setItem(KEY, JSON.stringify(open)); } catch (err) { /* private mode: this page only */ }
    apply();
  }, true);
  if (document.head) { apply(); } else { document.addEventListener('DOMContentLoaded', apply); }
})();
