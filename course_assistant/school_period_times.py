"""Bounded, value-minimal clock projection from an official timetable table.

There is no fallback clock schedule. Only a standalone numbered period cell and
an explicit clock pair in the same row of a timetable-header table are usable.
Course, examination, hidden-input and script text never become clock values.
"""
from copy import deepcopy
from html.parser import HTMLParser
import re


TIMETABLE_PAGE = "/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html"
PERIOD_PAGE_LIMIT = 512 * 1024
PERIOD_SOURCE_STATES = frozenset({"known", "not_available", "source_unavailable",
                                 "schema_changed", "permission_denied"})
_CLOCK = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]"
_PAIR = re.compile(rf"^({_CLOCK})\s*(?:[-—–－]|至)\s*({_CLOCK})$")
_SUBJECT_NAMES = frozenset({"source_subject_id", "student_number", "xh", "xhm", "学号"})


def period_source(state="not_available"):
    if state not in PERIOD_SOURCE_STATES:
        raise ValueError("invalid_period_source")
    return {"state": state, "page_path": TIMETABLE_PAGE}


def validate_period_metadata(times, source):
    """Reject partial/unknown structures, arbitrary paths and ambiguous periods."""
    if (not isinstance(source, dict) or set(source) != {"state", "page_path"}
            or source.get("state") not in PERIOD_SOURCE_STATES
            or source.get("page_path") != TIMETABLE_PAGE
            or not isinstance(times, list) or len(times) > 40):
        raise ValueError("invalid_period_source")
    periods = set()
    for row in times:
        if (not isinstance(row, dict) or set(row) != {"period", "start_time", "end_time"}
                or type(row.get("period")) is not int or not 1 <= row["period"] <= 40
                or row["period"] in periods
                or not isinstance(row.get("start_time"), str) or not re.fullmatch(_CLOCK, row["start_time"])
                or not isinstance(row.get("end_time"), str) or not re.fullmatch(_CLOCK, row["end_time"])
                or row["start_time"] >= row["end_time"]):
            raise ValueError("invalid_period_source")
        periods.add(row["period"])
    if bool(times) != (source["state"] == "known"):
        raise ValueError("invalid_period_source")
    return sorted(deepcopy(times), key=lambda row: row["period"]), deepcopy(source)


class _PeriodTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.subjects = set()
        self._tables = []
        self._skip = 0
        self._nodes = 0

    def handle_starttag(self, tag, attrs):
        self._nodes += 1
        if self._nodes > 50000 or len(self._tables) > 20:
            raise ValueError("invalid_period_source")
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._skip += 1
        if tag == "input" and (attrs.get("name") or attrs.get("id")) in _SUBJECT_NAMES:
            value = attrs.get("value", "").strip()
            if value:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
                    raise ValueError("invalid_period_source")
                self.subjects.add(value)
        if tag == "table":
            if self._tables:
                self._tables[-1]["nested"] = True
            self._tables.append({"rows": [], "row": None, "cell": None, "nested": False})
        if not self._tables or self._skip:
            return
        table = self._tables[-1]
        if tag == "tr":
            if table["row"] is not None:
                raise ValueError("invalid_period_source")
            table["row"] = []
        if tag in {"td", "th"} and table["row"] is not None:
            if table["cell"] is not None:
                raise ValueError("invalid_period_source")
            spans = {}
            for key in ("colspan", "rowspan"):
                value = attrs.get(key, "1")
                limit = 40 if key == "colspan" else 1000
                spans[key] = int(value) if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}", value) and int(value) <= limit else None
            table["cell"] = {"parts": [], **spans}
        if tag == "br" and table["cell"] is not None:
            table["cell"]["parts"].append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if not self._tables:
            return
        table = self._tables[-1]
        if tag in {"td", "th"} and table["cell"] is not None:
            cell = table["cell"]
            table["row"].append({"text": " ".join("".join(cell["parts"]).split()),
                                 "colspan": cell["colspan"], "rowspan": cell["rowspan"]})
            table["cell"] = None
        if tag == "tr" and table["row"] is not None:
            if table["cell"] is not None or len(table["row"]) > 100:
                raise ValueError("invalid_period_source")
            table["rows"].append(table["row"])
            table["row"] = None
            if len(table["rows"]) > 1000:
                raise ValueError("invalid_period_source")
        if tag == "table":
            if table["row"] is not None or table["cell"] is not None:
                raise ValueError("invalid_period_source")
            self.tables.append(self._tables.pop())

    def handle_data(self, data):
        if self._skip or not self._tables or self._tables[-1]["cell"] is None:
            return
        cell = self._tables[-1]["cell"]["parts"]
        if sum(map(len, cell)) + len(data) > 512:
            # A large course cell is unusable; bounded marker prevents holding
            # its course/teacher/exam text while other rows are examined.
            cell[:] = ["INVALID-LARGE-CELL"]
        elif cell != ["INVALID-LARGE-CELL"]:
            cell.append(data)


def _positioned_rows(rows):
    """Compute bounded table columns, respecting occupied rowspan cells.

    Invalid/overlapping spans mean the time region cannot be established. The
    caller then returns no clock rows instead of guessing course-column layout.
    """
    active, result = {}, []
    for index, row in enumerate(rows):
        occupied = {column for column, end_row in active.items() if end_row > index}
        cursor, placed = 0, []
        for cell in row:
            if cell["colspan"] is None or cell["rowspan"] is None:
                return None
            while cursor in occupied:
                cursor += 1
            end = cursor + cell["colspan"]
            if end > 100 or any(column in occupied for column in range(cursor, end)):
                return None
            placed.append({**cell, "start_column": cursor, "end_column": end})
            if cell["rowspan"] > 1:
                for column in range(cursor, end):
                    active[column] = index + cell["rowspan"]
            cursor = end
        result.append(placed)
    return result


def _time_region(rows):
    regions = set()
    for row in rows:
        times = [cell for cell in row if cell["text"] == "时间"]
        weekdays = [cell for cell in row if re.fullmatch(r"星期[一二三四五六日天]", cell["text"])]
        if len(times) != 1 or len({cell["text"] for cell in weekdays}) < 2:
            continue
        first_weekday = min(cell["start_column"] for cell in weekdays)
        time = times[0]
        # The observed table has a "时间" header spanning all left-side time
        # columns. A separate explicit "节次" + "时间" header is also precise.
        covered = time["start_column"] == 0 and time["colspan"] >= 2
        separate_period = (time["start_column"] == 1 and any(cell["text"] == "节次"
                           and cell["start_column"] == 0 and cell["end_column"] == 1 for cell in row))
        if (covered or separate_period) and time["end_column"] == first_weekday:
            regions.add((0, first_weekday))
    return next(iter(regions)) if len(regions) == 1 else None


def parse_period_table(text):
    """Return only validated clock rows and transient continuity identifiers."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > PERIOD_PAGE_LIMIT:
        raise ValueError("invalid_period_source")
    page = _PeriodTable()
    page.feed(text)
    page.close()
    if page._tables or page._skip:
        raise ValueError("invalid_period_source")
    times = {}
    for table in page.tables:
        if table["nested"]:
            continue
        rows = _positioned_rows(table["rows"])
        region = _time_region(rows) if rows is not None else None
        if region is None:
            continue
        for row in rows:
            time_cells = [cell for cell in row if region[0] <= cell["start_column"]
                          and cell["end_column"] <= region[1] and cell["rowspan"] == 1]
            numbers = [int(cell["text"]) for cell in time_cells if re.fullmatch(r"[1-9][0-9]?", cell["text"])]
            pairs = [_PAIR.fullmatch(cell["text"]) for cell in time_cells]
            pairs = [pair for pair in pairs if pair]
            if not pairs or not numbers:
                continue
            if len(numbers) != 1 or len(pairs) != 1 or not 1 <= numbers[0] <= 40:
                raise ValueError("invalid_period_source")
            item = {"period": numbers[0], "start_time": pairs[0][1], "end_time": pairs[0][2]}
            if item["start_time"] >= item["end_time"] or numbers[0] in times and times[numbers[0]] != item:
                raise ValueError("invalid_period_source")
            times[numbers[0]] = item
    source = period_source("known" if times else "not_available")
    return (*validate_period_metadata(list(times.values()), source), page.subjects)
