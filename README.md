# 浙江大学选课助手

通过与 Agent 对话表达需求、澄清条件并获得推荐课表，支持比较、锁定、调整、保存与导出。确定性程序负责计算。当前已接入 DeepSeek 对话、受限工具查询和推荐预览，并取得真实 API 与本机页面的多轮 synthetic 验证。模型未配置时发送禁用，需求草稿只保留在页面。

**当前版本 0.2.0：可运行的本机 synthetic 演示。** 已实现服务端会话、固定合成主体绑定、本人工作区与方案授权，以及 P0/P1 计算和原生 Web 联调。学校真实认证、本人档案/成绩/选课数据源仍未接入；不能用于真实学生审核或自动选课。

## 启动

仅需 Python 3.10+，应用运行不依赖第三方包。本轮在 Python 3.13.13 上验证。

```sh
cd /Users/shuzida/Desktop/大数据信息分析系统
python3 -m course_assistant.server --enable-mock --port 8765
```

打开 [本机选课助手](http://127.0.0.1:8765/)，选择“模拟学生 A”或“模拟学生 B”。默认进入“对话”，在“选课需求”输入测试问题；配置好模型并明确允许发送合成测试需求后才可发送。左栏列出本人的历史对话，刷新后可打开并继续原会话；“新对话”保留历史，重新打开须再次选择远程发送许可。主区保留推荐课表和保存/导出，“已保存”可查看冻结课表并导出。窄屏历史默认折叠。历史加载不重复执行保存或下载，见[历史栏页面验证](validation/chat_history_browser_20261014.json)。手动条件入口、示例按钮及工具 JSON 已从界面移除。这两位身份和学生绑定由服务端预置，只验证 synthetic 计算和资源隔离，不验证现实使用者身份。页面不会要求学校密码、姓名、学号或真实 Excel。

按 `Ctrl+C` 停止服务。服务只绑定 `127.0.0.1`。默认数据库为 `runtime/planning.sqlite3`，自动创建并保存本人活动数据、偏好、会话和冻结方案；该目录被 Git 忽略。可用 `--db /本机路径/演示.sqlite3` 选择独立开发数据库。旧无归属表保留隔离，不会自动分配给任何登录用户。

不加 `--enable-mock` 时，mock 默认关闭，仅显示认证尚未配置的入口，个人 API 拒绝访问。学校认证未配置不会自动降级为 mock 登录。HTTP 回环 Cookie 使用 HttpOnly、SameSite=Strict；这份配置和固定 mock 主体不构成生产认证方案。

## 推荐能力线 R0—R2 合成演示

使用独立固定夹具运行课程选择、多套具体课表、连续调整及保存导出；原演示保留：

```sh
python3 -m course_assistant.server --enable-mock --recommendation-demo --port 18865 --db runtime/recommendation_demo.sqlite3
```

打开 [推荐演示](http://127.0.0.1:18865/)，选择合成身份。配置 DeepSeek 后，同意发送纯合成测试问题，输入“优先补专业选修6学分，偏向AI，尽量周五空闲，保留体育”。固定场景默认生成三套课程组合；“周五必须空闲”后只返回一套。可继续比较 A/B、明确换课或换班、解除个人锁及取消排除；有歧义的个人放宽要求明确说明，预置/正式来源成员保持保护。可说“保存B”“导出B为JSON”，也可点击方案卡。字母只指当前推荐集，历史引用与旧版本由服务端核验。

推荐从候选池选择课程，不将候选全部当必选；每套使用自己的选中课程子集完整复核。模块贡献、兴趣说明、周五取舍、搜索截断和 unknown 分开呈现。保存使用服务端结果引用，一次事务更新条件、冻结方案、对话基线与幂等回执。保存后的追问和新对话继承全局意图，上一套选中课程不会自动变成下一轮全部必选。

`--recommendation-case unknown` 与 `--recommendation-case locked_friday` 是另外两种已登记纯合成工程场景。它们分别验证缺周次/校历和体育在周五的锁定冲突，不含真实学校事实。[本轮验收记录](validation/推荐能力线R0-R2验收_20261013.md)分开记录工程用例、实际 DeepSeek、页面和下载。真实资料/RAG、学校本人接入与 Embedding 仍按后续阶段推进。

## R3 独立本机参考首片

2026-10-14已实现一个获准培养方案、一个专业模块与秋冬观察班次的本机参考链路：56个精细块连接47个课程代码，精确/词法检索带必要条款、表头和跨页附注，签名引用可跨进程回查；完整观察束用于生成条件性课表。该入口不挂模拟学生A/B，不使用个人工作区，不调用模型或网络。原对话参考工具仍使用既有54块JSON。

使用本机交付入口、独立用途授权及私有索引路径运行；下列路径是占位符，真实配置只保存在Git忽略目录：

```sh
python3 scripts/run_local_reference.py --delivery-index <本机交付入口.json> --grant <本机用途授权.json> --bundle <local-only内索引.json> build
python3 scripts/run_local_reference.py --delivery-index <本机交付入口.json> --grant <本机用途授权.json> --bundle <local-only内索引.json> search "专业模块课程 修满学分"
python3 scripts/run_local_reference.py --delivery-index <本机交付入口.json> --grant <本机用途授权.json> --bundle <local-only内索引.json> --output <local-only内新结果.json> recommend --credits 8 --friday soft
python3 scripts/run_local_reference.py --delivery-index <本机交付入口.json> --grant <本机用途授权.json> --bundle <local-only内索引.json> --output <local-only内新报告.html> report
```

还支持`describe`、`resolve`、分页`lookup`及JSON/CSV；输出文件不覆盖既有文件。真实首片生成3套不同组合，均为`conditional_reference/unknown`，搜索预算已用尽，不能声称全局最优、完全无冲突或本人可选。无可靠校历时ICS正常拒绝。385项当前回归及20项本机工程集成通过；人工金标仍为0，20题审阅工作表已冻结开发12/保留8，人工检索和业务验收尚未完成。见[实施与验收](docs/R3本机参考首片实施与验收_20261014.md)、[当前证据](validation/r3_20261014_validation.json)。

## 配置 DeepSeek 对话

在项目根目录的 `.env` 填入 `DEEPSEEK_API_KEY`，可参照 [.env.example](.env.example)。文件以点开头，Finder 默认隐藏；不要把 Key 发到聊天、前端或 Git。`DEEPSEEK_MODEL` 默认 `deepseek-flash`，可改为账号实际支持的模型名称。保存后重启上面的服务。程序只解析约定字段，不执行 `.env` 内容。

`COURSE_ALLOW_REFERENCE_TEXT=false` 默认只在本机显示培养方案引用；模型收不到原文。仅在明确允许必要非个人片段发给 DeepSeek 时设为 `true`。本机同意/配置不授权外传真实个人材料；学校密码与真实 Excel/截图均不进入模型。当前远程工具只接受固定 synthetic 夹具，不接受任意导入数据。

配置后可执行独立验证：`python3 validation/run_live_chat.py`。它会实际调用 API，在临时合成工作区测试查询、推荐、继承锁定调整与参考检索；会话与回执保存在 Git 忽略的 `local-only/chat_acceptance/`。它与浏览器、学校真实业务验收分开。[本轮系统与对话设计](docs/系统与对话查询实施设计_v1.md)列出完整模块、接口、范围和依赖。

## 已实现的本机范围

- Agent 对话默认入口、DeepSeek 服务端适配、本人多轮会话、受限查询/确定性预览/参考检索工具。模型需配置后才发送；最终结论和引用按真实工具结果核验，再由程序呈现事实摘要。退出、会话失效或主体变化清空页面对话。真实模型已在固定合成问题上验证查询、推荐与连续调整；对话不直接修改工作区或自动选课。
- 每次个人请求验证服务端会话和有效绑定；会话轮换、退出、空闲/绝对到期；写接口验证 CSRF、Origin 和 Host。知道他人学号或方案编号不获得权限。工作区、偏好、保存、比较、重新校验和导出按 owner 与安全域查询；活动及冻结输入另核对内部归属、synthetic 属性和摘要。
- P0 保留修读实例、课程身份和认定归属；实际获分与条件覆盖分开。失败、在修、待认定、被替代和未知不算已获学分。同码多次通过缺适用重复政策时保留 unknown，不统一折叠。学分使用 Decimal；需要原始小数精度时输入十进制字符串。
- P1 检查专业/方案版本、明确资格、先修、余量、学期、周集合、分学期、多时段、考试及固定安排。缺班号、时段、考试或校历保留 unknown。负余量是已知不可用，零与未知分开。最多展示三个组合，比较使用相同当前输入重新计算。
- 调整保留锁定、预置、正式来源成员和固定项，使用偏好版本防止旧条件覆盖。解除个人锁须明确指定；不会解除预置/正式安排。缺失班次不会默认为空闲，无解与待确认状态分别显示。
- 保存版本冻结数据、规则、偏好和校验。查看历史保持只读；“按当前数据重新校验”继承当前偏好与当前/历史锁定，产生当前草案，不改历史。历史比较保留各自输入，不将不同版本视为同条件排名。
- JSON、CSV、ICS 保留 synthetic、来源、冻结版本、未知/冲突及草案状态。ICS 仅导出显式校历映射的已知课时、固定安排和已知考试；无可靠校历正常拒绝。保存或导出不表示正式中选或已获学分。

演示账本基础 8、课堂 4、在修 3、未知记录 1；演示规则独立于原 23 条待审核候选。原资料模式没有本人学业数据，不输出真实毕业或资格结论。开发者 JSON 入口只接受当前固定合成学生的数据；结构校验不能独立证明任意自由文本的真实性。

## 验证和验收

```sh
python3 -m unittest discover -s tests -v
python3 validation/run_checks.py --record-prefix agent_dialog_20261009
```

Node 仅用于 Web 契约测试和 `node --check web/app.js`，不是应用运行依赖。没有 Node 时相关测试会明确跳过，不能据此声称已验证 Web 契约。基线 57 项、恢复开发 129 项及最新对话入口纠偏 133 项分别固化在 `validation/`；重新验证时更换 `--record-prefix` 可独立保存日志和源码摘要。实际浏览器步骤、下载哈希及未覆盖项见 [最新 Agent 入口验收](validation/Agent对话入口验收_20261009.md)、[恢复开发验收记录](validation/验收记录_20261009.md)、[验收范围](validation/验收范围_20261009.md)与[开发进度](docs/开发进度.md)。

原八项和新增十项权限验收仅完成文档所列 synthetic 切片，整体真实验收仍未完成。学校应用许可、具体认证协议/可信身份映射、个人数据源许可、真实重复修读/认定政策、完整教学班/周次/考试/校历均待确认。DeepSeek 模型调用、多轮工具循环与受限参考检索已取得固定合成场景的真实模型证据，详见[真实对话验收](validation/真实对话查询验收_20261013.md)；个人检索缓存、后台任务、资料维护角色及学校数据的运行接入尚未实现。已有本机只读页面解析与离线去重，见下方真实语料范围。Embedding 尚未接入，参考原文外传保持关闭，因此尚未验收模型基于原文的语义回答。服务端接入方式与本人授权边界见 [Agent 对话与模型接入设计](docs/Agent对话与模型接入设计_v1.md)。

## 离线真实语料与RAG设计

2026-10-13已完成现有学校样例的离线来源核验、去重和培养要求盘点。业务池为2,177个课程代码组、4,716个候选班次，保留11,051条班行及独立的时段、考试、容量、来源和个人页面状态。真实原件、派生数据和定位只在本机Git忽略目录；仓库交付可复跑代码和去敏统计，不包含这些真实数据。候选班身份、完整周集合/校历及本人适用仍待核验，不用于正式选课。

162份培养PDF共2,161页已盘点29类要求。只有第二、第三、第四课堂按用户要求暂缓首版业务，原文与官方总要求保留。完整说明见[去重实施与验收](docs/课程离线去重实施与验收_20261013.md)、[特殊要求盘点](docs/培养方案特殊要求盘点_20261013.md)及[真实语料RAG v2](docs/真实语料RAG设计与最小实施切片_v2_20261013.md)。R3已建立独立首片运行索引、精细分块与精确/词法检索；本轮只重新核验获准的1份原PDF及页定位文本，其余161份只登记历史盘点元数据。人工检索评估、词法/向量对照及模型基于真实原文的回答尚未验收；50题蓝图仍待人工标注。当前对话参考工具继续使用原inputs的54块JSON。

已有本机来源时可执行 `python3 scripts/deduplicate_courses.py`、`python3 scripts/validate_course_dedup.py` 和 `python3 scripts/audit_plan_requirements.py`；它们只读本机原件，不采学校或调用模型。按时间窗归档前当前整合回归249项通过，见[本轮验证](validation/git_archive_20261013_validation.json)。历史验证与当前代码分别绑定，详见[开发进度](docs/开发进度.md)。

## 接手阅读顺序

**2026-10-13最新接续入口：** [推荐能力线规划交接](docs/开发交接摘要_20261013_推荐能力线规划入口.md)。用户确认最终目标为登录后匹配本人培养方案、通过Agent获得多个课表推荐；学校学生接入渠道暂不可用，下一阶段先给出推荐能力线的规划、设计和执行方案。旧交接保留历史，完成度以最新进度、验证及实际代码为准。

推荐能力线方案已另存：[产品与技术设计](docs/推荐能力线产品与技术设计_v1_20261013.md)、[阶段执行方案与新对话交接](docs/推荐能力线执行方案与新对话交接_v1_20261013.md)。用户已明确恢复实施，R0—R2完成独立synthetic闭环与限定验收；界面精简及历史侧栏完成后，已实施R3独立本机参考首片，人工验收仍待完成。R4增强未实施；设计文件保留交付时的历史状态，当前状态以开发进度和本轮证据为准。

2026-10-09 数据接入设计轮新增[数据源、认证与更新契约](docs/数据接入契约与最小切片_v1.md)和[RAG 权限、引用、缓存与评估设计](docs/RAG检索权限引用与评估设计_v1.md)。该轮附带本机只读交接 JSON 参考检索切片，原离线命令继续可用；2026-10-13 对话层新增受限包装，按本人合成方案范围查询，原文外传另需许可；[本轮验收边界](validation/数据接入与参考检索切片_20261009.md)另存。查看示例：

```sh
python3 -m course_assistant.reference_retrieval --plan zju_materials_science_engineering_2026 --query Python
python3 validation/run_reference_eval.py
```

其中 `--plan` 只选择开发者参考范围，不建立本人适用绑定。输出是参考候选与出处，不是 Agent 回复、真实开课或学业结论。

1. [最新交接：数据接入与 RAG 入口](docs/开发交接摘要_20261009_数据接入与RAG入口.md)，再核对[当前开发进度](docs/开发进度.md)与本轮证据；[旧交接](docs/开发交接摘要_20261009.md)中的暂停状态属于前轮。
2. [登录认证与权限设计](docs/登录认证与权限设计_v1.md)。学校登录与个人数据接入是两个独立依赖。
3. [字段字典](docs/数据结构与字段字典_v1.md)与[模块矩阵](docs/系统模块与数据来源矩阵_v2.md)。逻辑对象不要求一对象一表，尚未全面实现每个对象。
4. 原输入：[README](inputs/zju_course_agent_handoff_v1/README.md) → [CODEX_HANDOFF](inputs/zju_course_agent_handoff_v1/CODEX_HANDOFF.md) → [ANALYSIS](inputs/zju_course_agent_handoff_v1/ANALYSIS.md) → [data/README.txt](inputs/zju_course_agent_handoff_v1/data/README.txt)及七份 JSON → [SOURCE_INDEX](inputs/zju_course_agent_handoff_v1/SOURCE_INDEX.md) → [MANIFEST](inputs/zju_course_agent_handoff_v1/MANIFEST.json)。原输入保持原字节、23 候选待审核、真实教学班模板为空。

`course_assistant/` 为标准库服务和计算，`web/` 为原生界面，`demo/` 为独立合成夹具，`tests/` 为回归，`validation/` 保存可复核代码/输入摘要、日志和合成页面证据。私有材料只放 `private/` 或外部目录；真实 Excel、截图、密码、Cookie、Token、会话、运行数据库和个人导出不得提交。Excel 与截图仅为字段参考，不规定用户上传。

远端为 [Ayakatokyo/zju-course-planner](https://github.com/Ayakatokyo/zju-course-planner) 私有仓库，交付前实际重新核对仍为 PRIVATE。恢复开发基线为 `main@716c70d`；用户已授权将当前代码、验证证据和新交接摘要作为一次交付提交并推送，交付提交用 `git log -1 -- docs/开发交接摘要_20261009_数据接入与RAG入口.md` 定位。实际执行结果以 Git 和本机忽略的 `local-only/git_delivery_20261009.json` 回执为准，不以文档预记推送成功。未打包、未创建 PR、未部署。每轮更新开发进度，分别记录实现、测试、页面验收和 Git/部署状态。
