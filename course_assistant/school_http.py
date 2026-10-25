"""In-memory ZJU CAS and teaching-system client.

Protocol reference: Celechron commit 724e54a3d8feb190a90544963cd53e8ef01c0ff9.
The request mappings below also use the teaching pages observed on 2026-10-14.
No upstream code is executed. Credentials, cookies and tickets are never saved,
logged or returned. Only an authenticated *own-profile* response establishes an
identity; a submitted account name is not an identity assertion.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import secrets
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .school_enrollment_preview import READ_ROUTES as ENROLLMENT_READ_ROUTES
from .student_source_parsers import PROFILE_FIELDS
from .student_source_client import MAX_SOURCE_PAGES, MAX_TIMETABLE_ROWS
from .school_period_times import TIMETABLE_PAGE, PERIOD_PAGE_LIMIT, parse_period_table, period_source
from .student_sources import CREDENTIAL_VALUE, StudentSourceError, sanitize_payload, business_field_diagnostics

SSO = "https://zjuam.zju.edu.cn"
SCHOOL = "https://zdbk.zju.edu.cn"
CAS_LOGIN = "/cas/login"
CAS_KEY = "/cas/v2/getPubKey"
CAS_CAPTCHA_STATUS = "/cas/v2/getKaptchaStatus"
CAS_CAPTCHA = "/cas/kaptcha"
SERVICE = "/jwglxt/xtgl/login_ssologin.html"
MENU = "/jwglxt/xtgl/index_initMenu.html"
# The profile dialog is opened by index_v510335.js and bootbox's POST load;
# this exact route/method was observed in the official menu on 2026-10-14.
# No caller may supply arbitrary URLs or student identifiers.
PROFILE_ROUTE = "/jwglxt/xtgl/yhxx_cxYhxx.html"
PROFILE_MENU_CODE = None
DATA_ROUTES = {
    "graduation_audit": ("/jwglxt/bysh/byshck_cxByshzsIndex.html", "N6025"),
    "academic": ("/jwglxt/cxdy/xscjcx_cxXscjIndex.html", "N5083"),
    "major_academic": ("/jwglxt/zycjtj/xszgkc_cxXsZgkcIndex.html", None),
    "timetable": ("/jwglxt/kbcx/xskbcx_cxXsKb.html", "N253508"),
    "exams": ("/jwglxt/xskscx/kscx_cxXsgrksIndex.html", "N509070"),
}
PLAN_CATALOGUE = "/jwglxt/pyfagl/pyfaxxcx_cxPyfaxscxIndex.html"
PLAN_DOCUMENT = "/jwglxt/pyfagl/pyfayl_cxPyfaylPdf.html"
PLAN_MAJOR_OPTIONS = "/jwglxt/xtgl/comm_cxPyfaZyxxList.html"
CAPTCHA = "/jwglxt/kaptcha"
JSON_LIMIT = 2 * 1024 * 1024
PDF_LIMIT = 10 * 1024 * 1024
IMAGE_LIMIT = 512 * 1024
PLAN_SCOPE = frozenset({"cohort", "college_name", "major_name", "major_or_category_raw", "program", "study_path_raw"})
PAGINATION = frozenset({"total", "totalCount", "totalResult", "pageNo", "pageSize", "currentPage", "showCount"})
SUBJECT_KEYS = ("source_subject_id", "student_number", "xh", "xhm", "学号")
_TERM = re.compile(r"^(20\d{2})-(20\d{2})-(AW|SS|A|W|S|SU)$")
_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PLAN_ID = re.compile(r"^[\w.()-]{1,128}$", re.UNICODE)
_BAD_VALUE = re.compile(r"(?i)(?:\b(?:Bearer|Basic)\s+|(?:password|passwd|cookie|token|ticket|authorization|csrf|JSESSIONID)\s*[:=])")
LOGIN_DIAGNOSTIC_STEPS = frozenset({"cas_form", "cas_captcha_status", "cas_pubkey", "cas_submit",
                                  "service_ticket", "service_callback", "menu_context", "own_profile", "login_captcha"})


class SchoolHTTPError(ValueError):
    """Only a stable sanitized code, never a school response or URL."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise SchoolHTTPError(code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _CookiePolicy(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return (cookie.domain.lstrip(".") in {"zju.edu.cn", "zjuam.zju.edu.cn", "zdbk.zju.edu.cn"}
                and super().set_ok(cookie, request))


@dataclass(frozen=True, repr=False)
class _Response:
    status: int
    content_type: str
    location: str | None
    body: bytes


class _Page(HTMLParser):
    """Small DOM projection used only in memory; scripts are never evaluated."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.inputs = {}
        self.links = []
        self.selects = {}
        self.rows = []
        self.inline_mentions_ignore_zoom = False
        self._select = self._option = self._row = self._cell = None
        self._skip = 0
        self.nodes = []
        self._root = {"tag": "root", "attrs": {}, "children": [], "text": [], "parent": None}
        self._stack = [self._root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if len(self.nodes) >= 50000:
            _fail("schema_changed")
        node = {"tag": tag, "attrs": attrs, "children": [], "text": [], "parent": self._stack[-1]}
        self._stack[-1]["children"].append(node)
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            if len(self._stack) > 100:
                _fail("schema_changed")
            self._stack.append(node)
        if tag in {"script", "style"}:
            self._skip += 1
        if tag == "input":
            name = attrs.get("name") or attrs.get("id")
            if name:
                self.inputs.setdefault(name, []).append(attrs.get("value", ""))
            if self._cell is not None and attrs.get("value"):
                self._cell.append(attrs["value"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "select":
            self._select = attrs.get("id") or attrs.get("name")
            if self._select:
                self.selects.setdefault(self._select, [])
        if tag == "option" and self._select:
            self._option = {"value": attrs.get("value", ""), "label": [], "selected": "selected" in attrs}
        if tag == "tr":
            self._row = []
        if tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                break
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if tag == "option" and self._option is not None and self._select:
            self._option["label"] = " ".join("".join(self._option["label"]).split())
            self.selects[self._select].append(self._option)
            self._option = None
        if tag == "select":
            self._select = None
        if tag in {"td", "th"} and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._skip:
            node = self._stack[-1]
            if node["tag"] == "script" and not node["attrs"].get("src") and re.search(r"\bignoreZoom\b", data):
                self.inline_mentions_ignore_zoom = True
            return
        self._stack[-1]["text"].append(data)
        if self._option is not None:
            self._option["label"].append(data)
        if self._cell is not None:
            self._cell.append(data)

    @staticmethod
    def text(node):
        if node["tag"] in {"script", "style"} or "hidden" in node["attrs"] or node["attrs"].get("type", "").lower() == "hidden":
            return ""
        raw = "".join(node["text"])
        if node["tag"] == "input" and node["attrs"].get("type", "text").lower() not in {"password", "submit", "button"}:
            raw += node["attrs"].get("value", "")
        raw += " ".join(_Page.text(child) for child in node["children"])
        return " ".join(raw.split())

    def profile_labels(self, labels):
        projected = {}
        by_id = {node["attrs"].get("id"): node for node in self.nodes if node["attrs"].get("id")}
        for node in self.nodes:
            if node["tag"] not in {"label", "dt", "th"}:
                continue
            label = self.text(node).replace(" ", "").rstrip("：:")
            field = labels.get(label)
            if not field:
                continue
            target = by_id.get(node["attrs"].get("for"))
            if target:
                values = [self.text(target)]
            else:
                siblings = node["parent"]["children"]
                index = next(index for index, sibling in enumerate(siblings) if sibling is node)
                values = [self.text(sibling) for sibling in siblings[index + 1:] if sibling["tag"] not in {"script", "style"}]
            values = [value for value in values if value]
            # A single label/value pair is required. Ambiguous parents such as
            # a whole multi-field form cannot establish a profile assertion.
            if len(values) != 1:
                continue
            if field in projected and projected[field] != values[0]:
                _fail("schema_changed")
            projected[field] = values[0]
        return projected


def _scalar(value, limit=10000):
    if value is None or isinstance(value, bool):
        return value
    if type(value) in {int, float}:
        try:
            json.dumps(value, allow_nan=False)
        except ValueError:
            _fail("schema_changed")
        return value
    if isinstance(value, str) and len(value) <= limit and not CREDENTIAL_VALUE.search(value) and not _BAD_VALUE.search(value) and not re.search(r"<[A-Za-z!/?][^>]*>", value):
        return value
    _fail("schema_changed")


def _subject(value):
    if type(value) not in {str, int}:
        return None
    text = str(value).strip()
    return text if _SUBJECT.fullmatch(text) else None


def _execution(value):
    # Current CAS returns a signed, encoded form-state value of roughly 4 KiB.
    # Treat it as an opaque same-form value; never interpret or expose it.
    return isinstance(value, str) and bool(re.fullmatch(r"[\x20-\x7e]{1,8192}", value))


def _canonical_pagination(payload):
    """Remove only the observed unused legacy bean pageNo sentinel.

    The real jqGrid response declares currentPage=1 while its separate,
    unused pageNo remains integer zero. An explicit positive currentPage is
    required to discard that sentinel; conflicting real page numbers fail.
    """
    if "pageNo" not in payload:
        return
    def positive(value):
        if type(value) is int:
            return value if value >= 1 else None
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value) and int(value) >= 1:
            return int(value)
        return None
    number = payload["pageNo"]
    current = positive(payload.get("currentPage"))
    if type(number) is int and number == 0:
        if current is None:
            _fail("schema_changed")
        del payload["pageNo"]
    elif positive(number) is None or current is not None and positive(number) != current:
        _fail("schema_changed")


def _subjects(payload):
    found = set()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in SUBJECT_KEYS:
                    if isinstance(child, dict):
                        child = child.get("value", child.get("raw_value"))
                    item = _subject(child)
                    if item:
                        found.add(item)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    if len(found) > 1:
        _fail("source_subject_changed")
    return next(iter(found)) if found else None


def _classify(response):
    """Recognize login/challenge responses before business JSON parsing."""
    if response.status in {401, 403, 901} or 300 <= response.status < 400:
        return "source_session_expired"
    if response.status != 200:
        return "source_unavailable"
    text = response.body.decode("utf-8", "replace")
    lower = text.lower()
    try:
        value = json.loads(text)
    except ValueError:
        # Login HTML often also includes dormant captcha controls. A password
        # form means expired authentication, rather than a query challenge.
        if re.search(r"(?:本人承诺遵守考试纪律|考试纪律承诺)", text):
            return "user_confirmation_required"
        if "captcha_error" in lower and "<" not in text:
            return "captcha_required"
        if (re.search(r"(?:type\s*=\s*['\"]?password|cas/login|统一身份认证|未登录|请先登录|登录.{0,4}失效|认证失败|authserver|unauthorized)", lower)
                or re.search(r"(?:无权|权限不足|禁止访问)", text)):
            return "source_session_expired"
        if re.search(r"(?:captcha_error|验证码|滑块|人机验证)", text, re.I):
            return "captcha_required"
        return "business"
    if isinstance(value, str) and value.lower() == "captcha_error":
        return "captcha_required"
    if isinstance(value, dict):
        for item in [value, value.get("data"), value.get("result")]:
            if not isinstance(item, dict):
                continue
            if str(item.get("kickout")) == "1" or str(item.get("code", item.get("status"))) in {"401", "403", "901"}:
                return "source_session_expired"
            message = " ".join(str(item.get(key, "")) for key in ("msg", "message", "error"))
            if re.search(r"本人承诺遵守考试纪律|考试纪律承诺", message):
                return "user_confirmation_required"
            if item.get("result") == "captcha_error" or item.get("data") == "captcha_error":
                return "captcha_required"
            if re.search(r"captcha|验证码", message, re.I) and (item.get("success") is False or item.get("code") not in {None, 200, "200"}):
                return "captcha_required"
            if re.search(r"登录|认证|过期|unauthorized|kickout", message, re.I) and (item.get("success") is False or item.get("code") not in {None, 200, "200"}):
                return "source_session_expired"
    return "business"


def _profile_from_response(response):
    if response.content_type in {"application/json", "text/json"}:
        try:
            payload = json.loads(response.body)
        except (ValueError, UnicodeError):
            _fail("schema_changed")
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
    elif response.content_type in {"text/html", "application/xhtml+xml"}:
        page = _Page(response.body.decode("utf-8", "strict"))
        payload = {}
        label_keys = {alias.replace(" ", "").rstrip("：:"): field
                      for field, aliases in PROFILE_FIELDS.items() for alias in aliases if re.search(r"[^\x00-\x7f]", alias)}
        for row in page.rows:
            for index in range(0, len(row) - 1, 2):
                field = label_keys.get(row[index].replace(" ", "").rstrip("：:"))
                if field:
                    prior = payload.get(field)
                    if prior is not None and prior != row[index + 1]:
                        _fail("schema_changed")
                    payload[field] = row[index + 1] or None
        for field, value in page.profile_labels(label_keys).items():
            if field in payload and payload[field] != value:
                _fail("schema_changed")
            payload[field] = value
    else:
        _fail("schema_changed")
    if not isinstance(payload, dict):
        _fail("schema_changed")
    normalized = {}
    for field, aliases in PROFILE_FIELDS.items():
        found = [_scalar(payload[key], 160) for key in aliases if key in payload and payload[key] is not None and payload[key] != ""]
        if found and any(value != found[0] for value in found):
            _fail("schema_changed")
        normalized[field] = found[0] if found else None
    observed = _subject(normalized.get("source_subject_id"))
    if not observed:
        _fail("identity_not_observed")
    normalized["source_subject_id"] = observed
    if normalized.get("cohort") is not None:
        raw = str(normalized["cohort"])
        match = re.fullmatch(r"((?:19|20|21)\d{2})(?:级|年)?", raw)
        if not match:
            _fail("schema_changed")
        normalized["cohort"] = int(match[1])
    elif isinstance(normalized.get("admission_date_raw"), str):
        raw = normalized["admission_date_raw"]
        if re.fullmatch(r"\d{8}", raw):
            try:
                normalized["cohort"] = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])).year
            except ValueError:
                pass
    return normalized


class SchoolHTTPClient:
    """One account, one CookieJar, serial requests; no process-global school state.

    ``opener`` is an injected urllib-compatible opener for scripted tests. A real
    client creates its own no-redirect opener and validates every destination.
    Never reuse one client for simultaneously logged-in application sessions.
    """
    def __init__(self, *, timeout=8, opener=None):
        if type(timeout) not in {int, float} or not 0 < timeout <= 30:
            raise ValueError("invalid_timeout")
        self._timeout = timeout
        self._cookies = http.cookiejar.CookieJar(policy=_CookiePolicy())
        self._opener = opener if opener is not None else build_opener(_NoRedirect())
        # Wire serialization and revocation must never share a lock. A timed
        # out source task can read its epoch or close while a socket is blocked.
        self._lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._epoch = secrets.token_hex(16)
        self._operation_epoch = None
        self._closed = False
        self._diagnostics = None
        self._authenticated = False
        self._profile = None
        self._captcha_text = None
        self._captcha_pending = False
        self._login_execution = None
        self._login_captcha_text = None
        self._login_image_ready = False
        self._login_expires = 0
        self._documents = {}
        self._catalogue_scope = None
        self._session_user_key = None
        self._session_menu_code = None
        self._period_page_read = False

    @property
    def session_epoch(self):
        with self._state_lock:
            return self._epoch

    def _check_active(self):
        if self._closed or self._operation_epoch != self._epoch:
            _fail("source_session_expired")

    def _bump(self, *, operation=False):
        if operation:
            self._check_active()
        self._epoch = secrets.token_hex(16)
        if operation:
            self._operation_epoch = self._epoch
        self._documents.clear()
        self._catalogue_scope = None
        self._captcha_text = None
        self._captcha_pending = False
        self._login_execution = None
        self._login_captcha_text = None
        self._login_image_ready = False
        self._login_expires = 0
        self._session_user_key = None
        self._session_menu_code = None
        if not operation:
            self._diagnostics = None

    def _result(self, state, response=None, **fields):
        with self._state_lock:
            if fields and (self._closed or self._operation_epoch != self._epoch):
                state, response, fields = "source_session_expired", None, {}
            result = {"state": state, "session_epoch": self._epoch,
                      "http_status": response.status if response else None,
                      "content_type": response.content_type if response else None,
                      "source_subject_id": self._profile.get("source_subject_id") if self._profile else None}
            result.update(fields)
            return result

    def logout(self):
        with self._state_lock:
            self._closed = True
            self._cookies.clear()
            self._profile = None
            self._authenticated = False
            self._bump()

    close = logout

    def _diagnostic_copy(self):
        with self._state_lock:
            if self._closed or self._operation_epoch != self._epoch or not self._diagnostics:
                return None
            return dict(self._diagnostics)

    @staticmethod
    def _login_step(method, origin, path, query):
        if origin == SSO:
            if path == CAS_LOGIN:
                return "cas_submit" if method == "POST" else "service_ticket" if "service" in parse_qs(query) else "cas_form"
            return {CAS_KEY: "cas_pubkey", CAS_CAPTCHA_STATUS: "cas_captcha_status", CAS_CAPTCHA: "login_captcha"}.get(path)
        return {SERVICE: "service_callback", MENU: "menu_context", PROFILE_ROUTE: "own_profile"}.get(path)

    def _raw(self, method, url, form=None, *, limit=JSON_LIMIT):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        allowed = ({CAS_LOGIN, CAS_KEY, CAS_CAPTCHA_STATUS, CAS_CAPTCHA} if origin == SSO else
                   {SERVICE, MENU, CAPTCHA, PROFILE_ROUTE, PLAN_CATALOGUE, PLAN_DOCUMENT, PLAN_MAJOR_OPTIONS,
                    TIMETABLE_PAGE} | ENROLLMENT_READ_ROUTES | {route for route, _ in DATA_ROUTES.values()})
        if origin not in {SSO, SCHOOL} or parsed.username or parsed.password or parsed.fragment or parsed.path not in allowed:
            _fail("route_not_confirmed")
        body = urlencode(form).encode("utf-8") if form is not None else None
        headers = {"Accept": "application/json, text/html;q=0.8, */*;q=0.5",
                   "User-Agent": "ZJU-Course-Planner/0.2", "Referer": SCHOOL + MENU}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        if origin == SCHOOL and method == "POST":
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Origin"] = SCHOOL
        request = Request(url, data=body, headers=headers, method=method)
        with self._state_lock:
            self._check_active()
            expected = self._epoch
            step = self._login_step(method, origin, parsed.path, parsed.query)
            self._diagnostics = {"failure_step": step, "http_status": None, "transport_error": None} if step in LOGIN_DIAGNOSTIC_STEPS else None
            self._cookies.add_cookie_header(request)
        try:
            try:
                response = self._opener.open(request, timeout=self._timeout)
            except HTTPError as error:
                response = error
            with response:
                with self._state_lock:
                    self._check_active()
                    if self._epoch != expected:
                        _fail("source_session_expired")
                    # The teaching system uses nonstandard 9xx HTTP statuses,
                    # including 901 for an unauthenticated own-profile call.
                    if self._diagnostics is not None and type(response.status) is int and 100 <= response.status <= 999:
                        self._diagnostics["http_status"] = response.status
                    self._cookies.extract_cookies(response, request)
                content_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                length = response.headers.get("Content-Length")
                if length and re.fullmatch(r"\d+", length) and int(length) > limit:
                    _fail("schema_changed")
                data = response.read(limit + 1)
                if len(data) > limit:
                    _fail("schema_changed")
                with self._state_lock:
                    self._check_active()
                    if self._epoch != expected:
                        _fail("source_session_expired")
                    return _Response(response.status, content_type, response.headers.get("Location"), data)
        except SchoolHTTPError:
            raise
        except (URLError, OSError, TimeoutError, ValueError, TypeError, UnicodeError) as error:
            underlying = error.reason if isinstance(error, URLError) else error
            category = ("timeout" if isinstance(underlying, TimeoutError) else "tls" if isinstance(underlying, ssl.SSLError)
                        else "network" if isinstance(error, (URLError, OSError)) else None)
            with self._state_lock:
                if not self._closed and self._epoch == expected and self._diagnostics is not None:
                    self._diagnostics["transport_error"] = category
            _fail("source_unavailable")

    def _has_cookie(self, name, host):
        with self._state_lock:
            self._check_active()
            now = time.time()
            return any(cookie.name == name and cookie.value and not cookie.is_expired(now)
                       and (host == cookie.domain.lstrip(".") or host.endswith("." + cookie.domain.lstrip("."))) for cookie in self._cookies)

    def _clear_business_cookies(self):
        with self._state_lock:
            self._check_active()
            for cookie in list(self._cookies):
                if cookie.domain.lstrip(".") == "zdbk.zju.edu.cn":
                    self._cookies.clear(cookie.domain, cookie.path, cookie.name)

    def _service_login(self):
        if not self._has_cookie("iPlanetDirectoryPro", "zjuam.zju.edu.cn"):
            _fail("source_session_expired")
        self._clear_business_cookies()
        response = self._raw("GET", SSO + CAS_LOGIN + "?" + urlencode({"service": SCHOOL + SERVICE}))
        if response.status not in {301, 302, 303, 307, 308} or not response.location:
            _fail("source_session_expired" if response.status in {200, 401, 403} else "source_unavailable")
        callback = urljoin(SSO + CAS_LOGIN, response.location)
        parsed = urlsplit(callback)
        # The older school callback can advertise http; upgrade only this exact
        # school host/path to TLS before consuming its one-time ticket.
        if parsed.scheme == "http" and parsed.netloc == "zdbk.zju.edu.cn" and parsed.path == SERVICE:
            callback = "https" + callback[4:]
            parsed = urlsplit(callback)
        values = parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.scheme != "https" or parsed.netloc != "zdbk.zju.edu.cn" or parsed.path != SERVICE
                or parsed.fragment or set(values) != {"ticket"} or len(values["ticket"]) != 1
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,512}", values["ticket"][0])):
            _fail("route_not_confirmed")
        response = self._raw("GET", callback)
        if (response.status not in {200, 301, 302, 303} or _classify(response) == "captcha_required"
                or response.status in {401, 403}):
            _fail("source_session_expired")
        if response.status == 200 and _classify(response) == "source_session_expired":
            _fail("source_session_expired")
        if not all(self._has_cookie(name, "zdbk.zju.edu.cn") for name in ("JSESSIONID", "route")):
            _fail("source_session_expired")
        with self._state_lock:
            self._bump(operation=True)
        # Preserve only a menu Location actually issued by the authenticated
        # callback; dropping its role query would change the normal flow.
        menu_url = self._menu_url(response.location, callback) if response.location else None
        self._load_menu_context(menu_url)

    @staticmethod
    def _menu_url(raw, base):
        target = urljoin(base, raw)
        parsed = urlsplit(target)
        query = parse_qs(parsed.query, keep_blank_values=True)
        path_ok = parsed.path == MENU or bool(re.fullmatch(re.escape(MENU) + r";jsessionid=[A-Za-z0-9_.:-]{1,512}", parsed.path))
        if (parsed.scheme == "https" and parsed.netloc == "zdbk.zju.edu.cn" and path_ok
                and not parsed.fragment and set(query) <= {"jsdm", "ignoreZoom", "_t"}
                and all(len(values) == 1 and re.fullmatch(r"\d{1,16}" if key == "_t" else r"\d{1,3}", values[0])
                        for key, values in query.items())):
            # The actual callback contains a servlet matrix session id and a
            # cache-buster. Cookies already carry the session; never forward,
            # persist or expose its URL representation or the cache-buster.
            retained = {key: values[0] for key, values in query.items() if key in {"jsdm", "ignoreZoom"}}
            return SCHOOL + MENU + ("?" + urlencode(retained) if retained else "")
        return None

    def _load_menu_context(self, observed_url=None):
        """Reproduce the official page's normal AJAX URL context.

        jquery.zftal.contact-min.js ajaxSend/getURL appends the current menu's
        sessionUserKey as ``su``. It is a transport parameter, not an identity
        proof. No input username or caller-provided identifier is used.
        """
        url = observed_url or SCHOOL + MENU
        for index in range(2):
            response = self._raw("GET", url)
            state = _classify(response)
            if state != "business":
                _fail(state)
            if response.content_type not in {"text/html", "application/xhtml+xml"}:
                _fail("schema_changed")
            page = _Page(response.body.decode("utf-8", "strict"))
            keys = [node["attrs"].get("value", "") for node in page.nodes
                    if node["tag"] == "input" and node["attrs"].get("id") == "sessionUserKey"
                    and node["attrs"].get("type", "").lower() == "hidden"]
            if keys:
                if len(keys) != 1 or not re.fullmatch(r"[\x21-\x7e]{1,512}", keys[0]):
                    _fail("schema_changed")
                menu_values = [node["attrs"].get("value", "") for node in page.nodes
                               if node["tag"] == "input" and node["attrs"].get("id") == "gnmkdmKey"]
                # The initial menu's own context is an opaque value (not
                # necessarily a functional N-code). Reproduce it only from
                # that authenticated page and URL-encode it without guessing.
                if len(menu_values) > 1 or menu_values and menu_values[0] and not re.fullmatch(r"[\x21-\x7e]{1,128}", menu_values[0]):
                    _fail("schema_changed")
                with self._state_lock:
                    self._check_active()
                    self._session_user_key = keys[0]
                    self._session_menu_code = menu_values[0] if menu_values and menu_values[0] else None
                return
            # Follow only a single iframe actually returned by this own menu.
            # Role and zoom values are observed in that src, never invented.
            if index:
                _fail("route_not_confirmed")
            frames = []
            has_frame_source = False
            for node in page.nodes:
                if node["tag"] != "iframe" or not node["attrs"].get("src"):
                    continue
                has_frame_source = True
                target = self._menu_url(node["attrs"]["src"], url)
                if target:
                    frames.append(target)
            if not frames and not has_frame_source and page.inline_mentions_ignore_zoom:
                # The observed viewport wrapper assigns its iframe src in JS.
                # Use only its already-confirmed normal rendering switch;
                # preserve an observed role and execute no page JavaScript.
                current = urlsplit(url)
                observed = parse_qs(current.query, keep_blank_values=True)
                query = {"jsdm": observed["jsdm"][0]} if "jsdm" in observed else {}
                query["ignoreZoom"] = "1"
                target = SCHOOL + MENU + "?" + urlencode(query)
                if target != url:
                    frames.append(target)
            if len(frames) != 1:
                _fail("route_not_confirmed")
            url = frames[0]

    def _school_url(self, route, parameters=None, *, use_menu_code=False):
        with self._state_lock:
            self._check_active()
            if not self._session_user_key:
                _fail("route_not_confirmed")
            query = dict(parameters or {})
            if "su" in query:
                _fail("route_not_confirmed")
            query["su"] = self._session_user_key
            if use_menu_code and self._session_menu_code and "gnmkdm" not in query:
                query["gnmkdm"] = self._session_menu_code
            return SCHOOL + route + "?" + urlencode(query)

    def login(self, username, password):
        with self._lock:
            with self._state_lock:
                self._diagnostics = None
            try:
                if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", username)
                        or not isinstance(password, str) or not 0 < len(password.encode("utf-8")) <= 512):
                    _fail("invalid_credentials")
                with self._state_lock:
                    continuation = bool(not self._closed and self._login_execution and self._login_captcha_text and
                                        self._login_image_ready and time.time() < self._login_expires)
                    if not continuation:
                        self._cookies.clear()
                        self._profile = None
                        self._authenticated = False
                        self._closed = False
                        self._bump()
                    self._operation_epoch = self._epoch
                    execution, authcode = self._login_execution, self._login_captcha_text
                if not continuation:
                    initial = self._raw("GET", SSO + CAS_LOGIN)
                    if initial.status != 200:
                        _fail("source_unavailable")
                    page = _Page(initial.body.decode("utf-8", "strict"))
                    values = page.inputs.get("execution", [])
                    if len(values) != 1 or not _execution(values[0]):
                        _fail("schema_changed")
                    execution = values[0]
                    if re.search(r"滑块|人机验证", initial.body.decode("utf-8", "replace")):
                        _fail("interactive_verification_required")
                    # The ordinary login HTML always contains the authcode
                    # controls. Its public login.js chooses visibility via this
                    # exact boolean endpoint; raw label text is not a challenge.
                    status = self._raw("GET", SSO + CAS_CAPTCHA_STATUS)
                    if status.status != 200:
                        _fail("source_unavailable")
                    required = json.loads(status.body)
                    if type(required) is not bool:
                        _fail("schema_changed")
                    if required:
                        with self._state_lock:
                            self._check_active()
                            self._login_execution = execution
                            self._login_expires = time.time() + 300
                            return self._result("captcha_required")
                    authcode = None
                key_response = self._raw("GET", SSO + CAS_KEY)
                if key_response.status != 200 or key_response.content_type not in {"application/json", "text/json"}:
                    _fail("schema_changed")
                key = json.loads(key_response.body)
                if not isinstance(key, dict):
                    _fail("schema_changed")
                modulus, exponent = key.get("modulus"), key.get("exponent")
                if (not isinstance(modulus, str) or not re.fullmatch(r"[A-Fa-f0-9]{128,1024}", modulus)
                        or not isinstance(exponent, str) or not re.fullmatch(r"[A-Fa-f0-9]{1,8}", exponent)):
                    _fail("schema_changed")
                mod, exp = int(modulus, 16), int(exponent, 16)
                pwd_number = int.from_bytes(password.encode("utf-8"), "big")
                if mod <= pwd_number or exp < 3 or exp % 2 == 0:
                    _fail("invalid_credentials")
                encrypted = format(pow(pwd_number, exp, mod), "x").zfill(128)
                form = {"username": username, "password": encrypted, "execution": execution,
                        "_eventId": "submit", "rememberMe": "false"}
                if authcode is not None:
                    form["authcode"] = authcode
                with self._state_lock:
                    self._check_active()
                    self._login_captcha_text = None
                submitted = self._raw("POST", SSO + CAS_LOGIN, form)
                if not self._has_cookie("iPlanetDirectoryPro", "zjuam.zju.edu.cn"):
                    text = submitted.body.decode("utf-8", "replace")
                    if "captcha_error" in text.lower() or re.search(r"验证码.{0,4}(?:错误|不正确|失效)", text):
                        values = _Page(text).inputs.get("execution", [])
                        if len(values) == 1 and _execution(values[0]):
                            with self._state_lock:
                                self._check_active()
                                self._login_execution = values[0]
                                self._login_image_ready = False
                                self._login_expires = time.time() + 300
                                return self._result("captcha_required")
                    _fail("invalid_credentials" if submitted.status in {200, 302, 401, 403} else "source_unavailable")
                self._service_login()
                profile = self._get_profile_once()
                with self._state_lock:
                    self._check_active()
                    self._profile = profile
                    self._authenticated = True
                    return self._result("authenticated", profile=deepcopy(profile))
            except (SchoolHTTPError, ValueError, TypeError, UnicodeError) as error:
                code = error.code if isinstance(error, SchoolHTTPError) else "schema_changed"
                diagnostics = self._diagnostic_copy()
                self.logout()
                result = self._result(code)
                if diagnostics:
                    result["diagnostics"] = diagnostics
                return result

    def _get_profile_once(self):
        if PROFILE_ROUTE is None:
            _fail("route_not_confirmed")
        query = {"gnmkdm": PROFILE_MENU_CODE} if PROFILE_MENU_CODE else {}
        response = self._raw("POST", self._school_url(PROFILE_ROUTE, query, use_menu_code=True), {})
        state = _classify(response)
        if state != "business":
            _fail(state)
        profile = _profile_from_response(response)
        with self._state_lock:
            self._check_active()
            if self._profile and profile["source_subject_id"] != self._profile["source_subject_id"]:
                _fail("source_subject_changed")
        return profile

    def _with_refresh(self, action):
        try:
            return action()
        except SchoolHTTPError as error:
            if error.code != "source_session_expired":
                raise
        # Exactly one CAS refresh, using only the still valid SSO cookie. A
        # refreshed identity is checked before repeating any business request.
        self._service_login()
        profile = self._get_profile_once()
        with self._state_lock:
            self._check_active()
            self._profile = profile
        return action()

    def get_profile(self):
        with self._lock:
            with self._state_lock:
                if not self._authenticated or self._closed:
                    return self._result("awaiting_user_login")
                self._operation_epoch = self._epoch
            try:
                profile = self._with_refresh(self._get_profile_once)
                with self._state_lock:
                    self._check_active()
                    self._profile = profile
                    return self._result("authenticated", profile=deepcopy(profile))
            except SchoolHTTPError as error:
                if error.code in {"source_session_expired", "source_subject_changed"}:
                    self.logout()
                return self._result(error.code)

    def enrollment_preview(self, course_code=None):
        """Preview only. Never retry or expose a school mutation operation."""
        from .school_enrollment_preview import PAGE, COURSES, SECTIONS, CHOSEN, PUBLIC_URL, PreviewPage, project_list
        if course_code is not None and (not isinstance(course_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", course_code)):
            return self._result("route_not_confirmed")
        with self._lock:
            with self._state_lock:
                if not self._authenticated or self._closed:
                    return self._result("awaiting_user_login")
                self._operation_epoch = self._epoch
            def read_json(route, form):
                response = self._raw("POST", self._school_url(route, {"gnmkdm": "N253530"}), form)
                state = _classify(response)
                if state != "business":
                    _fail(state)
                # The official class-list endpoint returns JSON with text/html.
                # Parse strictly, then validate the bounded list and field schema.
                value = json.loads(response.body)
                observed = _subjects(value)
                if observed and observed != self._profile["source_subject_id"]:
                    _fail("source_subject_changed")
                return value
            def preview():
                response = self._raw("GET", self._school_url(PAGE, {"gnmkdm": "N253530", "layout": "default"}))
                state = _classify(response)
                if state != "business":
                    _fail(state)
                page = PreviewPage()
                page.feed(response.body.decode("utf-8"))
                page.validated()
                ctx = page.context
                form = {key: ctx[key] for key in ("xn", "xq", "nj", "zydm")}
                form.update(dl="xk_1", lx="bl", xkmc="本类(专业)", kcbs="", jxjhh=ctx["nj"]+ctx["zydm"],
                            xnxq="("+ctx["xn"]+"-"+ctx["xq"]+")-", kspage="1", jspage="10")
                courses = project_list(read_json(COURSES, form), {"kcdm", "kcmc", "xskcdm", "kcxx", "kcxzzt", "xkkh"}, 10)
                selected = project_list(read_json(CHOSEN, {"xn": ctx["xn"], "xq": ctx["xq"]}),
                    {"kcdm", "kcmc", "xkkh", "jsxm", "xxq", "sksj", "skdd", "sxbj", "xkzy"}, 100)
                sections = []
                if course_code is not None:
                    if course_code not in {row["kcdm"] for row in courses}:
                        _fail("route_not_confirmed")
                    course = next(row for row in courses if row["kcdm"] == course_code)
                    sections = project_list(read_json(SECTIONS, {"dl": "xk_1", "xn": ctx["xn"], "xq": ctx["xq"],
                        "kcdm": course_code, "xkkh": course.get("xkkh") or "", "ylxs": ctx["ylxs"]}),
                        {"xkkh", "kcdm", "jsxm", "xxq", "sksj", "skdd", "kssj", "rs", "sfxz"}, 100)
                return self._result("business", response, preview={"academic_year": ctx["xn"], "season": ctx["xq"],
                    "courses": courses, "sections": sections, "selected": selected, "submission_enabled": False,
                    "official_url": PUBLIC_URL, "scope": "本类(专业)前10条课程及页面已选教学班；非全校可选目录"})
            try:
                return self._with_refresh(preview)
            except SchoolHTTPError as error:
                return self._result(error.code)
            except (ValueError, UnicodeError):
                return self._result("schema_changed")

    def _spec(self, spec):
        if not isinstance(spec, dict) or set(spec) - {"operation", "target_term", "page", "page_size", "scope", "plan_ref"}:
            _fail("route_not_confirmed")
        operation = spec.get("operation")
        if operation not in set(DATA_ROUTES) | {"plan_catalogue", "plan_document"}:
            _fail("route_not_confirmed")
        page, size = spec.get("page", 1), spec.get("page_size", 20)
        if type(page) is not int or not 1 <= page <= MAX_SOURCE_PAGES or type(size) is not int or not 1 <= size <= 100:
            _fail("route_not_confirmed")
        scope = spec.get("scope", {})
        if not isinstance(scope, dict) or set(scope) - PLAN_SCOPE:
            _fail("route_not_confirmed")
        for value in scope.values():
            if value is not None:
                _scalar(value, 160)
        if operation in DATA_ROUTES:
            if scope or spec.get("plan_ref") is not None:
                _fail("route_not_confirmed")
            if operation in {"major_academic", "graduation_audit"}:
                if spec.get("target_term") != "ALL-HISTORY":
                    _fail("route_not_confirmed")
            else:
                term = _TERM.fullmatch(spec.get("target_term", ""))
                if not term or int(term[2]) != int(term[1]) + 1:
                    _fail("route_not_confirmed")
        return dict(spec, page=page, page_size=size, scope=scope)

    def _business(self, spec):
        operation = spec["operation"]
        route, menu = DATA_ROUTES[operation]
        year_range = None
        if operation not in {"major_academic", "graduation_audit"}:
            year, _, season = _TERM.fullmatch(spec["target_term"]).groups()
            year_range = f"{year}-{int(year) + 1}"
            suffix = {"AW": "1", "SS": "2", "A": "1", "W": "1", "S": "2", "SU": "2"}[season]
            label = {"AW": "秋、冬", "SS": "春、夏", "A": "秋", "W": "冬", "S": "春", "SU": "夏"}[season]
        query = {} if operation == "timetable" else {"doType": "query"}
        if menu:
            query["gnmkdm"] = menu
        if operation in {"major_academic", "graduation_audit"}:
            # Celechron's primary-major transcript endpoint is explicitly an
            # all-history query. Preserve each row's year instead of inventing
            # unobserved year/term request parameters.
            form = {}
        elif operation == "academic":
            if season not in {"AW", "SS"}:
                _fail("route_not_confirmed")
            form = {"xn": year_range, "xq": suffix}
        elif operation == "exams":
            if season in {"AW", "SS"}:
                _fail("route_not_confirmed")
            form = {"xxq": label, "xnxq": f"({year_range}-{suffix})-"}
        else:
            form = {"xnm": year_range, "xqm": suffix + "|" + label}
        if operation == "timetable":
            if spec["page"] != 1:
                _fail("route_not_confirmed")
            form.update(xqmmc=label, xxqf="1", xsfs="1")
            with self._state_lock:
                self._check_active()
                if self._captcha_text:
                    form["captcha_value"] = self._captcha_text
        else:
            form.update({"queryModel.currentPage": str(spec["page"]), "queryModel.showCount": str(spec["page_size"])})
        if operation == "graduation_audit":
            if spec["page"] != 1:
                _fail("route_not_confirmed")
            form = {"queryModel.currentPage": "1", "queryModel.showCount": "5000"}
        response = self._raw("POST", self._school_url(route, query), form)
        state = _classify(response)
        if state != "business":
            if state == "captcha_required":
                with self._state_lock:
                    self._check_active()
                    self._captcha_text = None
                    self._captcha_pending = True
            _fail(state)
        if response.content_type not in {"application/json", "text/json"}:
            _fail("schema_changed")
        try:
            payload = json.loads(response.body)
        except (ValueError, UnicodeError):
            _fail("schema_changed")
        if operation == "graduation_audit":
            observed = _subjects(payload)
            if observed and observed != self._profile["source_subject_id"]:
                _fail("source_subject_changed")
            from .graduation_audit import project_rows
            try:
                payload = project_rows(payload)
            except ValueError:
                _fail("schema_changed")
        if not isinstance(payload, dict):
            _fail("schema_changed")
        domain = "academic" if operation == "major_academic" else operation
        try:
            projection = sanitize_payload(domain, payload)[0]
        except StudentSourceError:
            _fail("schema_changed")
        if operation != "timetable":
            _canonical_pagination(projection)
        containers = [key for key in ("items", "records", "kbList") if key in projection]
        row_limit = 2000 if operation == "graduation_audit" else MAX_TIMETABLE_ROWS if operation == "timetable" else spec["page_size"]
        if len(containers) != 1 or not isinstance(projection[containers[0]], list) or len(projection[containers[0]]) > row_limit:
            _fail("schema_changed")
        observed = _subjects(payload)
        with self._state_lock:
            self._check_active()
            if observed and observed != self._profile["source_subject_id"]:
                _fail("source_subject_changed")
        for row in projection[containers[0]]:
            if not isinstance(row, dict) or not row:
                _fail("schema_changed")
            value = row.get("xkkh", row.get("source_selection_id_raw"))
            source_year = re.match(r"^\((20\d{2}-20\d{2})-[^)]*\)", value) if isinstance(value, str) else None
            if year_range is not None and source_year and source_year[1] != year_range:
                _fail("schema_changed")
        if operation == "timetable":
            projection.update(self._period_times())
        with self._state_lock:
            self._check_active()
            self._captcha_pending = False
            if operation == "graduation_audit":
                return self._result("business", response, content_type="application/json", payload=projection)
            diagnostics = business_field_diagnostics(domain, payload, projection)
            return self._result("business", response, content_type="application/json", payload=projection,
                                source_field_diagnostics=diagnostics)

    def _period_times(self):
        """At most one bounded same-context page read per external request.

        Clock absence or page failure does not discard a successful list.
        Authentication/challenge/subject changes still stop or refresh through
        the normal client flow. No HTML or response identifiers are retained.
        """
        with self._state_lock:
            self._check_active()
            if self._period_page_read:
                return {"period_times": [], "period_times_source": period_source("source_unavailable")}
            self._period_page_read = True
            owner = self._profile["source_subject_id"]
        try:
            response = self._raw("GET", self._school_url(TIMETABLE_PAGE, {"gnmkdm": DATA_ROUTES["timetable"][1]}),
                                 limit=PERIOD_PAGE_LIMIT)
            state = _classify(response)
            if response.status == 403 and not re.search(r"type\s*=\s*['\"]?password|cas/login|请先登录|未登录",
                                                       response.body.decode("utf-8", "replace"), re.I):
                return {"period_times": [], "period_times_source": period_source("permission_denied")}
            if state != "business":
                if state == "captcha_required":
                    with self._state_lock:
                        self._check_active()
                        self._captcha_text = None
                        self._captcha_pending = True
                _fail(state)
            if response.content_type not in {"text/html", "application/xhtml+xml"}:
                _fail("schema_changed")
            times, source, subjects = parse_period_table(response.body.decode("utf-8", "strict"))
            with self._state_lock:
                self._check_active()
                if subjects and subjects != {owner}:
                    _fail("source_subject_changed")
            return {"period_times": times, "period_times_source": source}
        except SchoolHTTPError as error:
            if error.code not in {"source_unavailable", "schema_changed", "permission_denied"}:
                raise
            return {"period_times": [], "period_times_source": period_source(error.code)}
        except (ValueError, UnicodeError):
            return {"period_times": [], "period_times_source": period_source("schema_changed")}

    @staticmethod
    def _select(page, control, label):
        options = [option for option in page.selects.get(control, []) if option["label"] == str(label)]
        if len(options) != 1 or not options[0]["value"] or not re.fullmatch(r"[\w.-]{1,128}", options[0]["value"], re.UNICODE):
            _fail("route_not_confirmed")
        return options[0]["value"]

    @staticmethod
    def _selected_optional(page, control):
        options = page.selects.get(control, [])
        selected = [option for option in options if option["selected"]]
        if len(selected) > 1:
            _fail("schema_changed")
        value = (selected[0] if selected else options[0])["value"] if options else ""
        if value and not re.fullmatch(r"[\w.-]{1,128}", value, re.UNICODE):
            _fail("schema_changed")
        return value

    def _plan_major_option(self, page, year_value, college_value, major_label):
        # bindPyfaChangeEvent/refreshSelectList publicly declares this narrow
        # POST and its own-year/college parameter names. Do not use the empty
        # initial #zydm_cx or infer a major code from a student's identifier.
        form = {"njdm_id": year_value, "jg_id": college_value,
                "zydl_id": self._selected_optional(page, "zydl_cx"),
                "gjzy_id": self._selected_optional(page, "gjzy_cx")}
        url = self._school_url(PLAN_MAJOR_OPTIONS, {"rangeType": "ggxydm", "rangeable": "false", "gnmkdm": "N153020"})
        response = self._raw("POST", url, form)
        state = _classify(response)
        if state != "business":
            _fail(state)
        if response.content_type not in {"application/json", "text/json"}:
            _fail("schema_changed")
        try:
            rows = json.loads(response.body)
        except (ValueError, UnicodeError):
            _fail("schema_changed")
        if not isinstance(rows, list) or len(rows) > 1000:
            _fail("schema_changed")
        matched = []
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("zymc"), str)
                    or type(row.get("zydm")) not in {str, int}):
                _fail("schema_changed")
            label, code = _scalar(row["zymc"], 160), str(row["zydm"])
            if not re.fullmatch(r"[\w.-]{1,128}", code, re.UNICODE):
                _fail("schema_changed")
            if label == major_label:
                matched.append(code)
        if len(matched) != 1:
            _fail("route_not_confirmed")
        return matched[0]

    def _plan_catalogue(self, spec):
        scope = spec["scope"]
        major = scope.get("major_name") or scope.get("major_or_category_raw")
        if not scope.get("cohort") or not scope.get("college_name") or not major:
            _fail("route_not_confirmed")
        # A profile-derived narrow scope is mandatory; no caller may choose
        # another student's plan scope via request fields.
        with self._state_lock:
            self._check_active()
            own_profile = deepcopy(self._profile)
        for key in ("cohort", "college_name"):
            if str(scope[key]) != str(own_profile.get(key)):
                _fail("route_not_confirmed")
        own_major = own_profile.get("major_name") or own_profile.get("major_or_category_raw")
        if major != own_major:
            _fail("route_not_confirmed")
        for key in ("program", "study_path_raw"):
            if scope.get(key) is not None:
                # The observed directory lacks program/path filters. Never
                # pretend a name match proves those additional dimensions.
                _fail("route_not_confirmed")
        signature = json.dumps(scope, ensure_ascii=False, sort_keys=True)
        with self._state_lock:
            self._check_active()
            if spec["page"] == 1 or self._catalogue_scope != signature:
                self._documents.clear()
                self._catalogue_scope = signature
        index = self._raw("GET", self._school_url(PLAN_CATALOGUE, {"gnmkdm": "N153020"}))
        state = _classify(index)
        if state != "business":
            _fail(state)
        if index.content_type not in {"text/html", "application/xhtml+xml"}:
            _fail("schema_changed")
        page = _Page(index.body.decode("utf-8", "strict"))
        year_value = self._select(page, "synj_cx", scope["cohort"])
        college_value = self._select(page, "xydm_cx", scope["college_name"])
        major_value = self._plan_major_option(page, year_value, college_value, major)
        form = {"synj": year_value, "xydm": college_value, "zydm": major_value,
                "gjzydm": self._selected_optional(page, "gjzy_cx"), "dlbs": self._selected_optional(page, "dlbs_cx"), "jxjhh": "",
                "queryModel.currentPage": str(spec["page"]), "queryModel.showCount": str(spec["page_size"])}
        response = self._raw("POST", self._school_url(PLAN_CATALOGUE, {"doType": "query", "gnmkdm": "N153020"}), form)
        state = _classify(response)
        if state != "business":
            _fail(state)
        if response.content_type not in {"application/json", "text/json"}:
            _fail("schema_changed")
        try:
            payload = json.loads(response.body)
        except (ValueError, UnicodeError):
            _fail("schema_changed")
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or len(payload["items"]) > spec["page_size"]:
            _fail("schema_changed")
        result = {"items": []}
        pending = {}
        for row in payload["items"]:
            if (not isinstance(row, dict) or row.get("xymc") != scope["college_name"] or row.get("zymc") != major
                    or not isinstance(row.get("jxjhh"), str) or not _PLAN_ID.fullmatch(row["jxjhh"])):
                _fail("schema_changed")
            ref = "PLAN-" + secrets.token_hex(16)
            pending[ref] = (row["jxjhh"], signature)
            result["items"].append({"plan_ref": ref, "cohort": int(scope["cohort"]), "college_name": row["xymc"],
                "major_name": row["zymc"], "major_or_category_raw": None, "program": None, "study_path_raw": None,
                "cohort_provenance": "confirmed_query_scope", "source_observation_kind": "directory_observation"})
        for key in PAGINATION:
            if key in payload and payload[key] is not None:
                value = payload[key]
                if type(value) is not int and not (isinstance(value, str) and re.fullmatch(r"\d{1,9}", value)):
                    _fail("schema_changed")
                if int(value) < 0:
                    _fail("schema_changed")
                result[key] = value
        _canonical_pagination(result)
        with self._state_lock:
            self._check_active()
            self._documents.update(pending)
            return self._result("business", response, content_type="application/json", payload=result)

    def _plan_document(self, spec):
        ref = spec.get("plan_ref")
        with self._state_lock:
            self._check_active()
            if not isinstance(ref, str) or not re.fullmatch(r"PLAN-[a-f0-9]{32}", ref) or ref not in self._documents:
                _fail("route_not_confirmed")
            identifier, signature = self._documents[ref]
            if signature != self._catalogue_scope:
                _fail("route_not_confirmed")
        response = self._raw("GET", self._school_url(PLAN_DOCUMENT, {"id": identifier, "doType": "query"}), limit=PDF_LIMIT)
        state = _classify(response) if response.content_type != "application/pdf" else ("business" if response.status == 200 else
                "source_session_expired" if response.status in {401, 403, 901} or 300 <= response.status < 400 else "source_unavailable")
        if state != "business":
            _fail(state)
        if response.content_type != "application/pdf" or not response.body.startswith(b"%PDF-") or not response.body.rstrip().endswith(b"%%EOF"):
            _fail("schema_changed")
        return self._result("business", response, payload=response.body)

    def request(self, spec):
        with self._lock:
            with self._state_lock:
                if not self._authenticated or self._closed:
                    return self._result("awaiting_user_login")
                self._operation_epoch = self._epoch
                self._period_page_read = False
            try:
                spec = self._spec(spec)
                operation = spec["operation"]
                action = (lambda: self._plan_catalogue(spec)) if operation == "plan_catalogue" else ((lambda: self._plan_document(spec)) if operation == "plan_document" else (lambda: self._business(spec)))
                return self._with_refresh(action)
            except (SchoolHTTPError, ValueError, TypeError, UnicodeError) as error:
                code = error.code if isinstance(error, SchoolHTTPError) else "schema_changed"
                if code in {"source_session_expired", "source_subject_changed"}:
                    self.logout()
                if isinstance(spec, dict) and spec.get("operation") in {"plan_catalogue", "plan_document"}:
                    with self._state_lock:
                        self._documents.clear()
                        self._catalogue_scope = None
                return self._result(code)

    def get_captcha(self):
        with self._lock:
            with self._state_lock:
                login_pending = bool(not self._closed and self._login_execution and time.time() < self._login_expires)
                if self._closed or not self._authenticated and not login_pending:
                    if self._login_execution:
                        self.logout()
                    return self._result("awaiting_user_login")
                self._operation_epoch = self._epoch
            try:
                url = (SSO + CAS_CAPTCHA) if login_pending else (SCHOOL + CAPTCHA)
                response = self._raw("GET", url + "?" + urlencode({"time": int(time.time() * 1000)}), limit=IMAGE_LIMIT)
                if response.status in {401, 403} or 300 <= response.status < 400:
                    _fail("source_session_expired")
                if response.status != 200 or response.content_type not in {"image/png", "image/jpeg", "image/gif"}:
                    _fail("schema_changed")
                valid = ((response.content_type == "image/png" and response.body.startswith(b"\x89PNG\r\n\x1a\n")) or
                         (response.content_type == "image/jpeg" and response.body.startswith(b"\xff\xd8\xff")) or
                         (response.content_type == "image/gif" and response.body.startswith((b"GIF87a", b"GIF89a"))))
                if not valid:
                    _fail("schema_changed")
                with self._state_lock:
                    self._check_active()
                    if login_pending:
                        self._login_captcha_text = None
                        self._login_image_ready = True
                    else:
                        self._captcha_text = None
                        self._captcha_pending = True
                    return self._result("captcha_ready", response, image=response.body)
            except SchoolHTTPError as error:
                diagnostics = self._diagnostic_copy() if login_pending else None
                if error.code == "source_session_expired":
                    self.logout()
                result = self._result(error.code)
                if diagnostics:
                    result["diagnostics"] = diagnostics
                return result

    def submit_captcha(self, text):
        with self._state_lock:
            if self._closed:
                return self._result("awaiting_user_login")
            if self._login_execution and self._login_image_ready and time.time() < self._login_expires:
                if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9]{1,12}", text.strip()):
                    return self._result("captcha_required")
                self._login_captcha_text = text.strip()
                return self._result("captcha_submitted")
            if not self._authenticated:
                return self._result("awaiting_user_login")
            if not self._captcha_pending or not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9]{1,12}", text.strip()):
                return self._result("captcha_required")
            self._captcha_text = text.strip()
            return self._result("captcha_submitted")
