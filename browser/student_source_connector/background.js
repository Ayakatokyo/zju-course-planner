/* MV3 worker. Pairing token is held only in this worker's memory. */
"use strict";
importScripts("core.js");
const core = StudentConnector;
let connection = null, polling = false, pinging = false, lastState = "disconnected";
function popupSender(sender) { return sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("popup.html"); }
function validToken(token) { return typeof token === "string" && /^[A-Za-z0-9_-]{32,256}$/.test(token); }
function disconnect(state = "disconnected") { connection = null; lastState = state; }
async function endConnection(state = "disconnected") {
  const current = connection;
  disconnect(state);
  if (current) {
    try { await localRequest(current.port, "/disconnect", {session_epoch: current.epoch}, current.token); } catch { /* Local authority is already discarded. Broker expires after heartbeat loss. */ }
  }
  return {ok: true, state};
}
async function activeOfficialTab() {
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  const tab = tabs[0];
  if (!tab || !Number.isInteger(tab.id) || !tab.url || new URL(tab.url).origin !== core.ORIGIN) throw new Error("official_tab_required");
  return tab;
}
async function currentContext() {
  const tab = await activeOfficialTab();
  const context = await chrome.tabs.sendMessage(tab.id, {kind: "inspect"});
  if (!context?.ok || typeof context.session_epoch !== "string") throw new Error("source_session_expired");
  return {tab, context};
}
async function localRequest(port, path, body, token) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), core.TIMEOUT_MS);
  try {
    const headers = {"Content-Type": "application/json"};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(core.loopbackURL(port, path), {method: "POST", mode: "cors", credentials: "omit", cache: "no-store", redirect: "error", headers, body: JSON.stringify(body), signal: controller.signal});
    if (response.status !== 200) throw new Error("broker_rejected");
    const bytes = await core.readLimited(response, core.JSON_LIMIT);
    return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
  } finally { clearTimeout(timer); }
}
async function pair(message) {
  if (connection) return {ok: false, state: "already_connected"};
  disconnect();
  const {tab, context} = await currentContext();
  if (!context.operations?.length || context.state !== "ready") throw new Error("source_page_not_confirmed");
  if (typeof message.code !== "string" || !/^[A-Za-z0-9_-]{6,128}$/.test(message.code)) throw new Error("invalid_pairing_code");
  const port = Number(message.port); core.loopbackURL(port, "/pair");
  const result = await localRequest(port, "/pair", {code: message.code, session_epoch: context.session_epoch});
  if (!validToken(result.token) || result.session_epoch !== context.session_epoch) throw new Error("broker_rejected");
  connection = {port, token: result.token, tabId: tab.id, epoch: context.session_epoch};
  lastState = "connected";
  return {ok: true, state: lastState};
}
async function poll(sender, epoch) {
  const current = connection;
  if (!current || polling || sender.tab?.id !== current.tabId || sender.frameId !== 0) return;
  if (epoch !== current.epoch) { await endConnection("source_session_expired"); return; }
  polling = true;
  try {
    const next = await localRequest(current.port, "/next", {session_epoch: current.epoch}, current.token);
    if (!connection || connection !== current) return;
    if (next.job === null) return;
    if (!next.job || typeof next.job.id !== "string" || next.job.id.length > 100 || !next.job.spec) throw new Error("broker_rejected");
    let response;
    try {
      response = await chrome.tabs.sendMessage(current.tabId, {kind: "execute", session_epoch: current.epoch, spec: next.job.spec});
      if (!response || response.session_epoch !== current.epoch) response = core.responseMeta("source_session_expired", current.epoch);
    } catch { response = core.responseMeta("source_session_expired", current.epoch); }
    await localRequest(current.port, "/result", {id: next.job.id, response}, current.token);
    lastState = response.state;
    if (["awaiting_user_login", "source_session_expired", "captcha_required", "permission_denied", "source_subject_changed", "user_confirmation_required"].includes(response.state) && connection === current) await endConnection(response.state);
  } catch { if (connection === current) await endConnection("broker_unavailable"); }
  finally { polling = false; }
}
async function heartbeat(sender, epoch) {
  const current = connection;
  if (!current || sender.tab?.id !== current.tabId || sender.frameId !== 0) return;
  if (epoch !== current.epoch) { await endConnection("source_session_expired"); return; }
  if (pinging) return;
  pinging = true;
  try {
    await localRequest(current.port, "/heartbeat", {session_epoch: current.epoch}, current.token);
    if (connection === current) poll(sender, epoch);
  } catch { if (connection === current) await endConnection("broker_unavailable"); }
  finally { pinging = false; }
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object" || sender.id !== chrome.runtime.id) return;
  if (message.kind === "heartbeat") { heartbeat(sender, message.session_epoch); return; }
  if (!popupSender(sender)) return;
  if (message.kind === "status") sendResponse({ok: true, state: lastState, connected: connection !== null, extension_id: chrome.runtime.id});
  else if (message.kind === "disconnect") { endConnection().then(sendResponse); return true; }
  else if (message.kind === "register") {
    currentContext().then(({tab}) => chrome.tabs.sendMessage(tab.id, {kind: "register", operation: message.operation})).then(sendResponse).catch(() => sendResponse({ok: false, state: "official_tab_required"}));
    return true;
  } else if (message.kind === "pair") {
    pair(message).then(sendResponse).catch(error => { disconnect(); sendResponse({ok: false, state: ["official_tab_required", "source_session_expired", "source_page_not_confirmed", "invalid_pairing_code"].includes(error.message) ? error.message : "broker_unavailable"}); });
    return true;
  }
});
chrome.tabs.onRemoved.addListener(tabId => { if (connection?.tabId === tabId) endConnection("source_session_expired"); });
chrome.tabs.onUpdated.addListener((tabId, change) => { if (connection?.tabId === tabId && change.status === "loading") endConnection("source_session_expired"); });
