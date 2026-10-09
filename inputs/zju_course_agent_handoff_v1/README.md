# 浙江大学选课助手：Codex开发交接包

版本：v1  
打包日期：2026-10-09（Asia/Shanghai）  
项目题目：**基于 RAG 与约束校验的大学生个性化选课辅助系统设计**。

本包汇总上一轮分析、11页设计文档、结构化样本和原始资料，供后续在Codex中开发。当前已完成设计与样本整理，尚无应用代码、已实现插件或可运行Agent。

## 从这里开始

1. 完整解压压缩包，保留`zju_course_agent_handoff_v1`目录和内部结构。
2. 在开发工作区中让Codex读取本文件，再读[交接摘要](CODEX_HANDOFF.md)和[分析结论](ANALYSIS.md)。
3. 将[交接摘要末尾的启动提示词](CODEX_HANDOFF.md#可直接粘贴给codex的启动提示词)用于接续开发；如已有项目，先让Codex了解现有代码与运行方式。
4. 先完成数据导入、规则计算和显式模拟课表，再接入RAG与对话；实际网页适配以取得真实样本为前提。

本包采用相对路径链接，适合解压后在Markdown编辑器或代码工作区阅读。Mermaid架构图在支持Mermaid的阅读器中渲染，图的文本源码同时保留。

## 文件导航

| 路径 | 内容 | 状态 |
| --- | --- | --- |
| [ANALYSIS.md](ANALYSIS.md) | 上一轮分析结论的完整Markdown版，含规则依据、架构、业务、数据、导入、案例与验收 | 本轮新增 |
| [CODEX_HANDOFF.md](CODEX_HANDOFF.md) | 完成/未完成状态、关键决策、优先级、待补资料、启动提示词 | 本轮新增 |
| [SOURCE_INDEX.md](SOURCE_INDEX.md) | PDF原名与包内名映射、页码、哈希、网页参考及范围 | 本轮新增 |
| [MANIFEST.json](MANIFEST.json) | 文件清单、字节数、SHA256和来源映射 | 本轮新增 |
| [artifacts/选课助手初步设计_v1.docx](artifacts/选课助手初步设计_v1.docx) | 上一轮11页可编辑设计文档 | 原字节副本 |
| [artifacts/选课助手数据样本_v1.zip](artifacts/选课助手数据样本_v1.zip) | 上一轮7文件数据包 | 原字节副本 |
| [data/README.txt](data/README.txt)及同目录6个JSON | 数据ZIP的展开副本，可直接读取 | 与原ZIP逐文件一致 |
| [sources/course_slides.pdf](sources/course_slides.pdf) | 原始课程课件，331页 | 原字节副本 |
| [sources/journalism_2026.pdf](sources/journalism_2026.pdf) | 2026级新闻学培养方案，15页 | 原字节副本，便于识别的文件名 |
| [sources/materials_2026.pdf](sources/materials_2026.pdf) | 2026级材料科学与工程培养方案，15页 | 原字节副本，便于识别的文件名 |
| [sources/business_analytics_2026.pdf](sources/business_analytics_2026.pdf) | 2026级商务大数据分析培养方案，12页 | 原字节副本，便于识别的文件名 |

## 当前数据事实

- 3份培养方案，均为2026级上传样本；尚未确认当前用户的实际适用方案。
- 19条真实课程定义：商务12、材料4、新闻3；并非完整课程目录。
- 23条候选规则：商务15、材料4、新闻4；全部待人工核对，`enforce_automatically=false`。
- 12项来源差异或待补证据，11类拟议导入实体。
- 已取得的真实当期教学班为0条，个人真实学业记录尚未取得。
- 浏览器本次接入遇到统一认证502；这一状态不代表网站长期不可访问。

字段`available_count=0`表示本样本包已取得的真实教学班数量为0，不表示学校没有开课或课程余量为0。所有推荐、规则计算和验收能力当前均为设计目标。

## 开发时如何使用样本

原始JSON中的来源文件名仍沿用上传名称，避免破坏上一轮数据。根据[SOURCE_INDEX.md](SOURCE_INDEX.md)或`MANIFEST.json`中的`source_aliases`将其映射到`sources/`，并通过SHA256核对内容。PDF页码均按物理页从1开始。

原始交接材料保留为输入，应用代码和演示数据另建目录。任何合成的学生、教学班或演示规则都应明确标注为`synthetic`，不填入空的真实教学班模板。后续审核生成的规则集需记录依据和版本。

`import_data_dictionary.json`是导入字段建议，尚不是已建数据库。领域实体、导入字段和后续数据库命名需要在实现时统一。公开选课手册用于概念设计，当前网站字段、分页和状态映射仍待验证。

## 校验说明

打包时校验原DOCX和数据ZIP的字节一致性、原始PDF哈希、JSON解析与数量、候选规则状态、空教学班模板、相对文件链接，以及最终ZIP完整性。`MANIFEST.json`记录除清单自身之外的文件哈希。上述校验说明交接材料完整，不等同于应用功能或官方培养规则审核已经通过。
