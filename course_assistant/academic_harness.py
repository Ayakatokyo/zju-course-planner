"""Per-turn execution boundary between the agent and authenticated data adapters.

The model chooses registered business tools. The harness owns current-turn
receipts, source dependencies, policy context and execution events. It never
accepts identity or a file path from the model, opens a new school endpoint,
reuses old facts, or makes the solver's authorization/feasibility decisions.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
import re
import time

from .auth import AccessError
from .chat_runs import tool_event_fields
from .personal_acquisition import prepare_recommendation_sources
from .personal_tasks import PREPARE_RECOMMENDATION_TOOL, validate_recommendation_task

VERSION = "academic-harness-v2"
COMPUTE_TOOLS = frozenset({"generate_current_term_plan", "update_current_term_plan"})


def resource_catalog(context):
    """Describe routes, not fetched facts, using only the projected preflight."""
    capabilities = context.get("capabilities", {})
    resources = []
    for operation in context.get("api_operations", []):
        if operation in {"academic", "major_academic", "timetable", "exams"}:
            resources.append({"resource": operation, "tool": "query_my_records",
                "freshness": "read_on_demand_each_turn", "scope": "ALL-HISTORY" if operation == "major_academic" else "explicit_term"})
    if capabilities.get("curriculum_local_search") is True:
        resources.append({"resource": "curriculum", "tool": "search_my_curriculum", "freshness": "read_on_demand_each_turn", "scope": "authenticated_student"})
    if capabilities.get("course_catalog_search") is True:
        resources.append({"resource": "course_names", "tool": "search_course_catalog", "freshness": "local_reference", "scope": "configured_catalog"})
    course = context.get("course_data_test", {})
    if capabilities.get("course_data_test") is True and isinstance(course, dict) and isinstance(course.get("target_term"), str):
        resources.append({"resource": "course_sections", "tool": "search_current_term_courses", "freshness": "local_historical_observations", "scope": course["target_term"]})
    return {"version": VERSION, "resources": resources,
            "compute_dependencies_managed": capabilities.get("harness_managed_dependencies") is True,
            "policy": "query_only_needed_resources; no_cross_turn_facts"}


class AcademicHarness:
    """One authenticated turn; a fresh instance is required on every user send."""
    def __init__(self, school, context, *, authorize, generation_authorize, adopt, emit,
                 user_message, task_context=None, clarification_context=None, active_plan_ref=None):
        self.school, self.context = school, context
        self.authorize, self.generation_authorize, self.adopt, self.emit = authorize, generation_authorize, adopt, emit
        self.receipts = []
        self.clarification_context = deepcopy(clarification_context)
        self.policy_context = {"user_message": user_message, "hard_preferences": [], "soft_preferences": [],
            "active_plan_ref": active_plan_ref, "pending_messages": []}
        # An owned plan already carries canonical constraints. Do not replay
        # an older model-authored preparation summary over that state.
        for task in (task_context or {}).get("tasks", []) if active_plan_ref is None else []:
            if task.get("tool") == PREPARE_RECOMMENDATION_TOOL:
                self.policy_context["hard_preferences"] = deepcopy(task["hard_preferences"])
                self.policy_context["soft_preferences"] = deepcopy(task.get("soft_preferences", []))
        self.live_tool = getattr(school, "personal_chat_live_tool", None)
        try:
            self.supports_policy = callable(self.live_tool) and "policy_context" in inspect.signature(self.live_tool).parameters
        except (TypeError, ValueError):
            self.supports_policy = False

    def _invoke(self, name, args):
        if not callable(self.live_tool):
            return self.school.personal_chat_tool(self.context, name, args)
        kwargs = {"receipts": self.receipts, "emit": self.emit, "authorize": self.generation_authorize,
                  "adopt": self.adopt, "before": self.authorize}
        if self.supports_policy:
            kwargs["policy_context"] = deepcopy(self.policy_context)
        result = self.live_tool(self.context, name, args, **kwargs)
        if name == "get_my_context" and isinstance(result, dict):
            result = {**result, "capabilities": {**result.get("capabilities", {}),
                "harness_managed_dependencies": callable(getattr(self.school, "personal_chat_tool_requirements", None))}}
        return result

    def _prepare(self, task):
        return prepare_recommendation_sources(task, receipts=self.receipts,
            execute=lambda child, arguments: self.execute(child, arguments, initiated_by="service_acquisition"))

    def execute(self, name, args, *, initiated_by=None):
        self.authorize()
        initiated_by = initiated_by or ("service_preflight" if name == "get_my_context" else "model")
        self.emit("tool.start", tool_event_fields(name, args, initiated_by=initiated_by))
        started = time.monotonic()
        try:
            if name == PREPARE_RECOMMENDATION_TOOL:
                task = validate_recommendation_task(args)
                if task["request_basis"] == "pending_task" and self.clarification_context is None:
                    raise AccessError("当前没有可继续的待补请求", 409, "clarification_context_missing")
                self.policy_context["pending_messages"] = ([self.clarification_context["request"],
                    *self.clarification_context["supplements"]] if task["request_basis"] == "pending_task" else [])
                self.policy_context["hard_preferences"] = deepcopy(task["hard_preferences"])
                self.policy_context["soft_preferences"] = deepcopy(task.get("soft_preferences", []))
                if not callable(self.live_tool):
                    raise AccessError("当前来源不支持推荐资料准备", 409, "recommendation_preparation_unavailable")
                prepared = self._prepare(task)
                result = self._invoke("check_recommendation_inputs", {key: task[key] for key in ("recommendation_kind", "target_term")})
                result = {**result, "prepared_sources": prepared}
            else:
                requirements = getattr(self.school, "personal_chat_tool_requirements", None)
                task = None
                if name in COMPUTE_TOOLS and callable(self.live_tool) and callable(requirements):
                    # Dependencies come from the authenticated adapter and the
                    # owned plan, not a model-supplied resource/owner manifest.
                    task = requirements(self.context, name, args,
                        policy_context=deepcopy(self.policy_context), receipts=self.receipts, before=self.authorize)
                prepared = self._prepare(validate_recommendation_task(task)) if task is not None else []
                result = self._invoke(name, args)
                if prepared:
                    result = {**result, "dependency_task": task, "prepared_sources": prepared}
            plan = result.get("result") if result.get("data_kind") == "course_data_test" else None
            if (name in COMPUTE_TOOLS and type(plan) is dict and type(plan.get("plan_ref")) is str
                    and re.fullmatch(r"PLN-[a-f0-9]{48}", plan["plan_ref"])):
                self.policy_context["active_plan_ref"] = plan["plan_ref"]
        except Exception as error:
            self.emit("tool.end", {"tool_name": name, "status": "failed",
                "error_code": getattr(error, "code", getattr(error, "reason", "tool_unavailable")),
                "elapsed_ms": round((time.monotonic()-started)*1000)})
            raise
        self.authorize()
        self.emit("tool.end", {**tool_event_fields(name, args, result, initiated_by=initiated_by),
            "status": "not_ready" if result.get("status") in {"not_ready", "unavailable"} else "completed",
            "elapsed_ms": round((time.monotonic()-started)*1000)})
        return result
