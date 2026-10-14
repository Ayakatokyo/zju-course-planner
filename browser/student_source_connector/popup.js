"use strict";
const statusElement = document.getElementById("status");
const descriptions = {disconnected: "未连接", connected: "已连接当前官方标签", already_connected: "已有连接，请先断开，再获取新的连接码", business: "业务投影已交给本机任务", ready: "来源页已确认", official_tab_required: "请切回官方教务标签后操作", source_page_not_confirmed: "请先确认当前来源页", invalid_pairing_code: "连接码格式不正确", broker_unavailable: "本机连接不可用，请检查服务并重新连接", awaiting_user_login: "请在官方页面登录", source_session_expired: "来源会话已失效，请重新登录并连接", captcha_required: "请在官方页面完成验证码", permission_denied: "来源拒绝访问，连接已停止", source_subject_changed: "来源身份变化，连接已停止", user_confirmation_required: "请自行确认官方页面的个人承诺", source_unavailable: "来源暂不可用", schema_changed: "页面或接口字段尚未确认", route_not_confirmed: "当前来源页或查询范围尚未确认"};
function show(result) { statusElement.textContent = descriptions[result?.state] || (result?.ok ? "来源页已确认" : "操作未完成"); }
async function send(message) { try { const result = await chrome.runtime.sendMessage(message); show(result); return result; } catch { show({state: "broker_unavailable"}); return null; } }
document.getElementById("register").addEventListener("click", () => send({kind: "register", operation: document.getElementById("operation").value}));
document.getElementById("open-pair").addEventListener("click", () => {
  try { chrome.tabs.create({url: StudentConnector.loopbackURL(document.getElementById("port").value, "/pair")}); }
  catch { show({state: "broker_unavailable"}); }
});
document.getElementById("pair").addEventListener("click", async () => {
  const code = document.getElementById("code");
  await send({kind: "pair", port: document.getElementById("port").value, code: code.value.trim()});
  code.value = "";
});
document.getElementById("disconnect").addEventListener("click", () => send({kind: "disconnect"}));
send({kind: "status"}).then(result => { document.getElementById("extension-id").textContent = result?.extension_id || ""; });
