# 课程数据盘点与实现边界

盘点日期：2026-10-09。读取范围：用户提供交接包内 JSON、来源索引和文件清单。原资料的提取结论不等于本轮已重新逐页审核全部 PDF，也不等于校方确认。

范围说明（2026-10-09 更新）：本文是原交接包的历史盘点。之后提供的 Excel 和两张选课截图用于理解字段、结构与状态；原包的“个人记录未提供、真实教学班为零”不代表后续全部样本范围。Excel 不据此确定为真实使用者上传入口；用户已确认未来按基本信息检索档案、按偏好针对性检索的方向，当前不展开设计。最新进度以 [开发进度](开发进度.md) 为准。

## 当前确实取得的数据

- 3 份 2026 级培养方案，当前用户适用性未确认。
- 19 条课程定义，非完整课程目录。课程定义用于精确身份匹配和来源展示。
- 23 条候选规则，全部待审核，禁止自动执行。
- 12 项来源问题，保留来源差异及缺失政策。
- 0 条真实教学班，0 份当前用户学业记录。available_count=0 仅表示未取得教学班。

## 19 条课程定义

|方案|课程号|课程名|样本学分|建议修读时间|出处（PDF物理页）|
|---|---|---|---:|---|---|
|商务大数据分析（信息管理与信息系统-统计学）|CS1001G|C程序设计基础及实验|4.0|一(秋冬)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第3页|
|商务大数据分析（信息管理与信息系统-统计学）|ECON1001F|微观经济学（甲）|3.0|一(春夏)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第4页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT2041M|商业数据分析的Python基础|2.0|一(夏)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第4页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT2057M|数据库原理与智能应用|2.0|二(秋)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第4页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT2006M|数据结构|3.0|二(秋冬)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第4页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT2054M|生成式人工智能原理及应用|2.0|二(春)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第4页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT3055M|商务数据挖掘|3.0|三(秋冬)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第5页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT3074M|智能可视化分析|1.5|三(春)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第5页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT3034M|项目管理|2.0|二(春)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第5页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT3073M|智能系统设计与开发|3.0|三(春夏)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第5页|
|商务大数据分析（信息管理与信息系统-统计学）|MATH3403M|统计学习|3.0|三(春夏)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第6页|
|商务大数据分析（信息管理与信息系统-统计学）|MGMT4019M|毕业论文|6.0|四(春夏)|[document (1).pdf](../inputs/zju_course_agent_handoff_v1/sources/business_analytics_2026.pdf) 第6页|
|材料科学与工程|CS1002G|C程序设计基础|3.0|一(春夏)|[document-2.pdf](../inputs/zju_course_agent_handoff_v1/sources/materials_2026.pdf) 第3页|
|材料科学与工程|CS1006G|Python程序设计|3.0|一(春夏)|[document-2.pdf](../inputs/zju_course_agent_handoff_v1/sources/materials_2026.pdf) 第3页|
|材料科学与工程|MSE3123M|基于机器学习的材料设计|3.0|三(冬)|[document-2.pdf](../inputs/zju_course_agent_handoff_v1/sources/materials_2026.pdf) 第6页|
|材料科学与工程|MSE3124M|多尺度材料模拟|2.0|三(春)|[document-2.pdf](../inputs/zju_course_agent_handoff_v1/sources/materials_2026.pdf) 第6页|
|新闻学|CS0400G|人工智能初步|2.0|二(秋冬)|[document-3.pdf](../inputs/zju_course_agent_handoff_v1/sources/journalism_2026.pdf) 第5页|
|新闻学|MIC1175M|智能传播导论|2.0|一(秋冬)|[document-3.pdf](../inputs/zju_course_agent_handoff_v1/sources/journalism_2026.pdf) 第5页|
|新闻学|MIC2158M|智能传播|2.0|二(春)|[document-3.pdf](../inputs/zju_course_agent_handoff_v1/sources/journalism_2026.pdf) 第7页|

## 不能从这 19 条课程推出的内容

当期是否开课、教师、教学班号、具体周次/星期/节次、校区地点、余量、限选对象、强制先修、正式中选和个人已获学分均未取得。建议修读学期不能代替开课事实；课程名相似不能代替课程号或正式互认依据。

## 独立演示资料

`demo/bundle.json` 是算法夹具：1 名合成学生、9 条 DEMO 规则、7 个模拟教学班、模拟校历和状态记录。沿用材料样本中的4条课程定义，另加3条DEMO课程；标签和归属认定均为合成演示。所有演示教学班和规则都没有官方效力，不能用于真实选课或毕业审核。

## 原交接阶段列出的数据依赖（历史记录）

以下为此前草稿的数据依赖，保留供核对；不构成本轮要求用户补交材料，也不决定未来数据的检索或接入方式。

1. 用户确认实际适用方案、专业/项目、入学年、方案版本、路径、外语层级、目标学期。
2. 经授权的学业记录，含修读次数、通过/在修/认定状态和学分归属。
3. 当期教学班完整样本，含采集时间、范围、分页、校历及周次语义。
4. 候选规则审核、官方互认与认定清单、来源差异的解释，以及适用的先修/限选政策。

首版代码验证的是这些状态的建模与计算流程；真实数据依赖仍未完成。
