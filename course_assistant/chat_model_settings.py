"""Session-scoped model options; discovery never carries conversation data."""
from copy import deepcopy
import threading

from .auth import AccessError, is_academic_context
from .model_client import ModelError


class ChatModelSettings:
    def __init__(self, auth, get_model, resolver=None):
        self.auth, self.get_model, self.resolver = auth, get_model, resolver
        self._lock = threading.RLock()
        self._options, self._revisions, self._catalog, self._catalog_client = {}, {}, None, None

    def _base(self, context):
        return self.resolver(context) if self.resolver else self.get_model()

    def _key(self, context):
        self.auth.require_context(context)
        if not is_academic_context(context):
            raise AccessError("请先连接学校账号", 403, "school_connection_required")
        return (context.security_realm, context.student_id, context.session_ref, context.binding_version)

    def client(self, context):
        return self.snapshot(context)[0]

    def snapshot(self, context):
        key = self._key(context)
        with self._lock:
            base = self._base(context)
            status = base.status()
            options = deepcopy(self._options.get(key)) or {"model": status.get("model"),
                "thinking": status.get("thinking") is True or status.get("provider") == "DeepSeek"}
            revision = self._revisions.get(key, 0)
            client = base.with_options(**options) if callable(getattr(base, "with_options", None)) else base
            selected = {**client.status(), "settings_revision": revision}
        return client, selected, revision

    def catalog(self, context):
        self._key(context)
        base, selected = self._base(context), self.client(context).status()
        with self._lock:
            cached = deepcopy(self._catalog) if self._catalog_client is base else None
        local = base.local_catalog() if callable(getattr(base, "local_catalog", None)) else None
        result = cached or local or {"models": ([{"id": selected["model"], "name": selected["model"]}]
                                      if selected.get("model") else []),
                            "source": "configured", "status": "not_refreshed"}
        if local is not None and cached:
            defaults = {row["id"]: row for row in local["models"]}
            result["models"] = [{**defaults.get(row["id"], {}), **row} for row in result["models"] if not row["id"].startswith("chatgpt:")]
            result["models"] += [row for row in local["models"] if row["id"].startswith("chatgpt:")]
        self._key(context)
        return {**result, "configured": selected.get("configured") is True,
                "provider": selected.get("provider"), "selected_model": selected.get("model"),
                "thinking": selected.get("thinking") is True, "reasoning_effort": selected.get("reasoning_effort")}

    def refresh(self, context):
        self._key(context)
        base = self._base(context)
        method = getattr(base, "list_models", None)
        if not callable(method):
            raise AccessError("当前模型适配器不支持查询目录", 409, "model_catalog_unavailable")
        try:
            result = method()
        except ModelError as error:
            raise AccessError(str(error), 503, error.code) from None
        self._key(context)
        if self._base(context) is not base:
            raise AccessError("模型配置已变化，请重新刷新目录", 409, "personal_model_changed")
        with self._lock:
            self._catalog = {"models": deepcopy(result["models"]), "source": "provider", "status": "ready", "warnings": result.get("warnings", [])}
            self._catalog_client = base
        return self.catalog(context)

    def update(self, context, body):
        key = self._key(context)
        if (type(body) is not dict or set(body) not in ({"model", "thinking"}, {"model", "thinking", "reasoning_effort"})
                or type(body["model"]) is not str or type(body["thinking"]) is not bool):
            raise AccessError("模型设置格式无效", 400, "model_options_invalid")
        catalog = self.catalog(context)
        if body["model"] not in {item["id"] for item in catalog["models"]}:
            raise AccessError("请从实际模型目录中选择", 400, "model_not_in_catalog")
        if "reasoning_effort" in body:
            row = next(item for item in catalog["models"] if item["id"] == body["model"])
            levels = row.get("effort", {}).get("supported_levels", [])
            if type(body["reasoning_effort"]) is not str or body["reasoning_effort"] not in (levels or ["default"]):
                raise AccessError("请从该模型支持的思考强度中选择", 400, "model_effort_invalid")
        base = self._base(context)
        if not callable(getattr(base, "with_options", None)):
            raise AccessError("当前模型适配器不支持切换", 409, "model_options_unsupported")
        try:
            base.with_options(**body)
        except ModelError as error:
            raise AccessError(str(error), 400, error.code) from None
        self._key(context)
        with self._lock:
            if key not in self._options and len(self._options) >= 256:
                raise AccessError("模型会话设置已达本机上限，请重启后重试", 503, "model_settings_limit")
            self._options[key] = deepcopy(body)
            self._revisions[key] = self._revisions.get(key, 0) + 1
