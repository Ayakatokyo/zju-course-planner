"""Local public-client Sign in with ChatGPT. Never exposes tokens to the UI.

One independently authorized ChatGPT registration per academic owner. The
loopback callback is not a school login and cannot grant school access.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener
import uuid

from .auth import AccessError, is_academic_context
from .model_client import ModelError, NoRedirect, strict_json

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DIRECT = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + DIRECT
LEVELS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def jwt_library():
    # Optional isolated dependency, also usable from a normal installed venv.
    runtime = Path(__file__).resolve().parents[1] / "private/chatgpt_runtime/site-packages"
    if runtime.is_dir() and str(runtime) not in sys.path:
        sys.path.append(str(runtime))
    try:
        import jwt
        from cryptography.hazmat.primitives import serialization  # noqa: F401
        return jwt
    except ImportError:
        raise ModelError("ChatGPT 登录依赖未安装，请按 README 安装", "chatgpt_dependency_missing") from None


def official_url(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    if (not parsed or parsed.scheme != "https" or parsed.netloc != "auth.openai.com"
            or parsed.fragment or parsed.query):
        raise ModelError("OpenAI 身份服务配置无效", "chatgpt_identity_invalid")
    return value


def atomic_private(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ModelError("ChatGPT 本机凭据目录无效", "chatgpt_store_invalid")
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".oauth-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ChatGPTAccounts:
    def __init__(self, auth, root, *, opener=None, clock=time.time):
        self.auth, self.root, self.clock = auth, Path(root), clock
        self.opener = opener or build_opener(NoRedirect())
        self.lock = threading.RLock()
        self.accounts, self.pending, self.errors, self.routers = {}, {}, {}, {}
        self.discovery = None

    def close(self):
        with self.lock:
            for owner in list(self.pending):
                self._cancel(owner)

    def owner(self, context):
        self.auth.require_context(context)
        if not is_academic_context(context):
            raise AccessError("请先登录教务助手", 403, "school_connection_required")
        return hashlib.sha256(json.dumps([context.security_realm, context.student_id]).encode()).hexdigest()

    def _record(self, owner):
        if self.root.is_symlink():
            raise ModelError("ChatGPT 本机凭据目录无效", "chatgpt_store_invalid")
        if owner not in self.accounts:
            path = self.root / (owner + ".json")
            try:
                if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                    raise ValueError()
                os.chmod(path, 0o600)
                record = strict_json(path.read_text())
                if not isinstance(record, dict):
                    raise ValueError()
                self.accounts[owner] = record
            except FileNotFoundError:
                self.accounts[owner] = {}
            except (OSError, ValueError):
                raise ModelError("ChatGPT 本机凭据无法读取，请检查文件权限", "chatgpt_store_invalid") from None
        return self.accounts[owner]

    def _save(self, owner, record):
        atomic_private(self.root / (owner + ".json"), record)
        self.accounts[owner] = record

    def _json(self, url, *, form=None, bearer=None):
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urlencode(form).encode()
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=30) as response:
                raw = response.read(1048577)
            if not raw and form is not None and "token_type_hint" in form:
                return {}
            if len(raw) > 1048576:
                raise ValueError()
            body = strict_json(raw)
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except HTTPError as error:
            code = "chatgpt_http_" + str(error.code)
            if error.code in (400, 401):
                try:
                    body = strict_json(error.read(16384))
                    if body.get("error") == "invalid_grant":
                        code = "chatgpt_reconnect_required"
                except Exception:
                    pass
            status = error.code
            error.close()
            raise ModelError("ChatGPT 请求未成功，请重新连接或稍后重试", code, http_status=status) from None
        except ModelError:
            raise
        except (URLError, TimeoutError, OSError):
            raise ModelError("ChatGPT 服务暂时无法连接", "chatgpt_unavailable") from None
        except (ValueError, TypeError):
            raise ModelError("ChatGPT 返回格式无效", "chatgpt_protocol_error") from None

    def _discovery(self):
        if self.discovery is None:
            value = self._json(AUTH + "/.well-known/openid-configuration")
            if value.get("issuer") != AUTH:
                raise ModelError("OpenAI 身份服务校验失败", "chatgpt_identity_invalid")
            official_url(value.get("jwks_uri"))
            self.discovery = value
        return self.discovery

    def _identity(self, token, client_id, *, nonce=None, subject=None):
        jwt = jwt_library()
        try:
            discovery = self._discovery()
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"RS256", "ES256"}:
                raise ValueError()
            # Fresh published keys allow rotation; never follow token-supplied URLs.
            jwks = self._json(discovery["jwks_uri"])
            keys = [key for key in jwks["keys"] if key.get("kid") == header.get("kid")]
            if len(keys) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(keys[0], algorithm=header["alg"])
            claims = jwt.decode(token, key.key, algorithms=[header["alg"]], audience=client_id,
                                issuer=AUTH, options={"require": ["exp", "iat", "sub", "iss", "aud"]})
            if (not isinstance(claims.get("sub"), str) or not claims["sub"]
                    or (nonce is not None and claims.get("nonce") != nonce)
                    or (subject is not None and claims["sub"] != subject)):
                raise ValueError()
            return claims["sub"]
        except ModelError:
            raise
        except Exception:
            raise ModelError("ChatGPT 身份校验失败，请重新授权", "chatgpt_identity_invalid") from None

    def status(self, context):
        owner = self.owner(context)
        with self.lock:
            record = self._record(owner)
            return {"connected": bool(record.get("access_token")) and not record.get("reconnect", False),
                    "pending": owner in self.pending, "error": self.errors.get(owner, ""),
                    "connection_revision": record.get("revision", ""),
                    "plan_usage": DIRECT in record.get("scopes", [])}

    def _host(self):
        path = self.root / "host.json"
        if path.exists():
            if path.is_symlink():
                raise ModelError("ChatGPT 主机标识无效", "chatgpt_store_invalid")
            host = strict_json(path.read_text()).get("host_id", "")
            if not re.fullmatch(r"urn:uuid:[a-f0-9-]{36}", host):
                raise ModelError("ChatGPT 主机标识无效", "chatgpt_store_invalid")
            return host
        host = "urn:uuid:" + str(uuid.uuid4())
        atomic_private(path, {"host_id": host})
        return host

    def begin(self, context):
        owner = self.owner(context)
        jwt_library()
        with self.lock:
            self._cancel(owner)
            if len(self.pending) >= 8:
                raise ModelError("待授权连接过多，请稍后重试", "chatgpt_attempt_limit")
            record = self._record(owner)
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            service = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    valid_host = self.headers.get("Host") == "127.0.0.1:" + str(self.server.server_port)
                    try:
                        if not valid_host or len(self.path) > 16384 or urlsplit(self.path).path != "/auth/callback":
                            raise ValueError()
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=20)
                        if any(len(values) != 1 for values in query.values()):
                            raise ValueError()
                        service.finish(owner, {key: values[0] for key, values in query.items()})
                        message = "ChatGPT 已连接。请关闭此页，返回浙大教务助手选择模型。"
                        status = 200
                    except Exception:
                        message = "授权未完成。请返回浙大教务助手查看状态并重试。"
                        status = 400
                    body = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>浙大教务助手</title>"
                            "<h1>" + message + "</h1></html>").encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(body)

            listener = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
            listener.daemon_threads = True
            redirect = "http://127.0.0.1:" + str(listener.server_port) + "/auth/callback"
            attempt = {"state": state, "nonce": nonce, "verifier": verifier, "redirect": redirect,
                       "context": context, "expires": self.clock() + 600, "listener": listener,
                       "client_id": record.get("client_id"), "subject": record.get("subject")}
            self.pending[owner] = attempt
            self.errors.pop(owner, None)
            threading.Thread(target=listener.serve_forever, daemon=True).start()
            timer = threading.Timer(600, self._expire, args=(owner, state))
            timer.daemon = True
            timer.start()
            attempt["timer"] = timer
            params = {"client_id": record.get("client_id") or "dynamic_agent_client", "ext_agent_host_id": self._host(),
                      "response_type": "code", "scope": SCOPES, "resource": RESOURCE, "redirect_uri": redirect,
                      "state": state, "nonce": nonce, "code_challenge_method": "S256",
                      "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
            if not record.get("client_id"):
                params["agent_name_hint"] = "浙大教务助手"
            # No token hint in a frontend-visible URL. Official account chooser is used.
            return {"authorization_url": AUTH + "/api/accounts/authorize?" + urlencode(params), "expires_in": 600}

    def _cancel(self, owner):
        attempt = self.pending.pop(owner, None)
        if attempt:
            attempt.get("timer") and attempt["timer"].cancel()
            listener = attempt["listener"]
            def close():
                listener.shutdown()
                listener.server_close()
            threading.Thread(target=close, daemon=True).start()

    def _expire(self, owner, state):
        with self.lock:
            if self.pending.get(owner, {}).get("state") == state:
                self._cancel(owner)
                self.errors[owner] = "授权已超时，请重新连接 ChatGPT。"

    def _tokens(self, body, client_id, *, previous=None, nonce=None):
        old = previous or {}
        if (not isinstance(body.get("access_token"), str) or not body["access_token"]
                or len(body["access_token"]) > 32768 or any(c.isspace() for c in body["access_token"])
                or type(body.get("expires_in")) is not int or not 0 < body["expires_in"] <= 86400 * 30
                or body.get("token_type", "").lower() != "bearer"):
            raise ModelError("ChatGPT 令牌返回不完整", "chatgpt_protocol_error")
        scopes = body.get("scope", " ".join(old.get("scopes", [])))
        if not isinstance(scopes, str) or DIRECT not in scopes.split():
            raise ModelError("尚未授权使用 ChatGPT 订阅额度，请重新连接并授权", "chatgpt_scope_missing")
        refresh = body.get("refresh_token", old.get("refresh_token"))
        if not isinstance(refresh, str) or not 0 < len(refresh) <= 32768:
            raise ModelError("ChatGPT 未提供续期凭据，请重新授权", "chatgpt_protocol_error")
        id_token = body.get("id_token")
        if nonce is not None and not id_token:
            raise ModelError("ChatGPT 未提供身份凭据", "chatgpt_identity_invalid")
        subject = self._identity(id_token, client_id, nonce=nonce, subject=old.get("subject")) if id_token else old["subject"]
        return {"client_id": client_id, "subject": subject, "id_token": id_token or old.get("id_token"),
                "access_token": body["access_token"], "refresh_token": refresh, "scopes": scopes.split(),
                "expires_at": self.clock() + body["expires_in"], "revision": old.get("revision") or uuid.uuid4().hex,
                "models": old.get("models", [])}

    def finish(self, owner, query):
        with self.lock:
            attempt = self.pending.get(owner)
            if (not attempt or self.clock() >= attempt["expires"] or not isinstance(query.get("state"), str)
                    or not secrets.compare_digest(query["state"], attempt["state"])):
                raise ModelError("ChatGPT 授权状态无效", "chatgpt_state_invalid")
            self._cancel(owner)  # consume once, including denial or failed exchange
            try:
                self.owner(attempt["context"])
                if query.get("error"):
                    raise ModelError("ChatGPT 授权已取消，请重新连接", "chatgpt_denied")
                client_id = query.get("client_id") or attempt["client_id"]
                if (not isinstance(client_id, str) or not re.fullmatch(r"oaiapp_[A-Za-z0-9_-]{1,200}", client_id)
                        or (attempt["client_id"] and client_id != attempt["client_id"])
                        or not isinstance(query.get("code"), str) or not 0 < len(query["code"]) <= 4096):
                    raise ModelError("ChatGPT 注册未完成，请重新连接", "chatgpt_registration_invalid")
                body = self._json(AUTH + "/api/accounts/oauth/token", form={"grant_type": "authorization_code",
                    "client_id": client_id, "code": query["code"], "code_verifier": attempt["verifier"],
                    "redirect_uri": attempt["redirect"], "resource": RESOURCE})
                record = self._tokens(body, client_id, previous={"subject": attempt["subject"]}, nonce=attempt["nonce"])
                self.owner(attempt["context"])
                self._save(owner, record)
                self.errors.pop(owner, None)
            except (ModelError, AccessError) as error:
                self.errors[owner] = str(error) if isinstance(error, ModelError) else "教务助手登录已失效，请登录后重新连接 ChatGPT。"
                raise
            except Exception:
                self.errors[owner] = "ChatGPT 授权未能保存，请重试。"
                raise ModelError(self.errors[owner], "chatgpt_store_invalid") from None

    def access(self, context, revision):
        owner = self.owner(context)
        with self.lock:
            record = self._record(owner)
            if record.get("revision", "") != revision:
                raise ModelError("ChatGPT 连接已变化，请重新发送", "personal_model_changed")
            if record.get("reconnect") or not record.get("refresh_token") or DIRECT not in record.get("scopes", []):
                raise ModelError("请先连接 ChatGPT 并授权订阅额度", "chatgpt_reconnect_required")
            if record["expires_at"] <= self.clock() + 90:
                try:
                    body = self._json(AUTH + "/api/accounts/oauth/token", form={"grant_type": "refresh_token",
                        "client_id": record["client_id"], "refresh_token": record["refresh_token"], "resource": RESOURCE})
                    record = self._tokens(body, record["client_id"], previous=record)
                    # Save rotating credentials even if the school session expired during refresh;
                    # authority is checked again before returning the access token.
                    self._save(owner, record)
                except ModelError as error:
                    if error.code in {"chatgpt_reconnect_required", "chatgpt_scope_missing"}:
                        record = {**record, "reconnect": True, "revision": uuid.uuid4().hex}
                        self._save(owner, record)
                        self.errors[owner] = "ChatGPT 授权已失效，请重新连接。"
                    raise
            self.owner(context)
            return record["access_token"]

    def models(self, context, refresh=False):
        owner = self.owner(context)
        with self.lock:
            record = self._record(owner)
            if not self.status(context)["connected"]:
                return []
            if refresh:
                token = self.access(context, record["revision"])
                body = self._json(RESOURCE + "/models", bearer=token)
                rows = body.get("models")
                if not isinstance(rows, list) or len(rows) > 500:
                    raise ModelError("ChatGPT 模型目录格式无效", "chatgpt_protocol_error")
                models, seen = [], set()
                for row in rows:
                    if not isinstance(row, dict) or row.get("visibility") != "list":
                        continue
                    slug, name = row.get("slug"), row.get("display_name")
                    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,90}", slug) or slug in seen:
                        raise ModelError("ChatGPT 模型目录格式无效", "chatgpt_protocol_error")
                    seen.add(slug)
                    levels = row.get("supported_reasoning_levels", [])
                    levels = [v.get("effort") if isinstance(v, dict) else v for v in levels] if isinstance(levels, list) else []
                    effort = row.get("effort", {})
                    if not levels and isinstance(effort, dict):
                        levels = effort.get("supported_levels", [])
                    levels = list(dict.fromkeys(v for v in levels if isinstance(v, str) and v in LEVELS)) if isinstance(levels, list) else []
                    default = row.get("default_reasoning_level", effort.get("default_level") if isinstance(effort, dict) else None)
                    models.append({"id": "chatgpt:" + slug, "name": name if isinstance(name, str) and 0 < len(name) <= 150 and not any(ord(c)<32 for c in name) else slug,
                                   "provider": "ChatGPT", "effort": {"supported_levels": levels,
                                   "default_level": default if default in levels else (levels[0] if levels else "default")},
                                   "thinking_toggle": "none" in levels})
                self.owner(context)
                record = {**self._record(owner), "models": models}
                self._save(owner, record)
            return deepcopy(record.get("models", []))

    def disconnect(self, context):
        owner = self.owner(context)
        with self.lock:
            self._cancel(owner)
            record = self._record(owner)
            revoked = not bool(record.get("refresh_token"))
            try:
                if record.get("refresh_token"):
                    endpoint = official_url(self._discovery().get("revocation_endpoint"))
                    self._json(endpoint, form={"token": record["refresh_token"], "token_type_hint": "refresh_token",
                                               "client_id": record["client_id"]})
                    revoked = True
            except ModelError:
                pass
            self._save(owner, {"revision": uuid.uuid4().hex})
            self.errors[owner] = "" if revoked else "本机连接已移除；远端撤销未确认，可在 ChatGPT 账户中移除此应用授权。"
            return self.status(context)

    def model_for(self, context, base):
        owner = self.owner(context)
        with self.lock:
            # Per-session router: auth revalidation must use this exact session.
            key = (owner, context.session_ref, context.binding_version, id(base))
            if key not in self.routers:
                if len(self.routers) >= 256:
                    self.routers.clear()
                from .chatgpt_client import ModelRouter
                self.routers[key] = ModelRouter(self, context, base)
            return self.routers[key]
