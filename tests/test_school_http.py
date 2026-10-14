"""Scripted HTTP protocol checks; no school credentials or network access."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import io
import json
import ssl
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError

from course_assistant.school_http import SchoolHTTPClient, SSO, SCHOOL, PROFILE_ROUTE, SERVICE, MENU, PLAN_MAJOR_OPTIONS, _Page
from course_assistant.school_period_times import TIMETABLE_PAGE, PERIOD_PAGE_LIMIT


class Response(io.BytesIO):
    def __init__(self, body=b"", *, status=200, content_type="text/html", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            content_type = "application/json"
        elif isinstance(body, str):
            body = body.encode()
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        for key, value in (headers or {}).items():
            for item in value if isinstance(value, list) else [value]:
                self.headers[key] = item

    def info(self):
        return self.headers


class ScriptOpener:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append({"method": request.method, "url": request.full_url,
                              "body": request.data, "headers": dict(request.header_items()), "timeout": timeout})
        if not self.script:
            raise AssertionError("unexpected scripted request")
        response = self.script.pop(0)
        if isinstance(response, Exception):
            raise response
        return response(request) if callable(response) else response


PROFILE = {"xh": "SYNTHETIC-SOURCE-A", "xm": "合成学生", "xymc": "合成学院",
           "zymc": "合成专业", "cohort": 2023}
LOGIN = '<form><input name="execution" value="synthetic-execution"/><input type="password"/><label>验证码</label><input name="authcode"/></form>'
KEY = {"modulus": "f" * 128, "exponent": "10001"}
SYNTHETIC_PASSWORD = "SYNTHETIC-ONLY-PASSWORD"
MENU_HTML = '<input type="hidden" id="sessionUserKey" value="SYNTHETIC-MENU-OPAQUE"/><input type="hidden" id="gnmkdmKey" value=""/>'


def service_script(profile=None):
    return [Response(status=302, headers={"Location": SCHOOL + SERVICE + "?ticket=SYNTHETIC-TICKET"}),
            Response(status=302, headers={"Set-Cookie": ["JSESSIONID=SYNTHETIC-BUSINESS; Path=/jwglxt/; Secure",
                                                       "route=SYNTHETIC-ROUTE; Path=/; Secure"]}),
            Response(MENU_HTML),
            Response(deepcopy(PROFILE if profile is None else profile))]


def login_script(profile=None):
    return [Response(LOGIN, headers={"Set-Cookie": "JSESSIONID=SYNTHETIC-CAS-FORM; Path=/cas; Secure"}),
            Response(b"false", content_type="application/json"),
            Response(KEY),
            Response(status=302, headers={"Set-Cookie": "iPlanetDirectoryPro=SYNTHETIC-SSO; Domain=.zju.edu.cn; Path=/; Secure"})] + service_script(profile)


def rows(*, subject=None, year="2026-2027"):
    payload = {"items": [{"kcmc": "合成课程", "xkkh": f"({year}-1)-SYNTHETIC-COURSE", "xf": "2", "cj": "85"}], "total": 1, "pageNo": 1}
    if subject:
        payload["xh"] = subject
    return payload


class SchoolHTTPTests(unittest.TestCase):
    def client(self, script, profile=None):
        self.opener = ScriptOpener(login_script(profile) + list(script))
        self.http = SchoolHTTPClient(opener=self.opener)
        result = self.http.login("SYNTHETIC-LOGIN-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        return self.http

    def request(self, operation="academic", target_term="2026-2027-AW", **fields):
        return self.http.request({"operation": operation, "target_term": target_term, "page": 1, "page_size": 20, **fields})

    def test_identity_comes_from_own_profile_not_submitted_account(self):
        client = self.client([])
        self.assertEqual(client._profile["source_subject_id"], "SYNTHETIC-SOURCE-A")
        self.assertNotEqual(client._profile["source_subject_id"], "SYNTHETIC-LOGIN-ACCOUNT")
        self.assertEqual(urlsplit(self.opener.requests[-1]["url"]).path, PROFILE_ROUTE)
        self.assertEqual(parse_qs(urlsplit(self.opener.requests[-1]["url"]).query), {"su": ["SYNTHETIC-MENU-OPAQUE"]})
        self.assertNotIn("SYNTHETIC-LOGIN-ACCOUNT", self.opener.requests[-1]["url"])
        self.assertEqual(self.opener.requests[-1]["method"], "POST")
        self.assertEqual(self.opener.requests[-1]["body"], b"")
        self.assertEqual(self.opener.requests[-1]["headers"]["Origin"], SCHOOL)
        self.assertNotIn("SYNTHETIC-MENU-OPAQUE", str(self.http._result("authenticated", profile=self.http._profile)))

    def test_actual_same_menu_iframe_is_followed_once_without_invented_role(self):
        script = login_script()
        script[-2] = Response('<iframe src="' + MENU + '?jsdm=06&amp;ignoreZoom=1"></iframe>')
        script.insert(-1, Response(MENU_HTML.replace('id="gnmkdmKey" value=""', 'id="gnmkdmKey" value="N12345"')))
        opener = ScriptOpener(script)
        client = SchoolHTTPClient(opener=opener)
        self.assertEqual(client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)["state"], "authenticated")
        menus = [request for request in opener.requests if urlsplit(request["url"]).path == MENU]
        self.assertEqual(len(menus), 2)
        self.assertEqual(urlsplit(menus[0]["url"]).query, "")
        self.assertEqual(parse_qs(urlsplit(menus[1]["url"]).query), {"jsdm": ["06"], "ignoreZoom": ["1"]})
        self.assertEqual(parse_qs(urlsplit(opener.requests[-1]["url"]).query), {"su": ["SYNTHETIC-MENU-OPAQUE"], "gnmkdm": ["N12345"]})

    def test_opaque_non_function_menu_context_is_reproduced_from_source(self):
        script = login_script()
        script[-2] = Response(MENU_HTML.replace('id="gnmkdmKey" value=""', 'id="gnmkdmKey" value="INDEX-MENU"'))
        opener = ScriptOpener(script)
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        self.assertEqual(parse_qs(urlsplit(opener.requests[-1]["url"]).query)["gnmkdm"], ["INDEX-MENU"])
        self.assertNotIn("INDEX-MENU", str(result))
        client.close()
        self.assertIsNone(client._session_menu_code)

    def test_callback_selected_menu_role_is_preserved_without_input_guess(self):
        script = login_script()
        script[-3].headers["Location"] = MENU + "?jsdm=06"
        opener = ScriptOpener(script)
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        menus = [request for request in opener.requests if urlsplit(request["url"]).path == MENU]
        self.assertEqual(len(menus), 1)
        self.assertEqual(parse_qs(urlsplit(menus[0]["url"]).query), {"jsdm": ["06"]})

    def test_own_callback_matrix_session_and_cachebuster_are_stripped(self):
        script = login_script()
        script[-3].headers["Location"] = MENU + ";jsessionid=SYNTHETIC-MATRIX-SESSION?jsdm=06&_t=1234567890123"
        opener = ScriptOpener(script)
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        menus = [request for request in opener.requests if urlsplit(request["url"]).path == MENU]
        self.assertEqual(len(menus), 1)
        self.assertEqual(parse_qs(urlsplit(menus[0]["url"]).query), {"jsdm": ["06"]})
        self.assertFalse(any("MATRIX-SESSION" in request["url"] or "_t=" in request["url"] for request in opener.requests))
        self.assertNotIn("MATRIX-SESSION", str(result))

    def test_dynamic_viewport_wrapper_uses_only_known_rendering_switch(self):
        for role in (None, "06"):
            with self.subTest(role=role):
                script = login_script()
                if role:
                    script[-3].headers["Location"] = MENU + "?jsdm=" + role
                script[-2] = Response('<html><script>var ignoreZoom = false; createViewportFrame();</script><div id="viewport"></div></html>')
                script.insert(-1, Response(MENU_HTML))
                opener = ScriptOpener(script)
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], "authenticated")
                menus = [request for request in opener.requests if urlsplit(request["url"]).path == MENU]
                self.assertEqual(len(menus), 2)
                expected = {"ignoreZoom": ["1"], **({"jsdm": [role]} if role else {})}
                self.assertEqual(parse_qs(urlsplit(menus[-1]["url"]).query), expected)

    def test_rendering_switch_does_not_replace_foreign_or_injected_iframe(self):
        for frame in ("https://foreign.invalid/", MENU + "?jsdm=06&ticket=SYNTHETIC", MENU + "?ignoreZoom=%0a"):
            with self.subTest(frame=frame):
                body = '<script>var ignoreZoom = false;</script><iframe src="' + frame.replace('&', '&amp;') + '"></iframe>'
                opener = ScriptOpener(login_script()[:-2] + [Response(body)])
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], "route_not_confirmed")
                self.assertEqual(len([request for request in opener.requests if urlsplit(request["url"]).path == MENU]), 1)
                self.assertIsNone(client._session_user_key)

    def test_dynamic_wrapper_follow_budget_stops_if_second_page_has_no_key(self):
        body = '<script>var ignoreZoom = false;</script>'
        opener = ScriptOpener(login_script()[:-2] + [Response(body), Response(body)])
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "route_not_confirmed")
        self.assertEqual(len([request for request in opener.requests if urlsplit(request["url"]).path == MENU]), 2)
        self.assertIsNone(client._session_user_key)

    def test_foreign_or_unconfirmed_callback_menu_location_is_not_followed(self):
        for location in ("https://foreign.invalid" + MENU, MENU + "?jsdm=06&su=SYNTHETIC-OTHER", MENU + "?ticket=SYNTHETIC",
                         MENU + ";jsessionid=SYNTHETIC;other=bad?jsdm=06", MENU + "?jsdm=06&_t=not-numeric"):
            with self.subTest(location=location):
                script = login_script()
                script[-3].headers["Location"] = location
                opener = ScriptOpener(script)
                client = SchoolHTTPClient(opener=opener)
                self.assertEqual(client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)["state"], "authenticated")
                menus = [request for request in opener.requests if urlsplit(request["url"]).path == MENU]
                self.assertEqual(len(menus), 1)
                self.assertEqual(urlsplit(menus[0]["url"]).query, "")
                self.assertFalse(any("foreign.invalid" in request["url"] for request in opener.requests))

    def test_foreign_or_unconfirmed_menu_iframe_never_reaches_wire(self):
        for frame in ('https://foreign.invalid/' + MENU, MENU + '?su=SYNTHETIC-OTHER', MENU + '?jsdm=06&ignoreZoom=1&ticket=SYNTHETIC', MENU + '?jsdm=06&jsdm=07'):
            with self.subTest(frame=frame):
                script = login_script()[:-2] + [Response('<iframe src="' + frame.replace('&', '&amp;') + '"></iframe>')]
                opener = ScriptOpener(script)
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], "route_not_confirmed")
                self.assertEqual(result["diagnostics"]["failure_step"], "menu_context")
                self.assertEqual(len([request for request in opener.requests if urlsplit(request["url"]).path == MENU]), 1)
                self.assertEqual(len(opener.script), 0)
                self.assertIsNone(client._session_user_key)

    def test_menu_requires_authenticated_context_and_explicit_own_key(self):
        for body, state in ((LOGIN, "source_session_expired"), ("<div>SYNTHETIC-EMPTY-MENU</div>", "route_not_confirmed")):
            with self.subTest(state=state):
                opener = ScriptOpener(login_script()[:-2] + [Response(body)])
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], state)
                self.assertEqual(result["diagnostics"], {"failure_step": "menu_context", "http_status": 200, "transport_error": None})
                self.assertFalse(client._authenticated)
                self.assertIsNone(client._session_user_key)
                self.assertFalse(any(urlsplit(request["url"]).path == PROFILE_ROUTE for request in opener.requests))

    def test_cas_form_cookie_public_key_rsa_and_business_cookie_pair(self):
        self.client([Response(rows())])
        result = self.request()
        self.assertEqual(result["state"], "business")
        key_headers, submit = self.opener.requests[2]["headers"], self.opener.requests[3]
        self.assertIn("SYNTHETIC-CAS-FORM", key_headers["Cookie"])
        form = parse_qs(submit["body"].decode())
        self.assertEqual(form["execution"], ["synthetic-execution"])
        expected = format(pow(int.from_bytes(SYNTHETIC_PASSWORD.encode(), "big"), 65537, int("f" * 128, 16)), "x").zfill(128)
        self.assertEqual(form["password"], [expected])
        self.assertNotIn(SYNTHETIC_PASSWORD, submit["body"].decode())
        self.assertIn("SYNTHETIC-BUSINESS", self.opener.requests[-1]["headers"]["Cookie"])
        self.assertIn("SYNTHETIC-ROUTE", self.opener.requests[-1]["headers"]["Cookie"])
        self.assertNotIn("SYNTHETIC-CAS-FORM", self.opener.requests[-1]["headers"]["Cookie"])
        self.assertNotIn(SYNTHETIC_PASSWORD, str(vars(self.http)))
        self.assertNotIn("ticket", str(result).lower())

    def test_missing_identity_does_not_authenticate(self):
        opener = ScriptOpener(login_script({"xm": "合成学生"}))
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-LOGIN-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "identity_not_observed")
        self.assertFalse(client._authenticated)
        self.assertEqual(list(client._cookies), [])
        self.assertEqual(client.request({"operation": "academic"})["state"], "awaiting_user_login")

    def test_profile_html_preserves_labels_and_explicit_admission_date(self):
        html = '<table><tr><td>学号：</td><td>SYNTHETIC-SOURCE-A</td><td>姓名</td><td>合成学生</td></tr><tr><td>学院</td><td>合成学院</td><td>类（专业）</td><td>合成专业</td></tr><tr><td>入学日期</td><td>20230901</td></tr></table>'
        script = login_script()[:-1] + [Response(html)]
        client = SchoolHTTPClient(opener=ScriptOpener(script))
        result = client.login("SYNTHETIC-LOGIN-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        self.assertEqual(result["profile"]["cohort"], 2023)
        self.assertEqual(result["profile"]["major_or_category_raw"], "合成专业")
        self.assertIsNone(result["profile"]["major_name"])

    def test_invalid_credentials_never_reach_network(self):
        opener = ScriptOpener([])
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", "")
        self.assertEqual(result["state"], "invalid_credentials")
        self.assertEqual(opener.requests, [])

    def test_profile_div_labels_require_one_adjacent_value(self):
        html = '<div><label>学号：</label><div><p>SYNTHETIC-SOURCE-A</p></div></div><div><label>姓名：</label><span>合成学生</span></div><div><label>入学日期：</label><p>20230901</p></div><div><label>学院：</label><div>合成学院</div></div><div><label>类（专业）：</label><p>合成专业</p></div><div><label>联系方式</label><p>SYNTHETIC-PRIVATE-CONTACT</p></div>'
        script = login_script()[:-1] + [Response(html)]
        client = SchoolHTTPClient(opener=ScriptOpener(script))
        result = client.login("SYNTHETIC-LOGIN-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        self.assertEqual(result["profile"]["cohort"], 2023)
        self.assertNotIn("PRIVATE-CONTACT", str(result))

    def test_major_history_scope_does_not_invent_term_filters(self):
        source = rows(year="2023-2024")
        source["items"].append({"kcmc": "合成另一课程", "xkkh": "(2025-2026-2)-SYNTHETIC-OTHER-COURSE", "xf": "3", "cj": "90"})
        source["total"] = 2
        self.client([Response(source)])
        before = len(self.opener.requests)
        self.assertEqual(self.request("major_academic")["state"], "route_not_confirmed")
        self.assertEqual(len(self.opener.requests), before)
        result = self.request("major_academic", "ALL-HISTORY")
        self.assertEqual(result["state"], "business")
        self.assertEqual(len(result["payload"]["items"]), 2)
        self.assertEqual(parse_qs(self.opener.requests[-1]["body"].decode()), {"queryModel.currentPage": ["1"], "queryModel.showCount": ["20"]})
        self.assertEqual(parse_qs(urlsplit(self.opener.requests[-1]["url"]).query), {"doType": ["query"], "su": ["SYNTHETIC-MENU-OPAQUE"]})

    def test_bad_password_response_is_sanitized(self):
        opener = ScriptOpener(login_script()[:3] + [Response('invalid password SYNTHETIC-DO-NOT-RETURN')])
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "invalid_credentials")
        self.assertNotIn("DO-NOT-RETURN", str(result))
        self.assertEqual(list(client._cookies), [])

    def test_foreign_callback_and_duplicate_ticket_are_never_followed(self):
        for location in ("https://foreign.invalid/?ticket=SYNTHETIC", SCHOOL + SERVICE + "?ticket=A&ticket=B", SCHOOL + SERVICE + "?ticket=A&url=foreign"):
            with self.subTest(location=location):
                opener = ScriptOpener(login_script()[:4] + [Response(status=302, headers={"Location": location})])
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], "route_not_confirmed")
                self.assertEqual(len(opener.requests), 5)

    def test_business_cookies_must_be_paired(self):
        opener = ScriptOpener(login_script()[:5] + [Response(status=302, headers={"Set-Cookie": "JSESSIONID=SYNTHETIC-ONLY; Path=/; Secure"})])
        client = SchoolHTTPClient(opener=opener)
        self.assertEqual(client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)["state"], "source_session_expired")

    def test_changed_public_key_shape_fails_with_sanitized_code(self):
        opener = ScriptOpener(login_script()[:2] + [Response(b'["SYNTHETIC-DO-NOT-RETURN"]', content_type="application/json")])
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "schema_changed")
        self.assertNotIn("DO-NOT-RETURN", str(result))
        self.assertEqual(list(client._cookies), [])

    def test_login_transport_diagnostics_are_literal_and_never_echo_exception(self):
        secret = "https://synthetic.invalid/?ticket=SYNTHETIC-DO-NOT-RETURN"
        for failure, phase, category, prefix in (
                (URLError(TimeoutError(secret)), "cas_form", "timeout", []),
                (URLError(ssl.SSLError(secret)), "cas_pubkey", "tls", login_script()[:2]),
                (OSError(secret), "cas_captcha_status", "network", login_script()[:1])):
            with self.subTest(category=category):
                opener = ScriptOpener(prefix + [failure])
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], "source_unavailable")
                self.assertEqual(result["diagnostics"], {"failure_step": phase, "http_status": None, "transport_error": category})
                self.assertNotIn("DO-NOT-RETURN", str(result))
                self.assertNotIn("https", str(result))
                self.assertIsNone(client._diagnostics)
                self.assertEqual(list(client._cookies), [])

    def test_profile_http_failure_diagnostics_keep_status_without_source_body(self):
        opener = ScriptOpener(login_script()[:-1] + [Response("SYNTHETIC-DO-NOT-RETURN", status=500,
                headers={"Location": "https://synthetic.invalid/?ticket=SECRET", "Set-Cookie": "JSESSIONID=SYNTHETIC-DO-NOT-RETURN; Path=/; Secure"})])
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "source_unavailable")
        self.assertEqual(result["diagnostics"], {"failure_step": "own_profile", "http_status": 500, "transport_error": None})
        self.assertNotIn("DO-NOT-RETURN", str(result))
        self.assertNotIn("Location", str(result))
        self.assertIsNone(client._diagnostics)
        self.assertEqual(list(client._cookies), [])

    def test_nonstandard_school_http_error_status_is_preserved_safely(self):
        for status, state in ((901, "source_session_expired"), (902, "source_unavailable"), (999, "source_unavailable")):
            with self.subTest(status=status):
                headers = Message()
                headers["Content-Type"] = "text/html"
                error = HTTPError(SCHOOL + PROFILE_ROUTE, status, "SYNTHETIC-DO-NOT-RETURN", headers,
                                  io.BytesIO(b"SYNTHETIC-DO-NOT-RETURN"))
                opener = ScriptOpener(login_script()[:-1] + [error])
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], state)
                self.assertEqual(result["diagnostics"], {"failure_step": "own_profile", "http_status": status, "transport_error": None})
                self.assertNotIn("DO-NOT-RETURN", str(result))
                self.assertIsNone(client._diagnostics)
                self.assertEqual(list(client._cookies), [])

    def test_invalid_login_does_not_reuse_previous_request_diagnostic(self):
        client = self.client([])
        result = client.login("SYNTHETIC-ACCOUNT", "")
        self.assertEqual(result["state"], "invalid_credentials")
        self.assertNotIn("diagnostics", result)

    def test_login_captcha_failure_diagnostics_remain_source_free(self):
        opener = ScriptOpener([Response(LOGIN), Response(b"true", content_type="application/json"),
                              Response("SYNTHETIC-DO-NOT-RETURN", status=500)])
        client = SchoolHTTPClient(opener=opener)
        self.assertEqual(client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)["state"], "captcha_required")
        result = client.get_captcha()
        self.assertEqual(result["diagnostics"], {"failure_step": "login_captcha", "http_status": 500, "transport_error": None})
        self.assertNotIn("DO-NOT-RETURN", str(result))
        client.close()

    def test_long_opaque_cas_execution_is_preserved_in_same_form_only(self):
        prefix = "SYNTHETIC-OPAQUE:"
        execution = prefix + "A" * (3937 - len(prefix) - 3) + "+/="
        self.assertEqual(len(execution), 3937)
        html = LOGIN.replace("synthetic-execution", execution)
        script = login_script()
        script[0] = Response(html, headers={"Set-Cookie": "JSESSIONID=SYNTHETIC-CAS-FORM; Path=/cas; Secure"})
        opener = ScriptOpener(script)
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "authenticated")
        self.assertEqual(parse_qs(opener.requests[3]["body"].decode())["execution"], [execution])
        self.assertNotIn(execution, str(result))
        self.assertIsNone(client._login_execution)

    def test_oversized_or_control_form_state_never_submits_password(self):
        for execution in ("A" * 8193, "SYNTHETIC\nSTATE", "SYNTHETIC\x00STATE"):
            with self.subTest(length=len(execution)):
                html = LOGIN.replace("synthetic-execution", execution)
                opener = ScriptOpener([Response(html)])
                client = SchoolHTTPClient(opener=opener)
                result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
                self.assertEqual(result["state"], "schema_changed")
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(opener.requests[0]["method"], "GET")
                self.assertNotIn(SYNTHETIC_PASSWORD, str(vars(client)))
                self.assertEqual(list(client._cookies), [])

    def test_academic_map_and_projection_strip_unknown_fields(self):
        source = rows()
        source["secret_field"] = "SYNTHETIC-DO-NOT-RETURN"
        source["items"][0]["cookie"] = "SYNTHETIC-DO-NOT-RETURN"
        self.client([Response(source)])
        result = self.request()
        self.assertEqual(result["state"], "business")
        request = self.opener.requests[-1]
        self.assertEqual(parse_qs(urlsplit(request["url"]).query), {"doType": ["query"], "gnmkdm": ["N5083"], "su": ["SYNTHETIC-MENU-OPAQUE"]})
        self.assertEqual(parse_qs(request["body"].decode()), {"xn": ["2026-2027"], "xq": ["1"], "queryModel.currentPage": ["1"], "queryModel.showCount": ["20"]})
        self.assertNotIn("DO-NOT-RETURN", str(result))

    def test_source_field_diagnostics_locate_projection_loss_without_source_values(self):
        source = {"kbList": [{"kcmc": "SYNTHETIC-PRIVATE-COURSE", "sfqd": "1", "skdd": "SYNTHETIC-PRIVATE-PLACE",
                              "jsmc": "SYNTHETIC-UNVERIFIED-ALIAS",
                              "meetings": [{"cdmc": None, "xqj": 2, "sfqd": False}],
                              "SYNTHETIC-UNKNOWN-KEY": "SYNTHETIC-SECRET"},
                             {"kcmc": "SYNTHETIC-OTHER", "sfqd": "", "cdmc": ""}]}
        self.client([Response(source), Response('<div>课表查询</div>')])
        result = self.request("timetable")
        self.assertEqual(result["state"], "business")
        diagnostics = result["source_field_diagnostics"]
        self.assertEqual((diagnostics["domain"], diagnostics["source"]["row_count"], diagnostics["source"]["meeting_count"]),
                         ("timetable", 2, 1))
        self.assertEqual(diagnostics["source"]["fields"]["skdd"], {"present": 1, "empty": 0, "types": {"string": 1}})
        self.assertEqual(diagnostics["projection"]["fields"]["skdd"], diagnostics["source"]["fields"]["skdd"])
        self.assertNotIn("jsmc", diagnostics["projection"]["fields"])
        self.assertEqual(result["payload"]["kbList"][0]["skdd"], "SYNTHETIC-PRIVATE-PLACE")
        self.assertEqual(diagnostics["source"]["fields"]["sfqd"], {"present": 3, "empty": 1, "types": {"string": 2, "boolean": 1}})
        self.assertEqual(diagnostics["projection"]["fields"]["cdmc"], {"present": 2, "empty": 2, "types": {"null": 1, "string": 1}})
        self.assertNotIn("SYNTHETIC", json.dumps(diagnostics))

    def test_actual_jqgrid_legacy_zero_page_alias_is_removed_only_with_current_page(self):
        for operation, term in (("academic", "2026-2027-AW"), ("major_academic", "ALL-HISTORY"), ("exams", "2026-2027-A")):
            with self.subTest(operation=operation):
                source = rows()
                source.update(pageNo=0, currentPage=1, showCount=20, totalCount=1, totalResult=1)
                self.client([Response(source)])
                result = self.request(operation, term)
                self.assertEqual(result["state"], "business")
                self.assertNotIn("pageNo", result["payload"])
                self.assertEqual(result["payload"]["currentPage"], 1)
                self.assertEqual(result["payload"]["totalCount"], 1)
                self.assertEqual(result["payload"]["totalResult"], 1)

    def test_positive_page_conflict_and_unanchored_zero_alias_are_rejected(self):
        for fields in ({"pageNo": 2, "currentPage": 1}, {"pageNo": 0}, {"pageNo": 0, "currentPage": 0},
                       {"pageNo": "0", "currentPage": 1}, {"pageNo": False, "currentPage": 1}):
            with self.subTest(fields=fields):
                source = rows()
                source.update(fields)
                self.client([Response(source)])
                result = self.request()
                self.assertEqual(result["state"], "schema_changed")
                self.assertNotIn("payload", result)

    def test_timetable_uses_actual_list_mode_and_combined_term(self):
        self.client([Response({"kbList": [{"kcmc": "合成课", "kcb": "合成班", "xxq": "秋、冬"}]}), Response('<div>课表查询</div>')])
        result = self.request("timetable")
        self.assertEqual(result["state"], "business")
        self.assertEqual(parse_qs(self.opener.requests[-2]["body"].decode()), {"xnm": ["2026-2027"], "xqm": ["1|秋、冬"], "xqmmc": ["秋、冬"], "xxqf": ["1"], "xsfs": ["1"]})
        self.assertEqual(result["payload"]["period_times"], [])
        self.assertEqual(result["payload"]["period_times_source"]["state"], "not_available")

    def test_timetable_clock_reads_fixed_page_once_with_same_menu_context(self):
        html = '<table><tr><th colspan="2">时间</th><th>星期一</th><th>星期二</th></tr><tr><td>1</td><td>07:10—07:55</td><td>合成课 考试13:00—14:00</td></tr></table>'
        self.client([Response({"kbList": [{"kcmc": "合成课", "kcb": "合成班"}]}), Response(html)])
        result = self.request("timetable")
        self.assertEqual(result["state"], "business")
        self.assertEqual(result["payload"]["period_times"], [{"period": 1, "start_time": "07:10", "end_time": "07:55"}])
        clocks = [request for request in self.opener.requests if urlsplit(request["url"]).path == TIMETABLE_PAGE]
        self.assertEqual(len(clocks), 1)
        self.assertEqual(clocks[0]["method"], "GET")
        self.assertIsNone(clocks[0]["body"])
        self.assertEqual(parse_qs(urlsplit(clocks[0]["url"]).query), {"gnmkdm": ["N253508"], "su": ["SYNTHETIC-MENU-OPAQUE"]})
        self.assertNotIn("SYNTHETIC-MENU-OPAQUE", str(result))
        self.assertNotIn("13:00", str(result))

    def test_optional_clock_failure_and_page_permission_preserve_successful_list(self):
        cases = [(URLError('SYNTHETIC-NETWORK'), "source_unavailable"),
                 (Response('x' * (PERIOD_PAGE_LIMIT + 1)), "schema_changed"),
                 (Response('<div>无权访问</div>', status=403), "permission_denied"),
                 (Response({"unrelated": []}), "schema_changed")]
        for page, state in cases:
            with self.subTest(state=state):
                self.client([Response({"kbList": [{"kcmc": "合成课"}]}), page])
                result = self.request("timetable")
                self.assertEqual(result["state"], "business")
                self.assertEqual(result["payload"]["kbList"], [{"kcmc": "合成课"}])
                self.assertEqual(result["payload"]["period_times"], [])
                self.assertEqual(result["payload"]["period_times_source"]["state"], state)
                self.assertEqual(len([request for request in self.opener.requests if urlsplit(request["url"]).path == TIMETABLE_PAGE]), 1)

    def test_clock_in_weekday_column_does_not_replace_empty_period_clock_cell(self):
        html = '<table><tr><th colspan="2">时间</th><th>星期一</th><th>星期二</th></tr><tr><td>1</td><td></td><td>13:00—14:00</td></tr></table>'
        self.client([Response({"kbList": [{"kcmc": "合成课"}]}), Response(html)])
        result = self.request("timetable")
        self.assertEqual(result["state"], "business")
        self.assertEqual(result["payload"]["period_times"], [])
        self.assertEqual(result["payload"]["period_times_source"]["state"], "not_available")
        self.assertNotIn("13:00", str(result))

    def test_clock_subject_change_and_challenge_do_not_return_business_records(self):
        for page, state in ((Response('<input name="xh" value="SYNTHETIC-SOURCE-B">'), "source_subject_changed"),
                            (Response('<input name="captcha_value"><label>验证码</label>'), "captcha_required")):
            self.client([Response({"kbList": [{"kcmc": "合成课"}]}), page])
            result = self.request("timetable")
            self.assertEqual(result["state"], state)
            self.assertNotIn("payload", result)

    def test_clock_login_page_refresh_is_bounded_and_never_a_clock_source(self):
        self.client([Response({"kbList": [{"kcmc": "合成课"}]}), Response(LOGIN)] + service_script() +
                    [Response({"kbList": [{"kcmc": "合成课"}]})])
        result = self.request("timetable")
        self.assertEqual(result["state"], "business")
        self.assertEqual(result["payload"]["period_times"], [])
        self.assertEqual(result["payload"]["period_times_source"]["state"], "source_unavailable")
        self.assertEqual(len([request for request in self.opener.requests if urlsplit(request["url"]).path == TIMETABLE_PAGE]), 1)

    def test_revoked_epoch_during_clock_page_read_cannot_publish_earlier_business_rows(self):
        def close_during_read(request):
            self.http.close()
            return Response('<table><tr><th colspan="2">时间</th><th>星期一</th><th>星期二</th></tr><tr><td>1</td><td>07:10—07:55</td></tr></table>')
        self.client([Response({"kbList": [{"kcmc": "SYNTHETIC-PRIVATE-COURSE"}]}), close_during_read])
        result = self.request("timetable")
        self.assertEqual(result["state"], "source_session_expired")
        self.assertNotIn("payload", result)
        self.assertNotIn("SYNTHETIC-PRIVATE-COURSE", str(result))

    def test_exam_uses_single_season_actual_parameter_map(self):
        self.client([Response({"items": [{"kcmc": "合成课", "kssj": "未知", "xxq": "冬"}]})])
        self.assertEqual(self.request("exams", "2026-2027-W")["state"], "business")
        self.assertEqual(parse_qs(self.opener.requests[-1]["body"].decode())["xnxq"], ["(2026-2027-1)-"])
        self.assertEqual(parse_qs(self.opener.requests[-1]["body"].decode())["xxq"], ["冬"])
        before = len(self.opener.requests)
        self.assertEqual(self.request("exams")["state"], "route_not_confirmed")
        self.assertEqual(len(self.opener.requests), before)

    def test_session_refresh_once_uses_sso_and_checks_subject(self):
        client = self.client([Response(LOGIN)] + service_script() + [Response(rows())])
        epoch = client.session_epoch
        result = self.request()
        self.assertEqual(result["state"], "business")
        self.assertNotEqual(client.session_epoch, epoch)
        self.assertEqual(len([r for r in self.opener.requests if urlsplit(r["url"]).path == "/cas/login" and r["method"] == "POST"]), 1)
        self.assertEqual(len([r for r in self.opener.requests if urlsplit(r["url"]).path == PROFILE_ROUTE]), 2)

    def test_refresh_cannot_cross_source_identity(self):
        other = dict(PROFILE, xh="SYNTHETIC-SOURCE-B")
        client = self.client([Response({"kickout": 1})] + service_script(other))
        result = self.request()
        self.assertEqual(result["state"], "source_subject_changed")
        self.assertEqual(list(client._cookies), [])
        self.assertFalse(client._authenticated)

    def test_refresh_failure_stops_without_password_replay(self):
        client = self.client([Response(status=401), Response(LOGIN)])
        result = self.request()
        self.assertEqual(result["state"], "source_session_expired")
        self.assertFalse(client._authenticated)
        self.assertEqual(len([r for r in self.opener.requests if r["method"] == "POST" and urlsplit(r["url"]).path == "/cas/login"]), 1)

    def test_second_expired_business_response_stops_after_one_refresh(self):
        self.client([Response(status=403)] + service_script() + [Response({"data": {"kickout": "1"}})])
        result = self.request()
        self.assertEqual(result["state"], "source_session_expired")
        self.assertEqual(len([r for r in self.opener.requests if r["method"] == "GET" and urlsplit(r["url"]).path == "/cas/login"]), 3)

    def test_captcha_plain_json_and_image_remain_same_session(self):
        for challenge in (Response("captcha_error", content_type="text/plain"), Response({"msg": "验证码错误", "success": False})):
            with self.subTest(challenge=challenge):
                self.client([challenge, Response(b"\x89PNG\r\n\x1a\nSYNTHETIC-IMAGE", content_type="image/png"), Response({"kbList": [{"kcmc": "合成课", "kcb": "合成班"}]}), Response('<div>课表查询</div>')])
                epoch = self.http.session_epoch
                self.assertEqual(self.request("timetable")["state"], "captcha_required")
                image = self.http.get_captcha()
                self.assertEqual(image["state"], "captcha_ready")
                self.assertEqual(self.http.submit_captcha("AB12")["state"], "captcha_submitted")
                self.assertEqual(self.request("timetable")["state"], "business")
                self.assertEqual(self.http.session_epoch, epoch)
                self.assertEqual(parse_qs(self.opener.requests[-2]["body"].decode())["captcha_value"], ["AB12"])

    def test_login_challenge_does_not_keep_password_or_create_identity(self):
        opener = ScriptOpener([Response(LOGIN + '<label>滑块验证码</label>')])
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "interactive_verification_required")
        self.assertEqual(len(opener.requests), 1)
        self.assertNotIn(SYNTHETIC_PASSWORD, str(vars(client)))
        self.assertEqual(list(client._cookies), [])

    def test_sso_image_captcha_continuation_uses_same_form_and_fresh_credentials(self):
        script = [Response(LOGIN, headers={"Set-Cookie": "JSESSIONID=SYNTHETIC-CAS-FORM; Path=/cas; Secure"}),
                  Response(b"true", content_type="application/json"),
                  Response(b"\x89PNG\r\n\x1a\nSYNTHETIC-IMAGE", content_type="image/png"),
                  Response(KEY),
                  Response(status=302, headers={"Set-Cookie": "iPlanetDirectoryPro=SYNTHETIC-SSO; Domain=.zju.edu.cn; Path=/; Secure"})] + service_script()
        opener = ScriptOpener(script)
        client = SchoolHTTPClient(opener=opener)
        result = client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
        self.assertEqual(result["state"], "captcha_required")
        self.assertNotIn(SYNTHETIC_PASSWORD, str(vars(client)))
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(client.get_captcha()["state"], "captcha_ready")
        self.assertEqual(urlsplit(opener.requests[-1]["url"]).path, "/cas/kaptcha")
        self.assertEqual(client.submit_captcha("AB12")["state"], "captcha_submitted")
        result = client.login("SYNTHETIC-ACCOUNT", "SYNTHETIC-NEW-PASSWORD")
        self.assertEqual(result["state"], "authenticated")
        submitted = opener.requests[4]
        form = parse_qs(submitted["body"].decode())
        self.assertEqual(form["authcode"], ["AB12"])
        self.assertEqual(form["execution"], ["synthetic-execution"])
        self.assertIn("SYNTHETIC-CAS-FORM", submitted["headers"]["Cookie"])
        self.assertIsNone(client._login_execution)
        self.assertIsNone(client._login_captcha_text)

    def test_sso_captcha_pending_form_expires_without_saved_password(self):
        opener = ScriptOpener([Response(LOGIN), Response(b"true", content_type="application/json")])
        client = SchoolHTTPClient(opener=opener)
        self.assertEqual(client.login("SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)["state"], "captcha_required")
        client._login_expires = 0
        self.assertEqual(client.get_captcha()["state"], "awaiting_user_login")
        self.assertIsNone(client._login_execution)

    def test_invalid_image_and_missing_challenge_cannot_submit(self):
        self.client([Response(b"<html>login</html>", content_type="image/png")])
        self.assertEqual(self.http.submit_captcha("AB12")["state"], "captcha_required")
        self.assertEqual(self.http.get_captcha()["state"], "schema_changed")

    def test_arbitrary_routes_identifiers_and_invalid_specs_cannot_send(self):
        self.client([])
        for spec in ({"operation": "academic", "target_term": "2026-2027-AW", "url": "https://foreign.invalid"},
                     {"operation": "academic", "target_term": "2026-2027-AW", "student_number": "SYNTHETIC-B"},
                     {"operation": "course_update"}, [], {"operation": "academic", "target_term": "2026-2026-AW"}):
            with self.subTest(spec=spec):
                before = len(self.opener.requests)
                self.assertEqual(self.http.request(spec)["state"], "route_not_confirmed")
                self.assertEqual(len(self.opener.requests), before)

    def test_scope_wrong_year_and_subject_change_fail_without_body(self):
        for payload, state in ((rows(year="2025-2026"), "schema_changed"), (rows(subject="SYNTHETIC-SOURCE-B"), "source_subject_changed")):
            with self.subTest(payload=payload):
                self.client([Response(payload)])
                result = self.request()
                self.assertEqual(result["state"], state)
                self.assertNotIn("payload", result)

    def test_logout_epoch_cookies_documents_and_captcha_are_cleared(self):
        client = self.client([])
        epoch = client.session_epoch
        client._documents["PLAN-synthetic"] = ("synthetic", "scope")
        client._captcha_text = "AB12"
        client.logout()
        self.assertNotEqual(client.session_epoch, epoch)
        self.assertEqual((list(client._cookies), client._documents, client._captcha_text), ([], {}, None))
        self.assertEqual(client.get_profile()["state"], "awaiting_user_login")
        self.assertIsNone(client._session_user_key)
        self.assertIsNone(client._session_menu_code)

    def test_late_menu_context_cannot_restore_revoked_key_or_identity(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(request):
            entered.set()
            self.assertTrue(release.wait(3))
            return Response(MENU_HTML)
        opener = ScriptOpener(login_script()[:-2] + [blocked])
        client = SchoolHTTPClient(opener=opener)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(client.login, "SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
            self.assertTrue(entered.wait(1))
            try:
                pool.submit(client.close).result(timeout=.5)
            finally:
                release.set()
            result = pending.result(timeout=1)
        self.assertEqual(result["state"], "source_session_expired")
        self.assertNotIn("SYNTHETIC-MENU-OPAQUE", str(result))
        self.assertIsNone(client._session_user_key)
        self.assertIsNone(client._session_menu_code)
        self.assertFalse(client._authenticated)
        self.assertEqual(list(client._cookies), [])

    def test_close_and_epoch_reads_do_not_wait_for_blocked_business_socket(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(request):
            entered.set()
            self.assertTrue(release.wait(3))
            return Response(rows(), headers={"Set-Cookie": "JSESSIONID=SYNTHETIC-LATE-COOKIE; Path=/; Secure"})
        client = self.client([blocked])
        epoch = client.session_epoch
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = pool.submit(self.request)
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(pool.submit(lambda: client.session_epoch).result(timeout=.5), epoch)
                pool.submit(client.close).result(timeout=.5)
                self.assertNotEqual(client.session_epoch, epoch)
            finally:
                release.set()
            result = pending.result(timeout=1)
        self.assertEqual(result["state"], "source_session_expired")
        self.assertNotIn("payload", result)
        self.assertEqual(list(client._cookies), [])
        self.assertFalse(client._authenticated)
        self.assertIsNone(client._profile)
        self.assertTrue(client._closed)

    def test_late_login_profile_cannot_restore_revoked_identity(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(request):
            entered.set()
            self.assertTrue(release.wait(3))
            return Response(PROFILE, headers={"Set-Cookie": "JSESSIONID=SYNTHETIC-LATE-COOKIE; Path=/; Secure"})
        opener = ScriptOpener(login_script()[:-1] + [blocked])
        client = SchoolHTTPClient(opener=opener)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(client.login, "SYNTHETIC-ACCOUNT", SYNTHETIC_PASSWORD)
            self.assertTrue(entered.wait(1))
            try:
                pool.submit(client.close).result(timeout=.5)
            finally:
                release.set()
            result = pending.result(timeout=1)
        self.assertEqual(result["state"], "source_session_expired")
        self.assertNotIn("profile", result)
        self.assertFalse(client._authenticated)
        self.assertIsNone(client._profile)
        self.assertEqual(list(client._cookies), [])

    def test_plan_catalogue_is_narrowed_with_source_selects_and_opaque_pdf_ref(self):
        index = '<select id="synj_cx"><option value="2023">2023</option></select><select id="xydm_cx"><option value="SYNTHETIC-COLLEGE">合成学院</option></select><select id="zydm_cx"><option value="SYNTHETIC-MAJOR">合成专业</option></select>'
        pdf = b"%PDF-1.7\nSYNTHETIC-DOCUMENT\n%%EOF\n"
        self.client([Response(index), Response([{"zydm": "SYNTHETIC-MAJOR", "zymc": "合成专业"}]), Response({"items": [{"jxjhh": "SYNTHETIC-PLAN-ID", "xymc": "合成学院", "zymc": "合成专业"}], "total": 1, "pageNo": 0, "currentPage": 1}), Response(pdf, content_type="application/pdf")])
        scope = {"cohort": 2023, "college_name": "合成学院", "major_name": "合成专业", "program": None, "study_path_raw": None}
        catalogue = self.http.request({"operation": "plan_catalogue", "scope": scope})
        self.assertEqual(catalogue["state"], "business")
        self.assertNotIn("pageNo", catalogue["payload"])
        self.assertEqual(catalogue["payload"]["currentPage"], 1)
        self.assertNotIn("SYNTHETIC-PLAN-ID", str(catalogue))
        self.assertEqual(parse_qs(self.opener.requests[-1]["body"].decode())["zydm"], ["SYNTHETIC-MAJOR"])
        options_request = self.opener.requests[-2]
        self.assertEqual(urlsplit(options_request["url"]).path, PLAN_MAJOR_OPTIONS)
        self.assertEqual(options_request["method"], "POST")
        self.assertEqual(parse_qs(options_request["body"].decode(), keep_blank_values=True),
                         {"njdm_id": ["2023"], "jg_id": ["SYNTHETIC-COLLEGE"], "zydl_id": [""], "gjzy_id": [""]})
        ref = catalogue["payload"]["items"][0]["plan_ref"]
        result = self.http.request({"operation": "plan_document", "plan_ref": ref})
        self.assertEqual(result["payload"], pdf)
        self.assertEqual(parse_qs(urlsplit(self.opener.requests[-1]["url"]).query)["id"], ["SYNTHETIC-PLAN-ID"])
        self.assertEqual(self.http.request({"operation": "plan_document", "plan_ref": "PLAN-" + "f" * 32})["state"], "route_not_confirmed")

    def test_plan_scope_not_own_profile_or_unavailable_mapping_never_queries(self):
        self.client([])
        for scope in ({"cohort": 2026, "college_name": "合成学院", "major_name": "合成专业"},
                      {"cohort": 2023, "college_name": "合成学院", "major_name": "合成专业", "program": "合成项目"}):
            before = len(self.opener.requests)
            result = self.http.request({"operation": "plan_catalogue", "scope": scope})
            self.assertEqual(result["state"], "route_not_confirmed")
            self.assertEqual(len(self.opener.requests), before)

    def test_empty_major_control_uses_only_own_year_college_dependent_options(self):
        index = '<select id="synj_cx"><option value="2023">2023</option></select><select id="xydm_cx"><option value="SYNTHETIC-COLLEGE">合成学院</option></select><select id="zydm_cx"><option value="">全部</option></select><select id="gjzy_cx"><option value="">全部</option><option value="SYNTHETIC-NATIONAL" selected>合成国家专业</option></select><select id="zydl_cx"><option value="SYNTHETIC-CATEGORY" selected>合成类别</option></select>'
        options = [{"zydm": "SYNTHETIC-OTHER", "zymc": "合成另专业"}, {"zydm": "SYNTHETIC-MAJOR", "zymc": "合成专业"}]
        self.client([Response(index), Response(options), Response({"items": [], "currentPage": 1, "pageNo": 0, "totalResult": 0})])
        scope = {"cohort": 2023, "college_name": "合成学院", "major_name": "合成专业"}
        result = self.http.request({"operation": "plan_catalogue", "scope": scope})
        self.assertEqual(result["state"], "business")
        option_request, catalogue_request = self.opener.requests[-2:]
        query = parse_qs(urlsplit(option_request["url"]).query)
        self.assertEqual(query["rangeType"], ["ggxydm"])
        self.assertEqual(query["rangeable"], ["false"])
        self.assertEqual(parse_qs(option_request["body"].decode()), {"njdm_id": ["2023"], "jg_id": ["SYNTHETIC-COLLEGE"],
                         "zydl_id": ["SYNTHETIC-CATEGORY"], "gjzy_id": ["SYNTHETIC-NATIONAL"]})
        self.assertEqual(parse_qs(catalogue_request["body"].decode())["zydm"], ["SYNTHETIC-MAJOR"])
        self.assertNotIn("SYNTHETIC-MAJOR", str(result))

    def test_missing_or_ambiguous_dependent_major_never_queries_catalogue(self):
        index = '<select id="synj_cx"><option value="2023">2023</option></select><select id="xydm_cx"><option value="SYNTHETIC-COLLEGE">合成学院</option></select><select id="zydm_cx"><option value="">全部</option></select>'
        for options in ([], [{"zydm": "SYNTHETIC-A", "zymc": "合成专业"}, {"zydm": "SYNTHETIC-B", "zymc": "合成专业"}]):
            with self.subTest(options=options):
                self.client([Response(index), Response(options)])
                before = len(self.opener.requests)
                result = self.http.request({"operation": "plan_catalogue", "scope": {"cohort": 2023, "college_name": "合成学院", "major_name": "合成专业"}})
                self.assertEqual(result["state"], "route_not_confirmed")
                self.assertEqual(len(self.opener.requests) - before, 2)
                self.assertNotIn("payload", result)

    def test_page_parser_does_not_execute_scripts_or_use_script_values(self):
        page = _Page('<script>throw Error("synthetic")</script><table><tr><td>学号</td><td><input value="SYNTHETIC-SOURCE-A"/></td></tr></table>')
        self.assertEqual(page.rows, [["学号", "SYNTHETIC-SOURCE-A"]])


if __name__ == "__main__":
    unittest.main()
