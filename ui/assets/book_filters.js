/* The Book's column filters (ui/tabs/book.py::grid_head, 2026-09-29): each filterable head holds a
   <details class="book-pop"> whose summary is the funnel. The browser opens and closes it; this file
   keeps one open at a time, closes it on a click outside, focuses its box, and lets a panel's search
   box narrow its checklist. DOM only: nothing here touches Dash's state or asks the server. */
(function () {
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
  // one panel at a time; the panel's first box takes the focus
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d || !d.classList || !d.classList.contains('book-pop') || !d.open) { return; }
    closeOthers(d);
    var box = d.querySelector('.book-pop-panel input[type="text"]');
    if (box) { box.focus(); }
  }, true);
  // a checklist's search box hides the values that do not hold its text
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
