/* Pure request/projection contract. No passwords, cookies, headers or logging. */
(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StudentConnector = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createCore() {
  "use strict";
  const ORIGIN = "https://zdbk.zju.edu.cn";
  const JSON_LIMIT = 2 * 1024 * 1024;
  const PDF_LIMIT = 10 * 1024 * 1024;
  const TIMEOUT_MS = 30000;
  const DATA_ROUTES = Object.freeze({
    academic: "/jwglxt/cxdy/xscjcx_cxXscjIndex.html",
    major_academic: "/jwglxt/zycjtj/xszgkc_cxXsZgkcIndex.html",
    timetable: "/jwglxt/kbcx/xskbcx_cxXsKb.html",
    exams: "/jwglxt/xskscx/kscx_cxXsgrksIndex.html",
  });
  const PLAN_CATALOGUE_ROUTE = "/jwglxt/pyfagl/pyfaxxcx_cxPyfaxscxIndex.html";
  const PLAN_DOCUMENT_ROUTE = "/jwglxt/pyfagl/pyfayl_cxPyfaylPdf.html";
  const SOURCE_PAGES = Object.freeze({
    academic: Object.freeze([DATA_ROUTES.academic]),
    major_academic: Object.freeze([DATA_ROUTES.major_academic]),
    timetable: Object.freeze([DATA_ROUTES.timetable, "/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html"]),
    exams: Object.freeze([DATA_ROUTES.exams]),
    plan_catalogue: Object.freeze([PLAN_CATALOGUE_ROUTE]),
  });
  const FIELDS = Object.freeze({
    academic: new Set("xkkh kcdm kch kcmc xnm xn xqm xq xf cj jd source_selection_id_raw course_code_raw course_name_raw academic_year_raw term_raw nominal_credits grade_raw grade_points_raw completion_status_raw completion_status row_status sftg source_attempt_id attempt_source_id recognition_status_raw recognition_status substitution_raw substitution replacement major_status_raw is_major_raw major_status record_kind record_type earned_credits reported_earned_credits 选课课号 课程代码 课程号 课程名称 学年 学期 学分 额定学分 成绩 绩点 是否通过 修读状态 修读实例号 认定状态 替代课程 替换关系 主修标记 记录类型 已获学分".split(" ")),
    timetable: new Set("xkkh jxb_id jxbh kcdm kch kcmc xnm xqm kcb sfqd dsz xxq xm jsxm sksj cdmc skdd xqj djj skcd zcd source_selection_id_raw source_section_id course_code_raw course_name_raw academic_year_raw term_raw section_raw sfqd_raw dsz_raw xxq_raw instructor_raw teacher time_raw place_raw weekday periods jcs week_set weeks week_annotation_raw start_period_raw duration_raw meetings meeting_segments 上课时段 选课课号 教学班号 课程代码 课程号 课程名称 学年 学期 教学班原段 是否确定 单双周 分学期 教师 上课时间 地点 上课地点 星期 节次 周集合 周次 起始节 节数".split(" ")),
    exams: new Set("source_exam_id ks_id xkkh jxb_id jxbh kcdm kch kcmc xnm xqm xxq kssj jsmc qzkssj qzjsmc ksdd cdmc ksrq kssk ksjs source_selection_id_raw source_section_id course_code_raw course_name_raw academic_year_raw term_raw exam_time_raw final_exam_time_raw midterm_exam_time_raw final_exam_place_raw midterm_exam_place_raw exam_date start_time end_time timezone place_raw exam_status_raw exam_status 考试号 选课课号 教学班号 课程代码 课程号 课程名称 学年 学期 考试时间 期末考试时间 期中考试时间 期末考试地点 期中考试地点 考试日期 开始时间 结束时间 时区 考试地点 考试状态".split(" ")),
  });
  const NUMERIC_META = new Set(["total", "totalCount", "totalResult", "pageNo", "pageSize", "currentPage", "showCount"]);
  const SECRET_KEY = /(?:pass(?:word)?|pwd|cookie|token|ticket|authorization|credential|session|csrf|secret|验证码|密码)/i;
  const SECRET_VALUE = /(?:\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_=.-]+|(?:cookie|password|passwd|authorization|access_token|token|ticket|csrf|JSESSIONID)\s*[:=]|https?:\/\/[^/\s]+:[^/\s]+@)/i;
  const PLAN_FIELDS = new Set(["cohort", "college_name", "major_name", "major_or_category_raw", "program", "study_path_raw"]);
  function fail(code) { const error = new Error(code); error.code = code; throw error; }
  function plain(value) { return !!value && typeof value === "object" && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null); }
  function cleanScalar(value) {
    if (value === null || typeof value === "boolean") return value;
    if (typeof value === "number") { if (!Number.isFinite(value)) fail("invalid_business_value"); return value; }
    if (typeof value === "string") { if (value.length > 20000 || SECRET_VALUE.test(value)) fail("credential_like_business_value"); return value; }
    fail("invalid_business_value");
  }
  function safeSubject(value) {
    if (typeof value !== "string" && typeof value !== "number") return null;
    const text = String(value).trim();
    return /^[A-Za-z0-9_-]{1,80}$/.test(text) && !SECRET_VALUE.test(text) ? text : null;
  }
  function sourceSubject(json, rows) {
    const ids = new Set();
    for (const row of [json, ...rows]) {
      if (!plain(row)) continue;
      for (const key of ["source_subject_id", "student_number", "xh", "xhm", "学号"]) {
        if (Object.hasOwn(row, key)) { const value = safeSubject(row[key]); if (value) ids.add(value); }
      }
    }
    if (ids.size > 1) fail("source_subject_changed");
    return ids.size === 1 ? [...ids][0] : null;
  }
  function projectRow(row, allowed, depth = 0) {
    if (!plain(row) || depth > 4) fail("schema_unrecognized");
    const result = {};
    for (const [key, value] of Object.entries(row)) {
      if (SECRET_KEY.test(key) || !allowed.has(key)) continue;
      if (["meetings", "meeting_segments", "上课时段"].includes(key)) {
        if (!Array.isArray(value) || value.length > 100) fail("schema_unrecognized");
        result[key] = value.map(item => item === null ? null : projectRow(item, allowed, depth + 1));
      } else if (["periods", "jcs", "week_set", "weeks", "节次", "周集合"].includes(key) && Array.isArray(value)) {
        if (value.length > 100) fail("schema_unrecognized");
        result[key] = value.map(cleanScalar);
      } else if (plain(value)) {
        const wrapper = {};
        for (const name of ["value", "raw_value", "state"]) if (Object.hasOwn(value, name)) wrapper[name] = cleanScalar(value[name]);
        if (!Object.keys(wrapper).length) fail("schema_unrecognized");
        result[key] = wrapper;
      } else result[key] = cleanScalar(value);
    }
    if (!Object.keys(result).length) fail("schema_unrecognized");
    return result;
  }
  function explicitValue(value) {
    if (plain(value)) return Object.hasOwn(value, "raw_value") ? value.raw_value : value.value;
    return value;
  }
  function guardRowScope(rows, targetTerm) {
    if (typeof targetTerm !== "string") return;
    const match = /^(20\d{2})-(20\d{2})-(?:AW|SS|A|W|S|SU)$/.exec(targetTerm);
    if (!match) fail("row_outside_confirmed_scope");
    const expected = `${match[1]}-${match[2]}`;
    for (const row of rows) {
      if (!plain(row)) continue;
      for (const key of ["xkkh", "source_selection_id_raw", "选课课号"]) {
        const value = explicitValue(row[key]);
        const source = typeof value === "string" ? /^\((20\d{2})-(20\d{2})-[^)]{1,80}\)/.exec(value) : null;
        if (source && `${source[1]}-${source[2]}` !== expected) fail("row_outside_confirmed_scope");
      }
      for (const key of ["xnm", "academic_year_raw", "academic_year", "学年"]) {
        const value = explicitValue(row[key]);
        const source = typeof value === "string" ? /^(20\d{2})-(20\d{2})(?:学年)?$/.exec(value.trim()) : null;
        if (source && `${source[1]}-${source[2]}` !== expected) fail("row_outside_confirmed_scope");
      }
    }
  }
  function projectJSON(operation, json, targetTerm, viewMode) {
    const domain = operation === "major_academic" ? "academic" : operation;
    const fields = FIELDS[domain];
    if (!fields || !plain(json)) fail("schema_unrecognized");
    if (operation === "timetable" && viewMode === "table") {
      for (const key of ["fKbList", "sjkList", "xskList", "ywkList", "jxkList"]) {
        if (Object.hasOwn(json, key) && (!Array.isArray(json[key]) || json[key].length)) fail("unsupported_timetable_supplement");
      }
    }
    const containers = ["items", "records", "kbList"].filter(key => Object.hasOwn(json, key));
    if (containers.length !== 1 || !Array.isArray(json[containers[0]]) || json[containers[0]].length > 1000) fail("schema_unrecognized");
    const rows = json[containers[0]];
    guardRowScope(rows, targetTerm);
    const payload = {[containers[0]]: rows.map(row => projectRow(row, fields))};
    for (const key of NUMERIC_META) {
      if (!Object.hasOwn(json, key)) continue;
      const value = json[key];
      if (value !== null && !(typeof value === "number" && Number.isInteger(value) && value >= 0) && !(typeof value === "string" && /^\d{1,9}$/.test(value))) fail("invalid_pagination");
      payload[key] = value;
    }
    return {payload, source_subject_id: sourceSubject(json, rows)};
  }
  function sameOriginURL(raw, base = ORIGIN) {
    let url; try { url = new URL(raw, base); } catch { fail("route_not_registered"); }
    if (url.origin !== ORIGIN || url.username || url.password || url.hash || [...url.searchParams.keys()].some(key => SECRET_KEY.test(key))) fail("route_not_registered");
    if (/(?:cas|login|auth|captcha|verify|logout)/i.test(url.pathname)) fail("route_not_registered");
    return url;
  }
  function routeObserved(operation, evidence) {
    const routes = SOURCE_PAGES[operation];
    if (!routes || !Array.isArray(evidence) || evidence.length > 2000) return false;
    return evidence.some(raw => { try { return routes.includes(sameOriginURL(raw).pathname); } catch { return false; } });
  }
  function validateSpec(spec) {
    if (!plain(spec) || Object.keys(spec).some(key => !["operation", "target_term", "page", "page_size", "scope", "plan_ref"].includes(key))) fail("invalid_request_spec");
    if (![...Object.keys(DATA_ROUTES), "plan_catalogue", "plan_document"].includes(spec.operation)) fail("unsupported_operation");
    const page = spec.page ?? 1, size = spec.page_size ?? 20;
    if (!Number.isInteger(page) || page < 1 || page > 3 || !Number.isInteger(size) || size < 1 || size > 100) fail("request_budget_exceeded");
    if (spec.target_term !== undefined) {
      const matched = typeof spec.target_term === "string" ? /^(20\d{2})-(20\d{2})-(AW|SS|A|W|S|SU)$/.exec(spec.target_term) : null;
      if (!matched || Number(matched[2]) !== Number(matched[1]) + 1) fail("invalid_request_spec");
    }
    const scope = spec.scope ?? {};
    if (!plain(scope) || Object.keys(scope).some(key => !PLAN_FIELDS.has(key))) fail("invalid_request_spec");
    if (DATA_ROUTES[spec.operation] && (spec.scope !== undefined || spec.target_term === undefined)) fail("invalid_request_spec");
    for (const value of Object.values(scope)) {
      if (value !== null && !(typeof value === "string" || typeof value === "number")) fail("invalid_request_spec");
      if (value !== null && (String(value).length > 160 || SECRET_VALUE.test(String(value)))) fail("invalid_request_spec");
    }
    if (spec.operation === "plan_document" && !/^PLAN-[a-f0-9]{32}$/.test(spec.plan_ref ?? "")) fail("invalid_plan_ref");
    if (spec.operation !== "plan_document" && spec.plan_ref !== undefined) fail("invalid_request_spec");
    return {...spec, scope, page, page_size: size};
  }
  function sourceTerm(yearLabel, termLabel) {
    const year = /^(20\d{2})\s*[-–—/]\s*(20\d{2})(?:\s*学年)?$/.exec(String(yearLabel).trim());
    const label = String(termLabel).trim().replace(/学期/g, "").replace(/[\s、,，/]+/g, "");
    const season = {"秋冬": "AW", "春夏": "SS", "秋": "A", "冬": "W", "春": "S", "夏": "SU"}[label];
    if (!year || !season || Number(year[2]) !== Number(year[1]) + 1) return null;
    return `${year[1]}-${year[2]}-${season}`;
  }
  function confirmedSelect(model) {
    if (!plain(model) || model.selected_count !== 1 || typeof model.value !== "string" || typeof model.option_label !== "string") return null;
    const label = model.option_label.replace(/\s+/g, " ").trim();
    if (!label || model.value.length > 80 || SECRET_VALUE.test(model.value)) return null;
    if (!model.visible) {
      if (model.chosen_visible !== true || model.chosen_same_parent !== true || !Array.isArray(model.chosen_labels) || model.chosen_labels.length !== 1 || typeof model.chosen_labels[0] !== "string" || model.chosen_labels[0].replace(/\s+/g, " ").trim() !== label) return null;
    }
    return {value: model.value, label};
  }
  function observedMenuCode(raw) {
    try {
      const url = new URL(raw);
      const values = url.searchParams.getAll("gnmkdm");
      if (url.origin !== ORIGIN || values.length !== 1 || !/^N\d{4,12}$/.test(values[0])) return null;
      return values[0];
    } catch { return null; }
  }
  function confirmedTimetableView(model) {
    if (!plain(model) || model.table_visible !== true || model.list_visible !== true || typeof model.table_selected !== "boolean" || typeof model.list_selected !== "boolean" || model.table_selected === model.list_selected) return null;
    return model.list_selected ? "list" : "table";
  }
  function buildRequest(input, registration) {
    const spec = validateSpec(input), route = DATA_ROUTES[spec.operation];
    if (!route || !registration || registration.operation !== spec.operation || registration.route !== route) fail("route_not_registered");
    const observed = registration.filters;
    if (!plain(observed)) fail("scope_not_verified");
    const year = observed.academic_year_raw, term = observed.term_raw;
    if (typeof year !== "string" || typeof term !== "string" || sourceTerm(observed.academic_year_label, observed.term_label) !== spec.target_term) fail("scope_not_verified");
    if (!year.length || !term.length || year.length > 80 || term.length > 80 || SECRET_VALUE.test(year) || SECRET_VALUE.test(term)) fail("scope_not_verified");
    const url = new URL(route, ORIGIN);
    if (spec.operation !== "timetable") url.searchParams.set("doType", "query");
    if (registration.menu_code != null) {
      if (typeof registration.menu_code !== "string" || !/^N\d{4,12}$/.test(registration.menu_code)) fail("route_not_registered");
      url.searchParams.set("gnmkdm", registration.menu_code);
    }
    // Current source JS confirms academic's xn/xq names. Other domains keep
    // their separately observed maps; UI names are never blindly POSTed.
    let parameters = spec.operation === "academic" ? {xn: String(year), xq: String(term)} : {xnm: String(year), xqm: String(term)};
    if (spec.operation === "exams") {
      const source = /^([12])\|([秋冬春夏])$/.exec(term);
      if (!source || !/^20\d{2}-20\d{2}$/.test(year) || source[2] !== observed.term_label.trim()) fail("scope_not_verified");
      parameters = {xxq: source[2], xnxq: `(${year}-${source[1]})-`};
    }
    if (spec.operation === "timetable") parameters.xqmmc = observed.term_label;
    if (spec.operation === "timetable") {
      if (!["table", "list"].includes(registration.view_mode)) fail("scope_not_verified");
      const mode = registration.view_mode === "list" ? "1" : "0";
      parameters.xxqf = mode; parameters.xsfs = mode;
    }
    const form = new URLSearchParams(parameters);
    if (spec.operation !== "timetable") {
      form.set("queryModel.currentPage", String(spec.page));
      form.set("queryModel.showCount", String(spec.page_size));
    } else if (spec.page !== 1) fail("request_budget_exceeded");
    return {url: url.href, method: "POST", body: form.toString(), credentials: "include", redirect: "manual", headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}};
  }
  function buildPlanRequest(input, registration) {
    const spec = validateSpec(input);
    if (spec.operation !== "plan_catalogue" || registration?.operation !== "plan_catalogue" || registration.route !== PLAN_CATALOGUE_ROUTE || !planScopeMatches(spec.scope, registration.filters, [registration.filters])) fail("scope_not_verified");
    const values = registration.query_values;
    if (!plain(values) || Object.keys(values).some(key => !["synj", "xydm", "zydm", "dlbs", "gjzydm"].includes(key))) fail("scope_not_verified");
    if (!["synj", "xydm", "zydm"].every(key => typeof values[key] === "string" && values[key].length > 0)) fail("scope_not_verified");
    if (String(spec.scope.cohort) !== values.synj) fail("scope_not_verified");
    const url = new URL(PLAN_CATALOGUE_ROUTE, ORIGIN);
    url.searchParams.set("doType", "query");
    if (registration.menu_code != null) {
      if (typeof registration.menu_code !== "string" || !/^N\d{4,12}$/.test(registration.menu_code)) fail("route_not_registered");
      url.searchParams.set("gnmkdm", registration.menu_code);
    }
    const form = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) {
      if (typeof value !== "string" || value.length > 160 || SECRET_VALUE.test(value)) fail("scope_not_verified");
      form.set(key, value);
    }
    form.set("queryModel.currentPage", String(spec.page));
    form.set("queryModel.showCount", String(spec.page_size));
    return {url: url.href, method: "POST", body: form.toString(), credentials: "include", redirect: "manual", headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}};
  }
  function classifyHTTP(response) {
    if (response.type === "opaqueredirect" || response.status >= 300 && response.status < 400 || response.redirected) return "login_required";
    if (response.status === 401) return "session_expired";
    if (response.status === 403) return "permission_denied";
    if (response.status !== 200) return "transport_failure";
    if (response.url) { try { if (new URL(response.url).origin !== ORIGIN) return "login_required"; } catch { return "transport_failure"; } }
    return "business";
  }
  function classifyHTML(text) {
    if (/(?:验证码|captcha|滑块|人机验证)/i.test(text)) return "challenge_required";
    if (/(?:本人承诺遵守考试纪律|继续查看|考试纪律承诺)/.test(text)) return "user_confirmation_required";
    if (/(?:type\s*=\s*["']?password|统一身份认证|cas\/login|登录失效|请.*登录|会话.*失效)/i.test(text)) return "session_expired";
    if (/(?:无权|权限不足|禁止访问)/.test(text)) return "permission_denied";
    return "schema_unrecognized";
  }
  async function readLimited(response, limit) {
    const length = response.headers?.get("content-length");
    if (length !== null && length !== undefined && /^\d+$/.test(length) && Number(length) > limit) fail("response_too_large");
    if (!response.body || typeof response.body.getReader !== "function") {
      const buffer = new Uint8Array(await response.arrayBuffer());
      if (buffer.length > limit) fail("response_too_large"); return buffer;
    }
    const reader = response.body.getReader();
    const parts = []; let size = 0;
    try {
      while (true) {
        const part = await reader.read(); if (part.done) break;
        size += part.value.byteLength;
        if (size > limit) { await reader.cancel(); fail("response_too_large"); }
        parts.push(part.value);
      }
    } finally { reader.releaseLock(); }
    const result = new Uint8Array(size); let offset = 0;
    for (const part of parts) { result.set(part, offset); offset += part.byteLength; }
    return result;
  }
  function responseMeta(state, sessionEpoch, status = null, type = null) {
    const mapped = {login_required: "awaiting_user_login", session_expired: "source_session_expired", challenge_required: "captcha_required", transport_failure: "source_unavailable", timeout: "source_unavailable", response_too_large: "schema_changed", schema_unrecognized: "schema_changed", invalid_business_value: "schema_changed", credential_like_business_value: "schema_changed", invalid_pagination: "schema_changed", row_outside_confirmed_scope: "schema_changed", unsupported_timetable_supplement: "schema_changed"};
    const allowed = new Set(["business", "awaiting_user_login", "source_session_expired", "captcha_required", "permission_denied", "source_unavailable", "schema_changed", "route_not_confirmed", "user_confirmation_required", "source_subject_changed"]);
    const wire = mapped[state] || (allowed.has(state) ? state : "route_not_confirmed");
    const mime = ["application/json", "text/json", "application/pdf", "text/html", "dom_business_projection"].includes(type) ? type : null;
    return {state: wire, http_status: status, content_type: mime, session_epoch: sessionEpoch, source_subject_id: null};
  }
  async function fetchBusiness(spec, registration, sessionEpoch, fetcher = fetch) {
    let request; try { request = buildRequest(spec, registration); } catch (error) { return responseMeta(error.code || "invalid_request_spec", sessionEpoch); }
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetcher(request.url, {...request, signal: controller.signal});
      const state = classifyHTTP(response);
      if (state !== "business") return responseMeta(state, sessionEpoch, response.status || null);
      const contentType = (response.headers?.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
      const bytes = await readLimited(response, JSON_LIMIT), text = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
      if (!["application/json", "text/json"].includes(contentType)) return responseMeta(classifyHTML(text), sessionEpoch, 200, contentType || null);
      let json; try { json = JSON.parse(text); } catch { return responseMeta("schema_unrecognized", sessionEpoch, 200, contentType); }
      const projected = projectJSON(spec.operation, json, spec.target_term, registration.view_mode);
      return {...responseMeta("business", sessionEpoch, 200, contentType), ...projected};
    } catch (error) { return responseMeta(error.code || (error.name === "AbortError" ? "timeout" : "transport_failure"), sessionEpoch); }
    finally { clearTimeout(timer); }
  }
  function pdfURL(raw, base) {
    const url = sameOriginURL(raw, base);
    if (url.pathname === PLAN_DOCUMENT_ROUTE) {
      if (url.searchParams.getAll("id").length !== 1 || url.searchParams.getAll("doType").length !== 1 || url.searchParams.get("doType") !== "query" || [...url.searchParams.keys()].some(key => !["id", "doType"].includes(key)) || !safePlanRowId(url.searchParams.get("id"))) fail("plan_document_not_observed");
    } else if (!/\.pdf$/i.test(url.pathname)) fail("plan_document_not_observed");
    return url.href;
  }
  function safePlanRowId(value) {
    return typeof value === "string" && /^[\p{L}\p{N}_.()-]{1,128}$/u.test(value) && !SECRET_VALUE.test(value);
  }
  function planDocumentURL(rowId) {
    if (!safePlanRowId(rowId)) fail("schema_unrecognized");
    const url = new URL(PLAN_DOCUMENT_ROUTE, ORIGIN);
    url.searchParams.set("id", rowId); url.searchParams.set("doType", "query");
    return pdfURL(url.href);
  }
  function planScopeMatches(scope, filters, rows) {
    if (!plain(scope) || !plain(filters) || !Array.isArray(rows) || !rows.length) return false;
    const required = ["cohort", "college_name"];
    const majorKey = scope.major_name ? "major_name" : "major_or_category_raw";
    required.push(majorKey);
    if (required.some(key => scope[key] === null || scope[key] === undefined || !String(scope[key]).trim())) return false;
    function observedValue(source, key) {
      // A directory label can be filtered by the exact raw category text.
      // Do not rewrite the result's major/category fields or verify a mapping.
      if (key === "major_or_category_raw" && !scope.major_name && source[key] == null) return source.major_name;
      return source[key];
    }
    for (const key of PLAN_FIELDS) {
      if (scope[key] === undefined || scope[key] === null || scope[key] === "") continue;
      if (String(observedValue(filters, key)) !== String(scope[key]) || rows.some(row => String(observedValue(row, key)) !== String(scope[key]))) return false;
    }
    return true;
  }
  function projectPlanRows(rows, scope, filters) {
    if (!planScopeMatches(scope, filters, rows) || rows.length > 100) fail("plan_scope_not_verified");
    return rows.map(row => {
      if (!plain(row) || row.title != null && (typeof row.title !== "string" || !row.title.trim()) || !/^PLAN-[a-f0-9]{32}$/.test(row.plan_ref ?? "")) fail("schema_unrecognized");
      const result = {plan_ref: row.plan_ref, title: row.title == null ? null : cleanScalar(row.title), source_observation_kind: "directory_observation"};
      for (const key of PLAN_FIELDS) result[key] = row[key] == null || row[key] === "" ? null : cleanScalar(row[key]);
      if (row.cohort_provenance === "confirmed_query_scope") result.cohort_provenance = row.cohort_provenance;
      return result;
    });
  }
  function projectPlanJSON(json, spec, registration, makeRef = () => `PLAN-${crypto.randomUUID().replace(/-/g, "")}`) {
    if (!plain(json) || !Array.isArray(json.items) || json.items.length > spec.page_size || !plain(registration.filters)) fail("schema_unrecognized");
    const documents = new Map();
    const rows = json.items.map(row => {
      if (!plain(row) || typeof row.xymc !== "string" || typeof row.zymc !== "string" || !safePlanRowId(row.jxjhh)) fail("schema_unrecognized");
      const plan_ref = makeRef();
      if (!/^PLAN-[a-f0-9]{32}$/.test(plan_ref)) fail("schema_unrecognized");
      documents.set(plan_ref, planDocumentURL(row.jxjhh));
      return {plan_ref, cohort: Number(registration.query_values.synj), cohort_provenance: "confirmed_query_scope", college_name: row.xymc, major_name: row.zymc, major_or_category_raw: null, program: null, study_path_raw: null, title: null};
    });
    let projected;
    try { projected = rows.length ? projectPlanRows(rows, spec.scope, registration.filters) : []; }
    catch { fail("row_outside_confirmed_scope"); }
    const payload = {items: projected};
    for (const key of NUMERIC_META) {
      if (!Object.hasOwn(json, key)) continue;
      const value = json[key];
      if (value !== null && !(typeof value === "number" && Number.isInteger(value) && value >= 0) && !(typeof value === "string" && /^\d{1,9}$/.test(value))) fail("invalid_pagination");
      if (value !== null) payload[key] = value;
    }
    return {payload, documents};
  }
  async function fetchPlanCatalogue(input, registration, sessionEpoch, fetcher = fetch) {
    let spec, request; try { spec = validateSpec(input); request = buildPlanRequest(spec, registration); } catch (error) { return responseMeta(error.code || "invalid_request_spec", sessionEpoch); }
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetcher(request.url, {...request, signal: controller.signal});
      const state = classifyHTTP(response); if (state !== "business") return responseMeta(state, sessionEpoch, response.status || null);
      const type = (response.headers?.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
      const bytes = await readLimited(response, JSON_LIMIT), text = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
      if (type !== "application/json") return responseMeta(classifyHTML(text), sessionEpoch, 200, type || null);
      let json; try { json = JSON.parse(text); } catch { return responseMeta("schema_unrecognized", sessionEpoch, 200, type); }
      const projected = projectPlanJSON(json, spec, registration);
      return {...responseMeta("business", sessionEpoch, 200, type), ...projected};
    } catch (error) { return responseMeta(error.code || (error.name === "AbortError" ? "timeout" : "transport_failure"), sessionEpoch); }
    finally { clearTimeout(timer); }
  }
  function bytesToBase64(bytes) {
    let result = "";
    for (let offset = 0; offset < bytes.length; offset += 32768) result += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    return btoa(result);
  }
  async function fetchPlanDocument(input, documents, sessionEpoch, fetcher = fetch) {
    let spec, url;
    try { spec = validateSpec(input); if (spec.operation !== "plan_document" || !(documents instanceof Map) || !documents.has(spec.plan_ref)) fail("plan_document_not_observed"); url = pdfURL(documents.get(spec.plan_ref)); }
    catch (error) { return responseMeta(error.code || "invalid_request_spec", sessionEpoch); }
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetcher(url, {method: "GET", credentials: "include", redirect: "manual", signal: controller.signal});
      const state = classifyHTTP(response); if (state !== "business") return responseMeta(state, sessionEpoch, response.status || null);
      const type = (response.headers?.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
      if (type !== "application/pdf") { const bytes = await readLimited(response, JSON_LIMIT); return responseMeta(classifyHTML(new TextDecoder().decode(bytes)), sessionEpoch, 200, type || null); }
      const bytes = await readLimited(response, PDF_LIMIT);
      if (bytes.length < 5 || new TextDecoder().decode(bytes.subarray(0, 5)) !== "%PDF-") return responseMeta("schema_unrecognized", sessionEpoch, 200, type);
      return {...responseMeta("business", sessionEpoch, 200, type), payload_base64: bytesToBase64(bytes)};
    } catch (error) { return responseMeta(error.code || (error.name === "AbortError" ? "timeout" : "transport_failure"), sessionEpoch); }
    finally { clearTimeout(timer); }
  }
  function loopbackURL(port, path) {
    if (!/^\d{4,5}$/.test(String(port)) || Number(port) < 1024 || Number(port) > 65535 || !["/pair", "/health", "/next", "/result", "/heartbeat", "/disconnect"].includes(path)) fail("invalid_loopback_address");
    return `http://127.0.0.1:${Number(port)}${path}`;
  }
  return Object.freeze({ORIGIN, DATA_ROUTES, SOURCE_PAGES, PLAN_CATALOGUE_ROUTE, PLAN_DOCUMENT_ROUTE, JSON_LIMIT, PDF_LIMIT, TIMEOUT_MS, validateSpec, sourceTerm, confirmedSelect, observedMenuCode, confirmedTimetableView, routeObserved, buildRequest, buildPlanRequest, projectJSON, sourceSubject, classifyHTTP, classifyHTML, readLimited, responseMeta, fetchBusiness, pdfURL, planDocumentURL, planScopeMatches, projectPlanRows, projectPlanJSON, fetchPlanCatalogue, fetchPlanDocument, loopbackURL});
});
