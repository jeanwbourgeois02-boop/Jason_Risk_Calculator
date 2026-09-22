"""The "Pull Bloomberg now" control in the top bar, beside the upload button, and the one
Bloomberg status line every screen shares (2026-09-18, user: "what rate is Bloomberg
polled at" / wants to order a pull at a chosen moment).

What the button does: `app.bloomberg_feed.trigger_now()` (`data.bloomberg.live.LiveFeed`),
which wakes the feed thread for one cycle. Since 2026-09-21 that is the ONLY thing that
pulls Bloomberg (user: "make it only pull the bloomberg info on request - no automatic"). The pull itself runs on the
feed thread, not inside the Dash callback, so the click returns at once with
"pull requested..." and a fast poll (`PULL_POLL_ID`, enabled ONLY while a requested pull
is outstanding, switched off again when it lands or after `PULL_TIMEOUT_SECONDS`) watches
the status file until a pull that started at or after the click has reported. When it
lands the poll publishes `ui.revision.DATA_REVISION_ID`, so every open view re-renders
from the new marks with no browser reload.

Where the status comes from: `data.bloomberg.live.read_status`, the same status file the
Market data tab reads (the feed thread, that tab's synchronous "Pull now" and the
backfill all rewrite it). Nothing here prices anything or reads a mark.

`feed_headline` moved here from `ui/tabs/market_data.py` (which now imports it) so the
top bar and the Market data tab print the same sentence. It ends by saying that Bloomberg
is pulled on request only. With no feed (`app.bloomberg_feed is None`, RISK_LIVE=0) a
click says why in plain words, never nothing.

A press on a machine with no Bloomberg still runs one cycle (user decision 2026-09-22:
"pull bbg now should recalc options too, using log data if no bbg access"): `pull_once`
re-prices the FX options from the marks on file and records it under `status["recalc"]`
and `status["recalc_summary"]`. The not-connected line then also says what the press did
(`recalc_words`), so a press never reads as if nothing happened. The no-feed message is
left alone: with no feed to wake, that press ran nothing, and a summary from an earlier
cycle would claim otherwise.

Rapid clicks: `PullGuard` is a server-side record of the one outstanding request, under
a lock, so ten clicks (or two browser tabs) make one `trigger_now()` call; the button is
also disabled in the browser while a request is outstanding.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from dash import Input, Output, State, dcc, html, no_update

from ui import revision

PULL_BUTTON_ID = "feed-pull-now"
PULL_STATUS_ID = "feed-pull-status"
PULL_PENDING_ID = "feed-pull-pending"
PULL_POLL_ID = "feed-pull-poll"
STATUS_REFRESH_ID = "feed-status-refresh"

PULL_BUTTON_LABEL = "Pull Bloomberg now"
PULL_POLL_MS = 2_000
PULL_TIMEOUT_SECONDS = 180
FALLBACK_INTERVAL_SECONDS = 900     # the feed's cadence, used only when data.bloomberg.live cannot be imported
STATUS_REFRESH_MAX_SECONDS = 60     # the passive status line re-reads the status file at least this often
NO_FEED_REASON = "no live Bloomberg feed was started in this session"
NO_FEED_WORDS = "Bloomberg pulls are switched off in this session"
ON_REQUEST_WORDS = "pulls only when you press Pull Bloomberg now"
RECALC_HEAD = "no Bloomberg on this machine: "   # how data.bloomberg.live.recalc_summary opens its sentence


# --------------------------------------------------------------------------- words
def feed_interval_seconds(feed=None) -> Optional[int]:
    """Seconds between the screens' own safety-net re-reads of the marks on file
    (`safety_refresh_ms`, `status_refresh_ms`): `data.bloomberg.live.INTERVAL_SECONDS`.
    No Bloomberg pull runs on it (2026-09-21). None when it cannot be read."""
    value = getattr(feed, "interval", None)
    if value is None:
        try:
            from data.bloomberg.live import INTERVAL_SECONDS as value
        except Exception:  # noqa: BLE001 -- a module mid-edit must not take the top bar down
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safety_refresh_ms() -> int:
    """Milliseconds for a tab's own safety-net `dcc.Interval` (Ladder, Market data): one
    feed cycle, read from the feed module, `FALLBACK_INTERVAL_SECONDS` when that cannot be
    imported. The in-place refresh on a data change comes from `ui/revision.py` within
    seconds; these timers only catch what that misses, so they follow the feed instead of
    carrying a number of their own that goes stale when the cadence changes."""
    return (feed_interval_seconds() or FALLBACK_INTERVAL_SECONDS) * 1000


def cadence_words(seconds) -> str:
    """'every 15 minutes', 'every minute', 'every 45 s', 'every 2 min 30 s'."""
    seconds = int(seconds)
    minutes, rest = divmod(seconds, 60)
    if minutes and not rest:
        return "every minute" if minutes == 1 else f"every {minutes} minutes"
    if minutes:
        return f"every {minutes} min {rest} s"
    return f"every {seconds} s"


def seconds_words(value) -> str:
    """'48 s', '3.4 s' (one decimal under ten seconds); '' when it is not a number."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if seconds != seconds or seconds < 0:
        return ""
    return f"{seconds:.1f} s" if seconds < 10 else f"{seconds:.0f} s"


def pull_timings(status: Optional[dict]) -> dict:
    """`status["timings"]` (seconds per step of the last pull, written by
    `data.bloomberg.live.pull_once`; absent on an older status file) with anything that is
    not a number dropped. {} when there is nothing usable."""
    timings = (status or {}).get("timings")
    if not isinstance(timings, dict):
        return {}
    return {str(step): float(value) for step, value in timings.items() if seconds_words(value)}


def recalc_words(status: Optional[dict], drop_head: bool = True) -> str:
    """`status["recalc_summary"]`: the one sentence `data.bloomberg.live.pull_once` writes
    when a press found no Bloomberg and re-priced the FX options from the marks on file
    instead (user decision 2026-09-22). The pull's own words, untouched, except that with
    `drop_head` the opening "no Bloomberg on this machine: " is left off, for a line that
    has already said Bloomberg is not connected. "" when the status carries none: a
    connected pull, or a status file from before the change."""
    summary = (status or {}).get("recalc_summary")
    if not isinstance(summary, str) or not summary.strip():
        return ""
    summary = summary.strip()
    if drop_head and summary.startswith(RECALC_HEAD):
        summary = summary[len(RECALC_HEAD):].strip()
    return summary


def _parse_time(text) -> Optional[datetime]:
    """An aware datetime from a status-file timestamp; None when it is not ISO. A naive
    value is taken as this machine's local time, which is what `live._now_iso` writes."""
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def short_time(text, now: Optional[datetime] = None) -> str:
    """'14:32:05' for a timestamp dated today (local), 'YYYY-MM-DD HH:MM' otherwise, and
    the raw text when it is not a timestamp at all."""
    parsed = _parse_time(text)
    if parsed is None:
        return str(text or "")
    local = parsed.astimezone()
    today = (now.astimezone() if now else datetime.now().astimezone()).date()
    return local.strftime("%H:%M:%S") if local.date() == today else local.strftime("%Y-%m-%d %H:%M")


def feed_headline(status: Optional[dict], interval_seconds: Optional[int] = None,
                  feed_running: Optional[bool] = None, now: Optional[datetime] = None,
                  say_on_request: bool = True, say_recalc: bool = True) -> str:
    """One line: connected or the stated reason it is not, time of the last pull, marks
    written / failed, how long that pull took (only when the status file carries
    `timings["total"]`), and that Bloomberg is pulled on request only. A not-connected
    status that carries `recalc_summary` (a press with no Bloomberg re-priced the options
    from the marks on file, 2026-09-22) also says so, in the pull's own words.

    `feed_running`: False = there is no feed to ask (RISK_LIVE=0, start_feed=False), said
    so. `interval_seconds` is accepted for the callers that still pass it and ignored.
    `say_on_request=False` leaves the closing "pulls only when you press ..." off: the top
    bar prints this line right beside that very button (the button's tooltip says it).
    `say_recalc=False` leaves the recalc sentence off: the Market data tab's status block
    prints it in full underneath (`market_data.recalc_block`), once."""
    if feed_running is False:
        tail = f" · {NO_FEED_WORDS}"
    else:
        tail = f" · {ON_REQUEST_WORDS}" if say_on_request else ""
    if not status:
        return f"Bloomberg: no pull recorded yet{tail}"
    when = short_time(status.get("time"), now)
    if not status.get("connected"):
        line = f"Bloomberg: not connected — {status.get('reason') or 'unknown reason'}"
        recalc = recalc_words(status) if say_recalc else ""
        if recalc:
            # What the press did instead: the options re-priced from the marks on file, in
            # the pull's own words less its "no Bloomberg on this machine" opening, which
            # this line has just said.
            line += f" · {recalc}"
        if when:
            # "as of", not "last attempt": the file also holds placeholders written at
            # startup ("no pull has run yet", "first Bloomberg pull in progress"), whose
            # timestamp is not an attempt at anything.
            line += f" · status as of {when}"
    else:
        line = (f"Bloomberg: connected · last pull {when or 'time not recorded'} · "
                f"{status.get('written', 0)} marks written, {status.get('failed', 0)} failed")
        took = seconds_words(pull_timings(status).get("total"))
        if took:
            line += f" · last pull took {took}"
    return f"{line}{tail}"


def not_connected_message(app, status: Optional[dict]) -> str:
    """What a click says when there is no feed to wake. The reason is the one recorded
    when the app tried to start the feed (`ui.app.create_app` keeps it on
    `app.bloomberg_feed_reason`), else the status file's, else a plain default."""
    reason = getattr(app, "bloomberg_feed_reason", "") or ""
    if not reason and status and not status.get("connected"):
        reason = status.get("reason") or ""
    return f"Bloomberg is not connected on this machine: {reason or NO_FEED_REASON}"


# --------------------------------------------------------------------------- one request at a time
def read_feed_status(db_path) -> Optional[dict]:
    try:
        from data.bloomberg.live import read_status
        return read_status(db_path)
    except Exception:  # noqa: BLE001 -- unreadable status is "no pull recorded", never a 500
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def status_fingerprint(status: Optional[dict]) -> str:
    """Identity of one pull report: the fields `live.pull_once` rewrites every cycle. The
    backfill's progress patches (`live.patch_status`, the "backfill" key only) leave it
    unchanged, so they are never mistaken for a pull landing."""
    if not status:
        return ""
    return repr(tuple(status.get(k) for k in ("time", "connected", "reason", "requested", "written", "failed")))


def pull_landed(status: Optional[dict], pending: Optional[dict]) -> bool:
    """True once the status file holds a report that (a) is not the one that was already
    there when the button was clicked (`pending["baseline"]`) and (b) is of a pull that
    STARTED at or after the request (`status["time"]` is the pull's start,
    `live.pull_once`).

    (a) alone would accept a pull already in flight at the click, which finishes first
    and did not see what the user wanted priced; the feed runs the requested cycle
    straight after it. (b) alone would accept the report already on file whenever its
    timestamp shares the click's second (the status file carries no fraction), e.g. the
    placeholder written at app start, and the guard would then let a second click
    through. An unreadable timestamp falls back to (a)."""
    if not status or not pending:
        return False
    if status_fingerprint(status) == pending.get("baseline"):
        return False
    started, asked = _parse_time(status.get("time")), _parse_time(pending.get("requested_at"))
    if started is not None and asked is not None:
        return started >= asked
    return True


def seconds_waited(pending: Optional[dict], now: Optional[datetime] = None) -> int:
    asked = _parse_time((pending or {}).get("requested_at"))
    if asked is None:
        return PULL_TIMEOUT_SECONDS + 1     # unreadable request: treat as expired, never spin
    return max(0, int(((now or _utcnow()) - asked).total_seconds()))


class PullGuard:
    """The one outstanding manual pull, server-side and under a lock, so rapid clicks (or
    a second browser tab) cannot call `trigger_now()` again before the first has landed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._outstanding: Optional[dict] = None

    def request(self, feed, status: Optional[dict], now: Optional[datetime] = None) -> Tuple[dict, bool]:
        """`(pending, triggered)`. `triggered` is False when an earlier request is still
        outstanding; `pending` is then that earlier request, so the caller waits on it."""
        now = now or _utcnow()
        with self._lock:
            out = self._outstanding
            if out is not None and not pull_landed(status, out) and seconds_waited(out, now) <= PULL_TIMEOUT_SECONDS:
                return dict(out), False
            feed.trigger_now()
            self._outstanding = {"requested_at": now.replace(microsecond=0).isoformat(),
                                 "baseline": status_fingerprint(status)}
            return dict(self._outstanding), True


def click_outcome(app, guard: PullGuard, status: Optional[dict],
                  now: Optional[datetime] = None) -> Tuple[str, Optional[dict]]:
    """`(status line, pending)` for one click. `pending` is None when nothing was asked
    for (no feed on this machine), which leaves the fast poll switched off."""
    feed = getattr(app, "bloomberg_feed", None)
    if feed is None:
        return not_connected_message(app, status), None
    pending, triggered = guard.request(feed, status, now)
    if triggered:
        return "Bloomberg: pull requested...", pending
    # A repeat click while one is outstanding: same waiting line the poll prints, and the
    # caller waits on the FIRST request. (Seen live: rapid clicks reach the server before
    # the first response has disabled the button, so this branch is not hypothetical.)
    return f"Bloomberg: pull requested... {seconds_waited(pending, now)} s", pending


def poll_outcome(status: Optional[dict], pending: Optional[dict], feed=None,
                 now: Optional[datetime] = None) -> Tuple[str, bool, bool]:
    """`(status line, finished, landed)` for one fast-poll tick. `finished` switches the
    poll off; `landed` is what publishes the data revision."""
    headline = feed_headline(status, feed_interval_seconds(feed), feed_running=feed is not None, now=now,
                             say_on_request=False)
    if not pending:
        return headline, True, False
    if pull_landed(status, pending):
        return headline, True, True
    waited = seconds_waited(pending, now)
    if waited > PULL_TIMEOUT_SECONDS:
        return (f"Bloomberg: the pull requested at {short_time(pending.get('requested_at'), now)} has not reported "
                f"after {PULL_TIMEOUT_SECONDS} s (feed busy or stopped). Last status: {headline}"), True, False
    return f"Bloomberg: pull requested... {waited} s", False, False


# --------------------------------------------------------------------------- layout + callbacks
def controls() -> list:
    """The button and its status line, for the top bar's right corner (`ui/uploads.py`
    places them; the bar is pinned to the top of the window, so the button is in reach
    from every tab and at any scroll position)."""
    return [
        html.Button(PULL_BUTTON_LABEL, id=PULL_BUTTON_ID, n_clicks=0, className="btn btn--feed-pull",
                    title="Pull today's marks and any missing past closes from Bloomberg, for what the "
                          "trades on file need. Nothing is pulled until this is pressed."),
        html.Div(id=PULL_STATUS_ID, role="status", className="feed-pull-status"),
    ]


def status_refresh_ms() -> int:
    """How often the passive status line re-reads the status file: never less often than
    `STATUS_REFRESH_MAX_SECONDS`. A pull that failed never touches the database (which is
    what `ui.revision` watches), so this timer is the only thing that shows it. The read
    is one small local JSON file; nothing is asked of Bloomberg."""
    seconds = feed_interval_seconds() or STATUS_REFRESH_MAX_SECONDS
    return max(1, min(seconds, STATUS_REFRESH_MAX_SECONDS)) * 1000


def plumbing() -> list:
    """The invisible parts. The fast poll starts disabled and runs only while a requested
    pull is outstanding (`PULL_POLL_MS`, independent of the feed's cadence, so the line
    under the button still moves within seconds of a click). The slow refresh is
    `status_refresh_ms`."""
    return [
        dcc.Store(id=PULL_PENDING_ID),
        dcc.Interval(id=PULL_POLL_ID, interval=PULL_POLL_MS, n_intervals=0, disabled=True),
        dcc.Interval(id=STATUS_REFRESH_ID, interval=status_refresh_ms(), n_intervals=0),
    ]


def register(app, get_db_path: Callable[[], object]) -> None:
    guard = PullGuard()

    # Every writer of the status line also writes it as the element's tooltip (`title`):
    # the bar clamps the text to three lines, and a long "not connected" reason must
    # still be readable in full.
    @app.callback(
        Output(PULL_STATUS_ID, "children", allow_duplicate=True),
        Output(PULL_STATUS_ID, "title", allow_duplicate=True),
        Output(PULL_PENDING_ID, "data"),
        Output(PULL_POLL_ID, "disabled"),
        Output(PULL_BUTTON_ID, "disabled"),
        Input(PULL_BUTTON_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _pull_clicked(n_clicks):
        if not n_clicks:
            return no_update, no_update, no_update, no_update, no_update
        line, pending = click_outcome(app, guard, read_feed_status(get_db_path()))
        waiting = pending is not None
        return line, line, pending, not waiting, waiting

    @app.callback(
        Output(PULL_STATUS_ID, "children", allow_duplicate=True),
        Output(PULL_STATUS_ID, "title", allow_duplicate=True),
        Output(PULL_PENDING_ID, "data", allow_duplicate=True),
        Output(PULL_POLL_ID, "disabled", allow_duplicate=True),
        Output(PULL_BUTTON_ID, "disabled", allow_duplicate=True),
        Output(revision.DATA_REVISION_ID, "data", allow_duplicate=True),
        Input(PULL_POLL_ID, "n_intervals"),
        State(PULL_PENDING_ID, "data"),
        prevent_initial_call=True,
    )
    def _pull_poll(_n, pending):
        db_path = get_db_path()
        line, finished, landed = poll_outcome(read_feed_status(db_path), pending,
                                              getattr(app, "bloomberg_feed", None))
        if not finished:
            return line, line, no_update, no_update, no_update, no_update
        # Landed: tell every open view the marks changed (ui/revision.py), no reload.
        return line, line, None, True, False, (revision.file_signature(db_path) if landed else no_update)

    @app.callback(
        Output(PULL_STATUS_ID, "children"),
        Output(PULL_STATUS_ID, "title"),
        Input(STATUS_REFRESH_ID, "n_intervals"),
        Input(revision.DATA_REVISION_ID, "data"),
        State(PULL_PENDING_ID, "data"),
    )
    def _status_refresh(_n, _data_rev, pending):
        # While a requested pull is outstanding the fast poll owns the line.
        if pending and seconds_waited(pending) <= PULL_TIMEOUT_SECONDS:
            return no_update, no_update
        feed = getattr(app, "bloomberg_feed", None)
        line = feed_headline(read_feed_status(get_db_path()), feed_interval_seconds(feed),
                             feed_running=feed is not None, say_on_request=False)
        return line, line
