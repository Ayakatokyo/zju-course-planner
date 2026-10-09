# 浙江大学选课助手

结合适用培养方案、个人学业与认定、选课偏好和当期教学班，提供可解释、可调整的选课规划、比较、保存及导出。RAG 检索依据，Agent 组织对话与工具调用，确定性程序负责学分、资格和冲突计算。

**当前是设计与代码草稿交接，尚未交付完成整体验收的应用。** 用户要求本轮补齐登录权限设计、整理交接并建立 Git/GitHub 仓库；业务代码实现留到新的对话。已有代码保留，不代表新字段结构、登录或真实学生检索已经实现。

## 接手阅读顺序

1. [本轮开发交接摘要](docs/开发交接摘要_20261009.md)：最新纠偏、已有产物、缺口和下一轮开发顺序。
2. [登录认证与权限设计](docs/登录认证与权限设计_v1.md)：可信身份绑定、本人资源授权、模拟身份隔离和待实现安全验收。
3. [开发进度](docs/开发进度.md)：统一进度入口，区分历史测试、当前验证及未完成验收。
4. [数据结构与字段字典](docs/数据结构与字段字典_v1.md)与[模块和数据来源矩阵](docs/系统模块与数据来源矩阵_v2.md)：逻辑对象、字段、来源、未知状态和模块边界。
5. 原输入依次阅读：[README](inputs/zju_course_agent_handoff_v1/README.md) → [CODEX_HANDOFF](inputs/zju_course_agent_handoff_v1/CODEX_HANDOFF.md) → [ANALYSIS](inputs/zju_course_agent_handoff_v1/ANALYSIS.md) → [data/README.txt](inputs/zju_course_agent_handoff_v1/data/README.txt)及七份 JSON，再核对 [SOURCE_INDEX](inputs/zju_course_agent_handoff_v1/SOURCE_INDEX.md) 和 [MANIFEST](inputs/zju_course_agent_handoff_v1/MANIFEST.json)。原包中的“尚无代码、未取得个人资料”是当时状态，以最新交接解释后续进展。

## 当前目录

| 目录 / 文件 | 用途 |
| --- | --- |
| `inputs/zju_course_agent_handoff_v1/` | 原交接输入，保留原字节与清单；不得改写候选规则或空真实教学班模板 |
| `course_assistant/`、`web/` | Python 标准库、SQLite、原生 Web 代码草稿，尚无认证与用户隔离 |
| `demo/bundle.json` | 独立 synthetic 学生、规则、教学班和校历，不能作为官方记录 |
| `tests/` | 57 个现有测试方法；历史局部结果不等于当前整体通过 |
| `docs/`、`PRODUCT.md` | 设计、进度、资料盘点及交接 |
| `validation/` | 后续保存可复核验证证据；当前没有应用整体验收日志 |
| `private/`（忽略） | 本机私有资料定位，不上传 GitHub；实际样本仍在外部路径 |
| `runtime/`、`tmp/`（忽略） | 数据库、运行产物及可重建临时文件 |

## 开发入口与限制

当前草稿入口是 `python3 -m course_assistant.server --port 8765`，服务绑定本机回环地址。新对话开发时可从本目录执行；本轮没有启动服务，也没有验证页面。这条命令是代码入口说明，不能作为“已经验证可运行”的验收结果。当前草稿只用于独立模拟数据开发，在完成认证、对象授权及多用户隔离之前不得接入真实学生个人记录或对外部署。

测试入口为 `python3 -m unittest discover -s tests -v`。下一轮先运行并保存基线结果，再针对实际修复做验证。原八项验收和新增登录权限验收均仍待完成，详见进度与权限文档。

Excel 与截图仅用作字段参考；未来用户登录后只能检索本人档案，姓名或学号输入不能授予访问他人数据的权限。学校身份验证和学业数据接入是两项依赖：不能假定学校已提供本项目可用的认证协议或档案 API，不能建立学校密码库。

远端为 [Ayakatokyo/zju-course-planner](https://github.com/Ayakatokyo/zju-course-planner) 私有仓库，本地 main 已关联 origin/main 并推送；建立与核验记录见[开发进度](docs/开发进度.md)。每轮开发后同步该文件；原始输入、个人资料及凭据的保留边界见 [AGENTS.md](AGENTS.md)。
