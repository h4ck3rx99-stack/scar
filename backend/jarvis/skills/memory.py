"""Long-term memory: a local SQLite database with full-text search (FTS5, BM25 ranking).

  events    : conversations, things seen on screen, notes  -> "what was that error yesterday?"
  activity  : which window was in front and for how long    -> "what did I work on this morning?"

Stored in %APPDATA%\\jarvis\\memory.db. Nothing leaves the PC.
"""
import datetime
import re
import sqlite3
import threading
import time

from jarvis import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, title TEXT, text TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(title, text, content='events', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, app TEXT, title TEXT, start REAL, end REAL);
CREATE INDEX IF NOT EXISTS activity_start ON activity(start);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


def day_range(when: str | None):
    """'today', 'yesterday', 'this morning', 'last week', 'monday', '2 days ago' -> (start_ts, end_ts) or (None, None)."""
    if not when:
        return None, None
    w = when.lower().strip()
    now = datetime.datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    one = datetime.timedelta(days=1)
    if "yesterday" in w:
        return (today - one).timestamp(), today.timestamp()
    if "this morning" in w:
        return today.timestamp(), today.replace(hour=12).timestamp()
    if "this afternoon" in w:
        return today.replace(hour=12).timestamp(), today.replace(hour=17).timestamp()
    if "tonight" in w or "this evening" in w:
        return today.replace(hour=17).timestamp(), (today + one).timestamp()
    if "today" in w:
        return today.timestamp(), (today + one).timestamp()
    if "last week" in w or "past week" in w or "this week" in w:
        return (today - 7 * one).timestamp(), (today + one).timestamp()
    m = re.search(r"(\d+|two|three|four|five) days? ago", w)
    if m:
        n = {"two": 2, "three": 3, "four": 4, "five": 5}.get(m.group(1)) or int(m.group(1))
        d = today - n * one
        return d.timestamp(), (d + one).timestamp()
    for i, name in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
        if name in w:
            delta = (today.weekday() - i) % 7 or 7
            d = today - delta * one
            return d.timestamp(), (d + one).timestamp()
    return None, None


def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"[\w']+", q.lower()) if len(w) > 2 and w not in _STOP]
    return " OR ".join(f'"{w}"' for w in words[:12])


_STOP = {"the", "and", "what", "was", "that", "this", "did", "about", "with", "from", "have", "had", "you", "for", "when",
         "where", "which", "who", "how", "are", "yesterday", "today", "last", "week", "morning", "remember", "tell", "said",
         "were", "there", "their", "they", "them", "then", "than"}


class MemoryDB:
    def __init__(self, path=None):
        self.path = path or (config.APPDATA_DIR / "memory.db")
        config.APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._current = None   # (activity id, app, title)

    # --------------------------------------------------------------- events
    def add(self, kind: str, title: str, text: str, ts: float | None = None):
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self.db.execute("INSERT INTO events(ts, kind, title, text) VALUES (?,?,?,?)", (ts or time.time(), kind, title or "", text[:8000]))
            self.db.commit()

    def search(self, query: str, when: str | None = None, kind: str | None = None, limit: int = 6):
        start, end = day_range(when)
        fts = _fts_query(query)
        sql, args = [], []
        if fts:
            sql.append("SELECT e.ts, e.kind, e.title, snippet(events_fts, 1, '', '', ' ... ', 40) FROM events_fts "
                       "JOIN events e ON e.id = events_fts.rowid WHERE events_fts MATCH ?")
            args.append(fts)
        else:
            sql.append("SELECT e.ts, e.kind, e.title, substr(e.text, 1, 300) FROM events e WHERE 1=1")
        if start:
            sql.append("AND e.ts BETWEEN ? AND ?")
            args += [start, end]
        if kind:
            sql.append("AND e.kind = ?")
            args.append(kind)
        sql.append("ORDER BY bm25(events_fts) LIMIT ?" if fts else "ORDER BY e.ts DESC LIMIT ?")
        args.append(limit)
        with self._lock:
            rows = self.db.execute(" ".join(sql), args).fetchall()
        return [{"when": datetime.datetime.fromtimestamp(ts).strftime("%a %d %b %I:%M %p"), "kind": k, "title": t, "text": s}
                for ts, k, t, s in rows]

    # ------------------------------------------------------------- activity
    def track(self, app: str, title: str):
        """Called every few seconds with the foreground window; extends the current stretch or starts a new one."""
        now = time.time()
        with self._lock:
            if self._current and self._current[1:] == (app, title):
                self.db.execute("UPDATE activity SET end=? WHERE id=?", (now, self._current[0]))
            else:
                cur = self.db.execute("INSERT INTO activity(app, title, start, end) VALUES (?,?,?,?)", (app, title[:200], now, now))
                self._current = (cur.lastrowid, app, title)
            self.db.commit()

    def activity_summary(self, when: str | None = "today", top: int = 8):
        start, end = day_range(when or "today")
        if start is None:
            start, end = day_range("today")
        with self._lock:
            by_app = self.db.execute(
                "SELECT app, SUM(end-start) s FROM activity WHERE start BETWEEN ? AND ? GROUP BY app ORDER BY s DESC LIMIT ?",
                (start, end, top)).fetchall()
            by_title = self.db.execute(
                "SELECT app, title, SUM(end-start) s FROM activity WHERE start BETWEEN ? AND ? GROUP BY app, title ORDER BY s DESC LIMIT ?",
                (start, end, top)).fetchall()
        fmt = lambda s: f"{int(s // 3600)} h {int(s % 3600 // 60)} min" if s >= 3600 else f"{max(1, int(s // 60))} min"  # noqa: E731
        apps = "; ".join(f"{a}: {fmt(s)}" for a, s in by_app if s > 30)
        titles = "; ".join(f"{t} ({a}, {fmt(s)})" for a, t, s in by_title if s > 60)
        return f"Time per app: {apps or 'nothing recorded'}. Main windows: {titles or 'none'}."

    def recent_screens(self, limit=3):
        with self._lock:
            return self.db.execute("SELECT ts, title, substr(text,1,400) FROM events WHERE kind='screen' ORDER BY ts DESC LIMIT ?",
                                   (limit,)).fetchall()
