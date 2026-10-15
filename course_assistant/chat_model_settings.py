"""Session-scoped model options; discovery never carries conversation data."""
from copy import deepcopy
import threading

from .auth import AccessError, SCHOOL_MODE
from .model_client import ModelError


class ChatModelSettings:
    def __init__(self, auth, get_model):
        self.auth, self.get_model = auth, get_model
        self._lock = threading.RLock()
        self._options, self._revisions, self._catalog, self._catalog_client = {}, {}, None, None

    def _key(self, context):
        self.auth.require_context(context)
        if context.auth_mode != SCHOOL_MODE:
            raise AccessError("请先连接学校账号", 403, "school_connection_required")
        return (context.security_realm, context.student_id, context.session_ref, context.binding_version)

    def client(self, context):
        return self.snapshot(context)[0]

    def snapshot(self, context):
        key = self._key(context)
        with self._lock:
            base = self.get_model()
            status = base.status()
            options = deepcopy(self._options.get(key)) or {"model": status.get("model"),
                "thinking": status.get("thinking") is True or status.get("provider") == "DeepSeek"}
            revision = self._revisions.get(key, 0)
            client = base.with_options(**options) if callable(getattr(base, "with_options", None)) else base
            selected = {**client.status(), "settings_revision": revision}
        return client, selected, revision

    def catalog(self, context):
        self._key(context)
        base, selected = self.get_model(), self.client(context).status()
        with self._lock:
            cached = deepcopy(self._catalog) if self._catalog_client is base else None
        result = cached or {"models": ([{"id": selected["model"], "name": selected["model"]}]
                                      if selected.get("model") else []),
                            "source": "configured", "status": "not_refreshed"}
        self._key(context)
        return {**result, "configured": selected.get("configured") is True,
                "provider": selected.get("provider"), "selected_model": selected.get("model"),
                "thinking": selected.get("thinking") is True}

    def refresh(self, context):
        self._key(context)
        base = self.get_model()
        method = getattr(base, "list_models", None)
        if not callable(method):
            raise AccessError("当前模型适配器不支持查询目录", 409, "model_catalog_unavailable")
        try:
            result = method()
        except ModelError as error:
            raise AccessError(str(error), 503, error.code) from None
        self._key(context)
        if self.get_model() is not base:
            raise AccessError("模型配置已变化，请重新刷新目录", 409, "personal_model_changed")
        with self._lock:
            self._catalog = {"models": deepcopy(result["models"]), "source": "provider", "status": "ready"}
            self._catalog_client = base
        return self.catalog(context)

    def update(self, context, body):
        key = self._key(context)
        if (type(body) is not dict or set(body) != {"model", "thinking"}
                or type(body["model"]) is not str or type(body["thinking"]) is not bool):
            raise AccessError("模型设置格式无效", 400, "model_options_invalid")
        catalog = self.catalog(context)
        if body["model"] not in {item["id"] for item in catalog["models"]}:
            raise AccessError("请从实际模型目录中选择", 400, "model_not_in_catalog")
        base = self.get_model()
        if not callable(getattr(base, "with_options", None)):
            raise AccessError("当前模型适配器不支持切换", 409, "model_options_unsupported")
        try:
            base.with_options(body["model"], body["thinking"])
        except ModelError as error:
            raise AccessError(str(error), 400, error.code) from None
        self._key(context)
        with self._lock:
            if key not in self._options and len(self._options) >= 256:
                raise AccessError("模型会话设置已达本机上限，请重启后重试", 503, "model_settings_limit")
            self._options[key] = deepcopy(body)
            self._revisions[key] = self._revisions.get(key, 0) + 1
