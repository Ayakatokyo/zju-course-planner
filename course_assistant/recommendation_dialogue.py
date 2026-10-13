"""Fixed synthetic recommendation tools and server-owned result references.

User action evidence is checked against this turn's original message. Retrieved
text and model assertions cannot authorize relaxation or personal unlocking.
"""
from copy import deepcopy
from decimal import Decimal
import re
import uuid
import base64

from .auth import AccessError
from .importer import digest
from .planner import revise_constraints, validate_plan

TOOL_NAMES = {"recommend_my_courses", "revise_my_recommendation", "compare_my_options", "act_on_my_option"}


def fail(code, message):
    from .chat import ChatFailure
    raise ChatFailure(code, message)


def default_intent(demo):
    metadata = demo.get("recommendation")
    if not isinstance(metadata, dict):
        fail("recommendation_not_configured", "请使用独立 synthetic 推荐演示；原演示保留原有查询与课表能力")
    fields = ("target_module", "target_credits", "interest", "optional_course_ids", "required_course_ids", "excluded_course_ids")
    result = {key: deepcopy(metadata[key]) for key in fields if key in metadata}
    result.setdefault("interest", "ai")
    result.setdefault("required_course_ids", [])
    result.setdefault("excluded_course_ids", [])
    return result


def resolve_option(state, option_id, versions, *, run_id=None, current=True):
    runs = state.get("recommendation_runs", [])
    if option_id in {"A", "B", "C", "U1", "U2", "U3"}:
        runs = [run for run in runs if run.get("run_id") == state.get("current_recommendation_run")]
        options = runs[-1].get("options", []) if runs else []
        option_id = next((item["option_id"] for item in options if item.get("label") == option_id), None)
    for run in reversed(runs):
        if run_id is not None and run.get("run_id") != run_id:
            continue
        for option in run.get("options", []):
            if option.get("option_id") != option_id:
                continue
            if run.get("workspace_versions") != versions or option.get("is_stale"):
                fail("stale_option", "该结果的工作区版本已失效，请按当前输入重新推荐")
            if current and run.get("intent_revision") != state.get("intent_revision"):
                fail("stale_option", "推荐条件已改变，旧方案只能查看或比较；保存前需重新推荐")
            return run, option
    fail("inaccessible_option", "方案引用不属于当前本人对话，或该轮没有这套方案")


def _normalized(text):
    return re.sub(r"\s+", "", text).lower()


def _names(demo, identifier):
    names = [identifier]
    for course in demo["courses"]:
        if course["course_id"] == identifier:
            names.append(course["course_name"])
            if course["course_name"].endswith("（合成）"):
                names.append(course["course_name"][:-4])
            if course["course_name"].startswith("合成"):
                names.append(course["course_name"][2:])
    return [_normalized(value) for value in names]


def _evidence(operation, message):
    evidence = operation.get("evidence")
    if not isinstance(evidence, str) or not evidence or evidence not in message:
        fail("explicit_intent_required", "本轮修改必须对应用户原话；模型或资料建议不能授权修改")
    clauses = re.split(r"[，,。；;\n]", message)
    implicated = [clause for clause in clauses if evidence in clause or clause in evidence]
    if not implicated or any(re.search(r"如果|假如|是否|能否|能不能|如何|怎么|为什么|建议|假设|[吗？?]|(?:不要|不用|不必|别|不能|不)(?:让|解除|取消|放宽|改|把)", clause)
                             for clause in implicated):
        fail("explicit_intent_required", "询问、假设或否定表达不构成本轮修改许可，请明确需要执行的操作")
    return '\n'.join(_normalized(clause) for clause in implicated)


def _command(text, pattern):
    # Match the command's direction from the original clause start, not a
    # model-selected fragment hidden inside a negative or hypothetical phrase.
    return re.search(r"(?m)^(?:(?:请帮我|请|帮我|现在|继续|我希望|我想|我要|本轮))*" +
                     r"(?:" + pattern + r")(?:$|[。！!]|其余|其他|并|请)", text)


def _course_pattern(demo, identifier):
    return '(?:' + '|'.join(re.escape(name) for name in _names(demo, identifier)) + ')'


def _contains(text, demo, identifier):
    return any(name in text for name in _names(demo, identifier))


def apply_operations(demo, constraints, intent, operations, message):
    from .chat import _credit, _identifiers, _object
    if not isinstance(operations, list) or not 1 <= len(operations) <= 8:
        fail("invalid_tool_arguments", "请提供一至八项明确修改")
    constraints, intent = deepcopy(constraints), deepcopy(intent)
    courses = {row["course_id"] for row in demo["courses"]}
    sections = {row["section_id"] for row in demo["offering_snapshot"]["records"]}
    confirmed = []
    for operation in operations:
        _object(operation, {"op", "field", "value", "replacement", "evidence"}, {"op", "field", "value", "evidence"})
        op, field, value = operation["op"], operation["field"], operation["value"]
        text = _evidence(operation, message)
        patch = {}
        if field == "friday_free" and op == "set":
            patterns = {"hard": r"周五(?:必须|一定|务必)(?:空闲|没课|无课)|周五不能(?:有课|上课)",
                        "soft": r"(?:尽量|最好)(?:让)?周五(?:空闲|没课|无课)|周五(?:尽量|最好)(?:空闲|没课|无课)",
                        "off": r"周五(?:可以|可|允许)(?:有课|上课)|取消周五(?:空闲|没课|无课)(?:要求|条件|限制)?"}
            if value not in patterns or not _command(text, patterns[value]):
                fail("explicit_intent_required", "周五条件修改需本轮明确说明软偏好、硬条件或允许有课")
            patch[field] = value
        elif field in {"min_credits", "max_credits"} and op == "set":
            number = _credit(value)
            word = "最低|至少|下限" if field == "min_credits" else "最高|最多|上限"
            if number is None:
                pattern = rf"取消(?:学分)?(?:{word})(?:学分)?(?:要求|条件|限制)?"
            else:
                pattern = rf"(?:{word})(?:学分)?(?:改为|调整为|设为|为|是|到)?{re.escape(str(value))}(?:个)?学分"
            if not _command(text, pattern):
                fail("explicit_intent_required", "学分条件修改需本轮明确说明对应上限或下限")
            patch[field] = number
        elif field in {"locked_section_ids", "excluded_section_ids"} and op in {"add", "remove", "replace"}:
            _identifiers([value], sections)
            if field == "locked_section_ids":
                pattern = r"(?:解除|取消|移除)(?:我(?:的)?)?锁定(?:的)?" if op != "add" else r"(?:锁定|保留)"
            else:
                pattern = r"(?:取消|解除|移除)(?:对)?(?:排除|禁用)" if op == "remove" else r"(?:排除|禁用|不要|不选)"
            if not _command(text, pattern + re.escape(_normalized(value))) and op != 'replace':
                fail("explicit_intent_required", "班次修改必须明确指向本轮的班次和操作")
            items = list(constraints.get(field, []))
            if op == "add":
                items = list(dict.fromkeys(items + [value]))
            else:
                items = [item for item in items if item != value]
                if field == "locked_section_ids":
                    from .planner import _protected_sections
                    protected = set(_protected_sections(demo["offering_snapshot"], demo["student"],
                                                         {**constraints, "locked_section_ids": []}))
                    if value in protected:
                        fail("protected_constraint", "预置或正式来源成员不能由个人解锁操作移除")
                    patch["unlock_section_ids"] = [value]
            if op == "replace":
                replacement = operation.get("replacement")
                _identifiers([replacement], sections)
                if not _command(text, r"(?:把|将)?(?:我锁定的)?" + re.escape(_normalized(value)) + r"(?:换成|换为|替换为)" + re.escape(_normalized(replacement))):
                    fail("explicit_intent_required", "换班需本轮明确指定原班与新班")
                items.append(replacement)
            patch[field] = items
        elif field in {"optional_course_ids", "required_course_ids", "excluded_course_ids"} and op in {"add", "remove", "replace"}:
            _identifiers([value], courses)
            source_name = _course_pattern(demo, value)
            if op == "replace":
                replacement = operation.get("replacement")
                _identifiers([replacement], courses)
                destination_name = _course_pattern(demo, replacement)
                prefix = r"(?:把|将)?(?:(?:方案)?[abc]的)?"
                pattern = prefix + source_name + r"(?:课程)?(?:换成|换为|替换为)" + destination_name
                if field == 'excluded_course_ids':
                    pattern = prefix + r"排除(?:的)?" + source_name + r"(?:换成|换为|替换为)排除" + destination_name
                if not _command(text, pattern):
                    fail("explicit_intent_required", "换课需明确指定原课程与新课程")
                if field == 'excluded_course_ids':
                    intent[field] = list(dict.fromkeys([item for item in intent.get(field, []) if item != value] + [replacement]))
                    confirmed.append({**deepcopy(operation), "user_evidence_verified": True})
                    continue
                for key in ("required_course_ids", "optional_course_ids"):
                    intent[key] = [item for item in intent.get(key, []) if item != value]
                intent["excluded_course_ids"] = list(dict.fromkeys(intent.get("excluded_course_ids", []) + [value]))
                intent["excluded_course_ids"] = [item for item in intent["excluded_course_ids"] if item != replacement]
                intent["required_course_ids"] = list(dict.fromkeys(intent.get("required_course_ids", []) + [replacement]))
                intent["optional_course_ids"] = list(dict.fromkeys(intent.get("optional_course_ids", []) + [replacement]))
            else:
                if field == 'excluded_course_ids':
                    pattern = (r"(?:取消|解除)(?:排除|禁用)(?:的)?" + source_name + r"|(?:取消|解除)(?:对)?" + source_name + r"(?:的)?排除|不再排除" + source_name if op == 'remove'
                               else r"(?:排除|禁用|不选|不要(?:选|安排)?)" + source_name)
                elif field == 'required_course_ids':
                    pattern = (r"(?:取消|移除)(?:必选|必须安排|必须保留)" + source_name if op == 'remove'
                               else r"(?:必选|必须选|必须安排|必须保留)" + source_name)
                else:
                    pattern = (r"(?:移除|删除|不要(?:选|安排)?|不选)" + source_name if op == 'remove'
                               else r"(?:加入|加上|考虑|保留)" + source_name)
                if not _command(text, pattern):
                    fail("explicit_intent_required", "课程增删操作需要本轮明确表达")
                intent[field] = ([item for item in intent.get(field, []) if item != value] if op == "remove"
                                 else list(dict.fromkeys(intent.get(field, []) + [value])))
        else:
            fail("invalid_tool_arguments", "该修改操作未开放；固定来源条件保持")
        if patch:
            patch["expected_revision"] = constraints.get("revision", 0)
            constraints = revise_constraints(constraints, patch)
        confirmed.append({**deepcopy(operation), "user_evidence_verified": True})
    return constraints, intent, confirmed


def execute(service, name, args, workspace, constraints, context, state, message, turn_id):
    from .chat import _object, _credit
    from .recommendation import recommend, revalidate_option
    demo, versions = workspace["demo"], service.app.versions(workspace)
    if name == 'act_on_my_option':
        _object(args, {'action', 'option_id', 'format'}, {'action', 'option_id'})
        run, option = resolve_option(state, args['option_id'], versions)
        text = _evidence({'evidence': message}, message)
        label = re.escape(option['label'].lower())
        target = r'(?:(?:方案|刚才的|刚才|这套|这版)*)' + label
        if args['action'] == 'save':
            permitted = _command(text, r'(?:保存|存下)' + target)
        elif args['action'] == 'export' and args.get('format', 'json') in {'json', 'csv', 'ics'}:
            format_name = re.escape(args.get('format', 'json'))
            permitted = _command(text, r'(?:导出|下载)' + target + r'(?:为|的)?' + format_name + r'|(?:把|将)' + target + r'(?:导出|下载)(?:为)?' + format_name)
        else:
            permitted = False
        if not permitted:
            fail('explicit_intent_required', '保存或导出必须明确指定本轮方案及操作；询问或假设不会执行')
        result = {'status': 'action_ready', 'action': args['action'], 'format': args.get('format', 'json'),
                  'run_id': run['run_id'], 'option_id': option['option_id'], 'label': option['label'],
                  'workspace_versions': versions, 'data_kind': 'synthetic', 'user_evidence_verified': True}
        state['pending_option_action'] = deepcopy(result)
        return result, constraints
    if name == "compare_my_options":
        _object(args, {"option_ids"}, {"option_ids"})
        ids = args["option_ids"]
        if not isinstance(ids, list) or not 2 <= len(ids) <= 3 or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
            fail("invalid_tool_arguments", "请选择两至三套明确方案比较")
        rows = [resolve_option(state, identifier, versions, current=False) for identifier in ids]
        refreshed = []
        for run, option in rows:
            plan = {**option, **service.app.enrich_plan(option, option["constraints"], {**workspace, "constraints": option["constraints"]}),
                    **revalidate_option(demo, option['constraints'], run['intent'], option['section_ids'])}
            refreshed.append(plan)
        base = set(refreshed[0]["course_ids"])
        differences = [{"option_id": item["option_id"], "added_course_ids": sorted(set(item["course_ids"]) - base),
                        "removed_course_ids": sorted(base - set(item["course_ids"])), "metrics": item["metrics"],
                        "added_section_ids": sorted(set(item["section_ids"]) - set(refreshed[0]["section_ids"])),
                        "removed_section_ids": sorted(set(refreshed[0]["section_ids"]) - set(item["section_ids"]))}
                       for item in refreshed]
        return {"status": "compared", "plans": refreshed, "differences": differences,
                "different_input_intents": len({digest(run["intent"]) for run, _ in rows}) > 1,
                "workspace_versions": versions, "data_kind": "synthetic", "preview_only": True}, constraints
    _object(args, {"constraint_patch", "intent_patch", "operations", "option_id"})
    intent = deepcopy(state.get("recommendation_intent") or default_intent(demo))
    if not state.get('recommendation_intent'):
        # A newly opened conversation may inherit a saved recommendation's
        # global intent. Its selected subset is not a new global mandatory list.
        for saved in service.app.store.saved(context):
            if saved.get('versions') == versions and saved.get('recommendation_intent'):
                service.app.own_saved(context, saved['saved_id'])
                intent = deepcopy(saved['recommendation_intent'])
                constraints = {**constraints, 'requested_course_ids': deepcopy(
                    saved.get('recommendation_global_constraints', demo['constraints']).get('requested_course_ids', []))}
                break
    if "recommendation_global_requested" in state:
        constraints = {**constraints, "requested_course_ids": deepcopy(state["recommendation_global_requested"])}
    if "option_id" in args:
        _, base = resolve_option(state, args["option_id"], versions)
        intent["optional_course_ids"] = [identifier for identifier in base["course_ids"] if identifier in intent["optional_course_ids"]]
    constraints = service._patch(constraints, args.get("constraint_patch", {}), demo)
    patch = args.get("intent_patch", {})
    _object(patch, {"target_credits", "interest"})
    if patch:
        if "target_credits" in patch:
            number = _credit(patch["target_credits"])
            same_target = number is not None and Decimal(number) == Decimal(str(intent['target_credits']))
            text = _evidence({'evidence': message}, message) if not same_target else ''
            pattern = r"(?:把)?(?:目标|专业选修目标)(?:学分)?(?:改为|设为|调整为|为)?" + re.escape(str(patch['target_credits'])) + r"(?:个)?学分"
            if number is None or not same_target and not _command(text, pattern):
                fail("explicit_intent_required", "目标学分变更需本轮明确数值；默认合成目标会展示")
            intent["target_credits"] = number
        if "interest" in patch:
            if patch["interest"] not in {"ai", "off"}:
                fail("invalid_tool_arguments", "首片只支持有合成说明的 AI 兴趣或关闭兴趣偏好")
            if patch['interest'] != intent.get('interest'):
                text = _evidence({'evidence': message}, message)
                pattern = r"取消(?:ai(?:兴趣)?|兴趣偏好)|不考虑ai(?:兴趣)?" if patch['interest'] == 'off' else r"恢复ai(?:兴趣)?|兴趣改为ai|偏向ai"
                if not _command(text, pattern):
                    fail("explicit_intent_required", "兴趣偏好修改需本轮明确表达")
            intent["interest"] = patch["interest"]
    actions = []
    if "operations" in args:
        constraints, intent, actions = apply_operations(demo, constraints, intent, args["operations"], message)
    elif name == "revise_my_recommendation":
        fail("invalid_tool_arguments", "连续替换需明确操作列表")
    revision = state.get("intent_revision", 0) + 1
    intent["revision"] = revision
    result = recommend(demo, constraints, intent, max_nodes=10000, limit=3)
    run_id = str(uuid.uuid4())
    for key in ("plans", "pending_plans"):
        enriched = []
        for position, plan in enumerate(result.get(key, [])):
            own_constraints = plan["constraints"]
            item = {**plan, **service.app.enrich_plan(plan, own_constraints, {**workspace, "constraints": own_constraints}),
                    **revalidate_option(demo, own_constraints, intent, plan['section_ids']),
                    "run_id": run_id, "option_id": str(uuid.uuid4()), "constraints": deepcopy(own_constraints)}
            item["label"] = f"U{position + 1}" if key == "pending_plans" else chr(65 + position)
            enriched.append(item)
        result[key] = enriched
    result.update(run_id=run_id, intent_revision=revision, intent=deepcopy(intent), actions=actions,
                  constraints=deepcopy(constraints), workspace_versions=versions, versions=versions,
                  data_kind="synthetic", preview_only=True, preview_valid=True)
    run = {"run_id": run_id, "intent_revision": revision, "intent": deepcopy(intent),
           "constraints": deepcopy(constraints), "workspace_versions": versions, "turn_id": turn_id,
           "options": deepcopy(result.get("plans", []) + result.get("pending_plans", []))}
    state.update(recommendation_intent=deepcopy(intent), intent_revision=revision, current_recommendation_run=run_id,
                 recommendation_runs=(state.get("recommendation_runs", []) + [run])[-6:],
                 recommendation_global_requested=deepcopy(constraints.get("requested_course_ids", [])))
    return result, constraints


def option_action(app, body, context, *, export=False, final_chat_request=None):
    from .chat import _object
    from .exporter import export_result
    _object(body, {"conversation_id", "expected_turn", "option_id", "run_id", "action_id", "format"},
            {"conversation_id", "expected_turn", "option_id"} | (set() if export else {"action_id"}))
    app.chat._uuid(body["conversation_id"])
    if not export:
        app.chat._uuid(body["action_id"])
        previous = app.store.recommendation_action(context, body["action_id"])
        if previous:
            request = previous["request"]
            if (request["conversation_id"] != body["conversation_id"] or request["expected_turn"] != body["expected_turn"]
                    or request["option_ref"]["option_id"] != body["option_id"] or
                    body.get("run_id", request["option_ref"]["run_id"]) != request["option_ref"]["run_id"]):
                raise AccessError("动作编号不能复用于不同保存请求", 409, "action_id_reused")
            if final_chat_request is not None and 'chat_response' in previous['response']:
                return deepcopy(previous['response']['chat_response'])
            return {**previous["response"]["saved_plan"], "action_id": body["action_id"], "replayed": True,
                    "conversation_id": body["conversation_id"], "turn_id": body["expected_turn"]}
    workspace, versions = app.chat._check_workspace(context)
    row = app.store.chat_one(context, body["conversation_id"])
    if row is None:
        raise AccessError("资源不可访问", 404, "inaccessible_object")
    if type(body["expected_turn"]) is not int or row["turn_id"] != body["expected_turn"] or row["in_progress"]:
        raise AccessError("对话已更新或正在执行，请等待并重新选择结果", 409, "turn_version_changed")
    state = deepcopy(row["state"])
    run, option = resolve_option(state, body["option_id"], versions, run_id=body.get("run_id"))
    if export and option.get("saved_id"):
        return export_result(app.own_saved(context, option["saved_id"]), body.get("format", "json"))
    # The selected option owns its subset. All protected workspace conditions
    # are inherited; only verified personal unlocks may be carried over.
    patch = deepcopy(option["constraints"])
    verified_unlocks = set(workspace["constraints"].get("locked_section_ids", [])) - set(patch.get("locked_section_ids", []))
    preview_locks = set(state.get("preview_constraints", {}).get("locked_section_ids", []))
    if verified_unlocks - (set(workspace["constraints"].get("locked_section_ids", [])) - preview_locks):
        fail("protected_constraint", "保存不能移除未经本轮明确解除的个人锁")
    patch["unlock_section_ids"] = sorted(verified_unlocks)
    patch["expected_revision"] = workspace["constraints"].get("revision", 0)
    constraints = revise_constraints(workspace["constraints"], patch)
    updated = {**workspace, "constraints": constraints}
    from .recommendation import revalidate_option
    plan = {**option, **app.enrich_plan(option, constraints, updated),
            **revalidate_option(workspace['demo'], constraints, run['intent'], option['section_ids']), "constraints": deepcopy(constraints)}
    if plan["validation"]["result"] == "fail":
        fail("invalid_option", "完整复核发现已知失败，不能保存为有效推荐方案")
    frozen = {"owner_student_id": context.student_id, "security_realm": context.security_realm,
              "data_scope": "synthetic", "data_kind": "synthetic", "status": "planning_draft",
              "not_official_enrollment": True, "plan": plan, "constraints": constraints,
              "versions": app.versions(updated), "frozen_dataset": deepcopy(workspace["demo"]),
              "recommendation_intent": deepcopy(run["intent"]), 'recommendation_global_constraints': deepcopy(run['constraints']),
              "run_id": run["run_id"], "option_id": option["option_id"]}
    app.auth.require_context(context)
    if export:
        return export_result(frozen, body.get("format", "json"))
    state.update(recommendation_intent=deepcopy(run["intent"]))
    state.pop('pending_option_action', None)
    receipt = app.store.save_chat_option(context, body["conversation_id"], body["expected_turn"], versions,
                                         {"run_id": run["run_id"], "option_id": option["option_id"]},
                                         body["action_id"], frozen, constraints, state, final_chat_request=final_chat_request)
    if final_chat_request is not None:
        return receipt['chat_response']
    return {**receipt["saved_plan"], "action_id": body["action_id"], "replayed": receipt["replayed"],
            "conversation_id": body["conversation_id"], "turn_id": body["expected_turn"], "preview_valid": True}


def finish_explicit_action(app, response, request_id, context):
    """Resume the same command after action_ready; saved receipts prevent duplicates."""
    action = response['pending_action']
    body = {'conversation_id': response['conversation_id'], 'expected_turn': response['turn_id'],
            'run_id': action['run_id'], 'option_id': action['option_id'], 'action_id': request_id, 'format': action['format']}
    try:
        if action['action'] == 'save':
            return option_action(app, body, context, final_chat_request={'request_id': request_id, 'response': response})
        data, content_type, suffix = option_action(app, body, context, export=True)
        final = {**response, 'action_pending': False, 'reply_origin': 'verified_explicit_action',
                 'reply': f"已按本轮明确指令导出方案 {action['label']} 的 {suffix.upper()}；仍是合成规划草案。",
                 'download': {'data_base64': base64.b64encode(data).decode(), 'content_type': content_type,
                              'filename': f'synthetic-recommendation.{suffix}', 'bytes': len(data)}}
        app.store.amend_chat_response(context, response['conversation_id'], request_id, response['turn_id'], final)
        return final
    except (ValueError, KeyError) as error:
        final = {**response, 'status': 'failed', 'action_pending': False, 'reply_origin': 'service_error',
                 'code': getattr(error, 'code', getattr(error, 'reason', 'explicit_action_failed')),
                 'reply': str(error)}
        app.store.amend_chat_response(context, response['conversation_id'], request_id, response['turn_id'], final)
        return final
