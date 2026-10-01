/* The code-change reload with no Dash callback (2026-10-01). A browser tab left open across a restart
   keeps the old page; its Dash poll may not even be answered by the new build (ui/revision.py). So every
   20 s, and whenever the tab comes back into view, this asks the server who it is
   (/_risk_monitor_identity, ui/launch.py: "jason-risk-monitor:<fingerprint>") and compares the answer
   with the build the page was served with (<meta name="risk-monitor-build">, ui/app.py). A known
   difference reloads the page once. A failed fetch, an error status or an answer that is not this app's
   identity changes nothing: a server that is down, restarting or launched without the route (a test
   app) is not a new build. Never a loop: the identity a reload was made for is kept for the session, and
   a page that comes back still behind that same identity does not reload again. */
(function () {
  var ROUTE = '/_risk_monitor_identity';
  var PREFIX = 'jason-risk-monitor:';
  var EVERY_MS = 20000;
  var KEY = 'risk-monitor-reloaded-for';
  var busy = false;
  var done = false;

  function pageBuild() {
    var meta = document.querySelector('meta[name="risk-monitor-build"]');
    return meta ? String(meta.getAttribute('content') || '') : '';
  }
  function check() {
    var mine = pageBuild();
    if (busy || done || !mine || !window.fetch) { return; }
    busy = true;
    window.fetch(ROUTE, {cache: 'no-store', credentials: 'same-origin'}).then(function (r) {
      return r.ok ? r.text() : '';
    }).then(function (seen) {
      busy = false;
      seen = String(seen || '').trim();
      if (!seen || seen.indexOf(PREFIX) !== 0 || seen === mine) { return; }
      var already = '';
      try { already = window.sessionStorage.getItem(KEY) || ''; } catch (e) { already = ''; }
      if (already === seen) { done = true; return; }   // reloaded for this build once already: stop
      try { window.sessionStorage.setItem(KEY, seen); } catch (e) { /* private mode: reload anyway */ }
      done = true;
      window.location.reload();
    }).catch(function () { busy = false; });       // server down or restarting: not a new build
  }
  window.setInterval(check, EVERY_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') { check(); }
  });
})();
