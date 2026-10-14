/* Runs in Chrome's isolated content-script world on the official top frame. */
(function contentConnector() {
  "use strict";
  if (window.top !== window || location.origin !== StudentConnector.ORIGIN) return;
  const core = StudentConnector;
  const epoch = crypto.randomUUID();
  const registrations = new Map(), documents = new Map();
  let previousSubject = null, busy = false, catalogueSignature = null, catalogueRowsSignature = null, catalogueScope = null;
  function visible(element) { return !!element && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden"; }
  function pageState() {
    if ([...document.querySelectorAll('input[type="password"]')].some(visible)) return "awaiting_user_login";
    const dialogs = [...document.querySelectorAll('[role="dialog"],.modal,.ui-dialog,.layui-layer')].filter(visible);
    const text = dialogs.map(element => (element.textContent || "").slice(0, 4000)).join(" ");
    if (/本人承诺遵守考试纪律|考试纪律承诺/.test(text)) return "user_confirmation_required";
    if (/验证码|captcha|人机验证|滑块/i.test(text)) return "captcha_required";
    return null;
  }
  function controlObservation(control) {
    if (!control || control.tagName !== "SELECT") return null;
    const chosen = control.id ? document.getElementById(`${control.id}_chosen`) : null;
    const labels = chosen ? [...chosen.querySelectorAll("a.chosen-single span")].filter(visible).map(element => element.textContent) : [];
    return core.confirmedSelect({value: String(control.value), option_label: control.selectedOptions[0]?.textContent || "", selected_count: control.selectedOptions.length, visible: visible(control), chosen_visible: visible(chosen), chosen_same_parent: chosen?.parentElement === control.parentElement, chosen_labels: labels});
  }
  function valueControl(keys) {
    const selector = keys.map(key => `[name="${key}"],#${key}`).join(",");
    const controls = [...document.querySelectorAll(selector)].filter(element => element.tagName === "SELECT");
    if (controls.length !== 1) return null;
    return controlObservation(controls[0]);
  }
  function termFilters(operation) {
    const keys = operation === "exams" ? [["xn", "xnSelect"], ["xxq", "xxqSelect"]] : operation === "timetable" ? [["xnm"], ["xqm"]] : [["xnm", "xn_cx"], ["xqm", "xq_cx"]];
    const year = valueControl(keys[0]), term = valueControl(keys[1]);
    return year === null || term === null || core.sourceTerm(year.label, term.label) === null ? null : {academic_year_raw: year.value, term_raw: term.value, academic_year_label: year.label, term_label: term.label};
  }
  function timetableView() {
    const tableButtons = [...document.querySelectorAll("#tableOne")], listButtons = [...document.querySelectorAll("#tableTwo")];
    if (tableButtons.length !== 1 || listButtons.length !== 1) return null;
    const table = tableButtons[0], list = listButtons[0];
    return core.confirmedTimetableView({table_visible: visible(table), list_visible: visible(list), table_selected: table.classList.contains("btn-primary"), list_selected: list.classList.contains("btn-primary")});
  }
  function evidence() {
    return [location.href, ...[...document.querySelectorAll("a[href],form[action]")].slice(0, 2000).map(element => element.href || element.action)];
  }
  function planHeader(raw) {
    const text = String(raw).replace(/\s+/g, "").replace(/[：:]$/, "").trim();
    const mapping = {"年级": "cohort", "入学年份": "cohort", "入学年": "cohort", "学院": "college_name", "所属学院": "college_name", "专业": "major_name", "专业名称": "major_name", "类（专业）": "major_or_category_raw", "类(专业)": "major_or_category_raw", "培养项目": "program", "项目": "program", "修读路径": "study_path_raw", "培养路径": "study_path_raw", "培养方案": "title", "培养方案名称": "title", "方案名称": "title"};
    return mapping[text] || null;
  }
  function planValue(key, raw) {
    const value = String(raw || "").replace(/\s+/g, " ").trim();
    if (key === "cohort") { const match = /^(19\d{2}|20\d{2}|21\d{2})(?:级|年)?$/.exec(value); return match ? Number(match[1]) : null; }
    return value || null;
  }
  function planHTTPContext() {
    const cohort = valueControl(["synj_cx"]), college = valueControl(["xydm_cx"]), major = valueControl(["zydm_cx"]);
    if (!cohort || !college || !major || /^(?:全部|所有|请选择|--)/.test(college.label) || /^(?:全部|所有|请选择|--)/.test(major.label)) return null;
    const year = planValue("cohort", cohort.label);
    if (year === null || String(year) !== cohort.value || !college.value || !major.value) return null;
    const query_values = {synj: cohort.value, xydm: college.value, zydm: major.value};
    for (const [source, parameter] of [["dlbs_cx", "dlbs"], ["gjzy_cx", "gjzydm"]]) {
      const observation = valueControl([source]);
      if (observation) query_values[parameter] = observation.value;
      else if (document.querySelector(`[name="${source}"],#${source}`)) return null;
    }
    return {filters: {cohort: year, college_name: college.label, major_name: major.label}, query_values};
  }
  function planFilters() {
    const filters = {};
    for (const control of [...document.querySelectorAll("select")].filter(visible)) {
      const label = control.id ? [...document.querySelectorAll("label[for]")].find(item => item.htmlFor === control.id) : null;
      let name = planHeader(label?.textContent || "");
      if (!name) {
        const parent = control.parentElement;
        const siblings = parent ? [...parent.children].filter(item => item !== control && item.tagName !== "SELECT") : [];
        name = siblings.map(item => planHeader(item.textContent)).find(Boolean);
      }
      if (!name || name === "title" || !control.selectedOptions.length) continue;
      const option = control.selectedOptions[0];
      if (/^(?:全部|所有|请选择|--)/.test(option.textContent.trim())) continue;
      filters[name] = planValue(name, option.textContent);
    }
    return filters;
  }
  function planTable() {
    const filters = planFilters();
    for (const table of [...document.querySelectorAll("table")].filter(visible)) {
      const jqGrid = table.closest(".ui-jqgrid");
      const header = table.querySelector("thead tr") || jqGrid?.querySelector(".ui-jqgrid-hdiv .ui-jqgrid-labels") || table.querySelector("tr");
      if (!header) continue;
      const keys = [...header.children].map(cell => planHeader(cell.textContent));
      if (!keys.includes("cohort") || !keys.includes("college_name") || !(keys.includes("major_name") || keys.includes("major_or_category_raw"))) continue;
      const rows = [];
      const visibleRows = [...table.querySelectorAll("tbody tr,tr")].filter(item => item !== header && visible(item));
      if (visibleRows.length > 100) return null;
      for (const row of visibleRows) {
        const cells = [...row.children]; if (cells.length !== keys.length || !cells.every(cell => ["TD", "TH"].includes(cell.tagName))) continue;
        const fields = {};
        for (let index = 0; index < keys.length; index++) if (keys[index]) fields[keys[index]] = planValue(keys[index], cells[index].textContent);
        if (!fields.title) {
          const sourceAnchor = [...row.querySelectorAll("a[href]")].find(item => visible(item) && /培养方案|方案/.test(item.textContent));
          if (sourceAnchor) fields.title = sourceAnchor.textContent.trim();
        }
        const links = [...row.querySelectorAll("a[href]")].filter(visible).map(anchor => { try { return core.pdfURL(anchor.href, location.href); } catch { return null; } }).filter(Boolean);
        rows.push({fields, links});
      }
      if (rows.length) return {filters, rows};
    }
    return null;
  }
  function catalogueScopeSignature(scope) {
    return JSON.stringify(["cohort", "college_name", "major_name", "major_or_category_raw", "program", "study_path_raw"].map(key => [key, scope[key] ?? null]));
  }
  function register(operation) {
    const state = pageState(); if (state) return {ok: false, state};
    if (operation === "plan_catalogue") {
      documents.clear(); catalogueSignature = null; catalogueRowsSignature = null; catalogueScope = null;
      if (location.pathname === core.PLAN_CATALOGUE_ROUTE) {
        const context = planHTTPContext(); if (!context) return {ok: false, state: "route_not_confirmed"};
        registrations.set(operation, {operation, route: core.PLAN_CATALOGUE_ROUTE, mode: "http", ...context, menu_code: core.observedMenuCode(location.href)});
      } else {
        const table = planTable(); if (!table) return {ok: false, state: "schema_changed"};
        registrations.set(operation, {operation, route: location.pathname, mode: "dom", filters: table.filters});
      }
    } else {
      if (!core.routeObserved(operation, evidence())) return {ok: false, state: "route_not_confirmed"};
      const filters = termFilters(operation); if (!filters) return {ok: false, state: "route_not_confirmed"};
      const view_mode = operation === "timetable" ? timetableView() : null;
      if (operation === "timetable" && view_mode === null) return {ok: false, state: "route_not_confirmed"};
      registrations.set(operation, {operation, route: core.DATA_ROUTES[operation], filters, view_mode, menu_code: core.observedMenuCode(location.href)});
    }
    return {ok: true, session_epoch: epoch, operations: [...registrations.keys()]};
  }
  function catalogue(spec) {
    const registration = registrations.get("plan_catalogue"), table = planTable();
    if (!registration || registration.route !== location.pathname || !table) return core.responseMeta("route_not_confirmed", epoch);
    const signature = catalogueScopeSignature(spec.scope);
    if (catalogueSignature !== null && catalogueSignature !== signature) documents.clear();
    catalogueSignature = signature;
    if (spec.page !== 1) return core.responseMeta("route_not_confirmed", epoch);
    try {
      if (table.rows.length > spec.page_size || !core.planScopeMatches(spec.scope, table.filters, table.rows.map(row => row.fields))) throw new Error("scope");
      documents.clear();
      catalogueScope = {...spec.scope};
      catalogueRowsSignature = JSON.stringify(table.rows);
      const rows = table.rows.map(row => {
        const ref = `PLAN-${crypto.randomUUID().replace(/-/g, "")}`;
        if (row.links.length === 1) documents.set(ref, row.links[0]);
        return {...row.fields, plan_ref: ref};
      });
      const items = core.projectPlanRows(rows, spec.scope, table.filters);
      return {...core.responseMeta("business", epoch, null, "dom_business_projection"), capture_method: "browser_dom", page_validation: {status: "visible_business_page", domain: "plan_catalogue"}, payload: {items, pageNo: 1}};
    } catch { documents.clear(); return core.responseMeta("route_not_confirmed", epoch); }
  }
  async function httpCatalogue(spec) {
    const registration = registrations.get("plan_catalogue"), context = planHTTPContext();
    if (registration?.mode !== "http" || location.pathname !== registration.route || !context || JSON.stringify(context.filters) !== JSON.stringify(registration.filters) || JSON.stringify(context.query_values) !== JSON.stringify(registration.query_values) || core.observedMenuCode(location.href) !== registration.menu_code) return core.responseMeta("route_not_confirmed", epoch);
    const signature = catalogueScopeSignature(spec.scope);
    if (spec.page === 1 || catalogueSignature !== signature) documents.clear();
    const result = await core.fetchPlanCatalogue(spec, registration, epoch);
    if (result.state === "business") {
      catalogueSignature = signature; catalogueScope = {...spec.scope};
      for (const [ref, url] of result.documents) documents.set(ref, url);
      const {documents: internalDocuments, ...response} = result;
      return response;
    }
    documents.clear(); catalogueScope = null; catalogueSignature = null;
    return result;
  }
  async function execute(raw) {
    if (busy) return core.responseMeta("source_unavailable", epoch);
    const state = pageState(); if (state) return core.responseMeta(state, epoch);
    let spec; try { spec = core.validateSpec(raw); } catch { return core.responseMeta("route_not_confirmed", epoch); }
    busy = true;
    try {
      let response;
      if (spec.operation === "plan_catalogue") response = registrations.get("plan_catalogue")?.mode === "http" ? await httpCatalogue(spec) : catalogue(spec);
      else if (spec.operation === "plan_document") {
        const registration = registrations.get("plan_catalogue");
        const effectiveScope = Object.keys(spec.scope).length ? spec.scope : catalogueScope;
        let valid = !!registration && !!effectiveScope && catalogueSignature === catalogueScopeSignature(effectiveScope);
        if (registration?.mode === "http") {
          const context = planHTTPContext();
          valid = valid && location.pathname === registration.route && !!context && JSON.stringify(context.filters) === JSON.stringify(registration.filters) && JSON.stringify(context.query_values) === JSON.stringify(registration.query_values) && core.observedMenuCode(location.href) === registration.menu_code && core.planScopeMatches(effectiveScope, context.filters, [context.filters]);
        } else {
          const table = planTable();
          valid = valid && !!table && catalogueRowsSignature === JSON.stringify(table.rows) && core.planScopeMatches(effectiveScope, table.filters, table.rows.map(row => row.fields));
        }
        if (!valid) { documents.clear(); return core.responseMeta("route_not_confirmed", epoch); }
        response = await core.fetchPlanDocument(spec, documents, epoch);
      } else {
        const registration = registrations.get(spec.operation), filters = termFilters(spec.operation);
        if (!registration || !filters || JSON.stringify(filters) !== JSON.stringify(registration.filters) || spec.operation === "timetable" && timetableView() !== registration.view_mode || core.observedMenuCode(location.href) !== registration.menu_code || !core.routeObserved(spec.operation, evidence())) return core.responseMeta("route_not_confirmed", epoch);
        response = await core.fetchBusiness(spec, registration, epoch);
      }
      if (response.source_subject_id !== null) {
        if (previousSubject !== null && previousSubject !== response.source_subject_id) { registrations.clear(); documents.clear(); return core.responseMeta("source_subject_changed", epoch); }
        previousSubject = response.source_subject_id;
      }
      if (["awaiting_user_login", "source_session_expired", "captcha_required", "permission_denied", "source_subject_changed"].includes(response.state)) { registrations.clear(); documents.clear(); }
      return response;
    } finally { busy = false; }
  }
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || !message || typeof message !== "object") return;
    if (message.kind === "inspect") sendResponse({ok: true, session_epoch: epoch, operations: [...registrations.keys()], state: pageState() || "ready"});
    else if (message.kind === "register") sendResponse(register(message.operation));
    else if (message.kind === "execute" && message.session_epoch === epoch) { execute(message.spec).then(sendResponse); return true; }
    else if (message.kind === "execute") sendResponse(core.responseMeta("source_session_expired", epoch));
  });
  // A message wakes the ephemeral worker; neither body nor credentials leave here
  // unless an explicitly paired broker has a queued, bounded job for this tab.
  setInterval(() => chrome.runtime.sendMessage({kind: "heartbeat", session_epoch: epoch}).catch(() => {}), 5000);
})();
