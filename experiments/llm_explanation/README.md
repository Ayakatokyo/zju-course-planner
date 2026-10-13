# 选课助手：第一轮 LLM 解释实验

日期：2026-10-10（Asia/Shanghai）。接续已有学习，不重开概念课。

当前已准备输入和核对合成计算，**模型尚未配置，实际模型请求与回复均未产生**。准备脚本由助手编写、运行和核对，不算用户独立实现或亲手运行。实验独立于应用 HTTP 和 SQLite，仅复用当前计算；未来接入本人工作区仍必须走有效 AuthContext、owner/realm、版本及来源许可链。

## 1. 在已有代码里找五个角色

| 角色 | 解决什么问题 | 目前的具体位置和状态 |
| --- | --- | --- |
| LLM（大语言模型） | 理解语言、组织解释或提出动作 | 未实现。`web/app.js` 的 Agent 草稿不能发送；无模型适配器或实际请求 |
| Workflow（固定流程） | 让已确定的步骤按代码顺序执行 | `server.py:196` 起的分支；如加载本人工作区→继承并修订条件→调用计算→存储→返回。生成器的回溯搜索也是确定性算法 |
| Tool（工具） | 实际查询或计算并返回可核验结果 | `planner.py:449` 的 `validate_plan`、715 的 `generate_plans`，以及 `audit.py:485` 的 `audit_progress` 等；尚无模型工具注册/路由 |
| Harness（执行支撑与控制） | 检查谁能执行、输入是否有效、状态与版本是否一致 | 已有 `auth.py:94` 有效上下文、`storage.py:69` 本人查询、`server.py:64` 归属/摘要、`planner.py:77` 条件版本与锁定继承；模型动作预算、超时、恢复和 trace 仍缺 |
| Agent（动态决定下一步的模型驱动流程） | 根据问题和工具反馈决定澄清、调用或结束 | 保留了对话主入口与设计。尚无“模型提议→程序检查→执行工具→回传→再推理”的循环或对话持久化 |

已有 `reference_retrieval.py:105` 为离线关键词参考查询，133 起为引用回查。它不接运行用户或模型；`plan_id` 仅是开发者参考选择，不能当本人授权。原候选仍待审核，restricted reference 不进入本实验。

```mermaid
flowchart LR
    F[固定合成夹具] --> T[已有 Tool：validate_plan]
    T --> P[脚本：投影少量实际结果]
    P -. 配置后的一次调用 .-> M[LLM：解释]
    M -. 实际回复待产生 .-> R[对照工具证据核验]
```

这一轮只加入一次模型解释。后续固定 Workflow、薄 Harness 和动态 Agent 在这个入口上逐步学习，不重建现有身份、数据库或前端。

## 2. 这次输入与目标

固定选择 `DEMO-PE-A`、`DEMO-ML-B`、`DEMO-SIM-A`；体育锁定和预置项保留。`DEMO-ML-B` 的合成时段为冬、周五、第1–2节、第1–8周，校历为 `DEMO-CALENDAR-v1`。

计算由原 `validate_plan` 完成，LLM 只解释计算结果。脚本沿用 `revise_constraints` 和原条件版本，只在内存修改周五条件。模型输入仅投影合成班标识、周五时段、相关条件、总体工具状态、未通过检查、偏好分项与必要版本；原课程定义/出处、学生档案、学业明细、整个 bundle、凭据和会话均不发送。`synthetic` 检查只是固定开发夹具的准入检查，不是任意上传材料的真实性或脱敏证明。

本实验为什么先用程序算结果：模型解释是否正确可以直接对照工具证据；模型文字不能修改偏好、取代计算或证明官方可选。

## 3. 先预测，再亲手运行

在项目目录执行。这里显式选用已核对的 Python 3.13.13，当前终端的 `python3` 指向3.9.6，不满足项目要求。

```sh
cd /Users/shuzida/Desktop/大数据信息分析系统
/opt/homebrew/bin/python3.13 experiments/llm_explanation/prepare_input.py --friday hard
```

第一次读输出时只看三处：`friday_free`、`focus_section.friday_meetings`、`tool_result`。`hard` 是必须满足的条件，`soft` 是偏好。先预测：只把 hard 改成 soft，上课事实会不会改变？校验状态和偏好分项可能怎样变化？为什么仍需程序重新校验？

预测后再运行：

```sh
/opt/homebrew/bin/python3.13 experiments/llm_explanation/prepare_input.py --friday soft
```

关键计算调用很短：读取旧条件→`revise_constraints`→选班→`validate_plan`。其余代码是固定文件准入与组装小投影；需要时只围绕计算调用补字典、列表和函数知识。两次都从同一份原夹具重新起算，因此 revision 都为2，不是在硬条件结果上连续追加修订。命令行换条件是跟做；独立改代码与新变体另行记录。

## 4. 配置后如何接一次实际 LLM

先确定服务、实际可用模型和调用范围：只允许这份合成投影，一次请求，失败时停止并保留事实。模型名由服务端环境提供，不由学生消息选择。实际调用前先冻结输入；记录输入、模型配置、实际回复、错误、延迟及服务提供的用量，凭据不进入记录。

### 远程模型 API

需要可用账户/访问条件、API Key、模型名称与调用预算。Key 放本地环境或密钥管理中，程序从服务端读取。以 OpenAI 为文档示例：官方 Python SDK 调用 `client.responses.create`；此处尚未选定供应商、安装 SDK 或验证账户可用性。[OpenAI 官方 Quickstart](https://developers.openai.com/api/docs/quickstart)、[认证与密钥说明](https://developers.openai.com/api/reference/overview)。

下面是**尚未运行的接入示例**；只有完成配置并确认一次调用后才执行。应用继续保留标准库依赖，SDK 若选用则放实验专用虚拟环境。

```python
import json, os
from openai import OpenAI
from experiments.llm_explanation.prepare_input import prepare

prepared = prepare("hard")
client = OpenAI(timeout=30.0, max_retries=0)
response = client.responses.create(
    model=os.environ["LLM_MODEL"], instructions=prepared["instructions"],
    input=json.dumps(prepared["input"], ensure_ascii=False),
)
print(response.output_text)
```

这段解释三个接口元素：`instructions` 是解释要求，`input` 是实际送入模型的合成证据，`output_text` 是真实服务返回的文本。超时/鉴权/配额错误与“请求成功但回答不正确”分开观察；30秒超时和不自动重试是本实验拟定配置，不是校方或供应商标准。SDK 的 `timeout`、`max_retries=0` 配置见[官方 Python 库说明](https://developers.openai.com/api/reference/python)。本段仅为接口讲解，最终运行器还需保存实际输入输出与错误回执。

### 本地模型服务

需要安装推理服务、下载所选本地模型，并核对本机资源。以 Ollama 为例，本地服务可用 `http://localhost:11434/api/generate`；把模型名、`system=prepared["instructions"]`、`prompt=json.dumps(prepared["input"])`、`stream=false` 组装成请求并读取实际返回。模型名称和资源开销待选，不默认下载官方示例里的模型。[Ollama 官方 Quickstart](https://docs.ollama.com/quickstart)、[Generate API](https://docs.ollama.com/api/generate)。

先选远程或本地的一条路线，再共同写/运行最小调用段。本轮没有选定或安装模型；没有用助手在聊天中的回答冒充项目 API 回复。

## 5. 正常、异常与验收

正常链：固定合成夹具→确定性校验→最小投影→一次真实模型请求→保存实际回复→人工逐项对照。准备脚本完成到投影为止。

| 场景 | 处理与判断 |
| --- | --- |
| 服务未配置 | 当前所处状态；可以观察准备输入，不声称已经调用模型 |
| 配置完整、实际返回解释 | 看是否准确保留工具状态、周五条件、班标识/时段、合成范围及待检查事项 |
| 超时、鉴权/配额错误或协议错误 | 标记本次调用失败；没有有效回复；不写方案、不自动切供应商或无限重试 |
| 请求成功但说 fail 的班已通过，或补造可行替代班 | 模型回答未通过；保留实际回复，对照工具证据定位，再做局部修正 |
| 提议换班或把 hard 改 soft | 提议不等于已执行；本人明确意图后才修订，保留锁定/固定/版本并重新计算 |

首轮成功要求：发生一次真实调用且有实际输入输出；解释与工具结果一致；不把合成结果称学校结论或把建议称执行成功。随后亲手改一个条件并预测、再运行和解释。用户跟做、独立修改、新变体与工程验收分别记录；本轮准备通过不完成以上验收。
