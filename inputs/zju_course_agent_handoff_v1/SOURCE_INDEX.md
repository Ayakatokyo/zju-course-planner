# 来源索引与核对范围

整理日期：2026-10-09（Asia/Shanghai）。本表用于将上一轮分析及JSON引用定位到包内原始材料。上传文件的名称为原名，包内采用便于识别的名称；PDF内容未修改，页码均为物理页，从1开始。

## 1. 用户提供的原始文件

| 编号 | 上传原名 | 包内路径 | 内容与页数 | 主要使用位置 |
| --- | --- | --- | --- | --- |
| S1 | 大数据信息系统分析与设计课件.pdf | [sources/course_slides.pdf](sources/course_slides.pdf) | 课程课件，331页 | 第4、6页作业与阶段安排；170—173页可行性；226页需求获取；331页设计过程 |
| S2 | document-3.pdf | [sources/journalism_2026.pdf](sources/journalism_2026.pdf) | 2026级新闻学，15页 | 第1、3页学分；4—8页要求；10页性质差异；14—15页图示差异 |
| S3 | document-2.pdf | [sources/materials_2026.pdf](sources/materials_2026.pdf) | 2026级材料科学与工程，15页 | 第1—2页学分；3页计算机任选；5—6页模块；8—9页认定及附注 |
| S4 | document (1).pdf | [sources/business_analytics_2026.pdf](sources/business_analytics_2026.pdf) | 2026级商务大数据分析（信息管理与信息系统-统计学），12页 | 第1页学分及双学位说明；3—6页课程与要求；7页二三四课堂 |

### SHA256

| 编号 | SHA256 |
| --- | --- |
| S1 | `567a4becf36d428af631cd17d2548b46190385c6f37f6ebed0e34f335868cb44` |
| S2 | `ebd050e4cfe529608f7d0187e413c967bf271de04be7e64bd770428d0c78486b` |
| S3 | `21ee7cbd3a116cdac57804cb5e11d5bd72d13c39c26bed7c412db57c51b3ce2f` |
| S4 | `99bde939d96d367918b74092cd6973e95d39125d9e3da73521fadc19bd8bf87e` |

同样的映射以结构化形式保存在[MANIFEST.json](MANIFEST.json)的`source_aliases`中。数据样本仍保留原文件名和哈希，解析时可据此定位；不要将原名不同当成内容不同。

上传来源与文字核对不等于已确认当前官方生效版本，也不代表适用于当前学生。新闻学第7页15/16学分、第4/10页课程性质和末尾图示差异均保留待核验；商务双学位绩点阈值及部分外部认定清单尚缺。

## 2. 上一轮已核查的公开网页依据

以下为上一轮分析使用的官方来源，供接手者按需复核。本轮任务是整理交接包，没有重新访问网站，也没有取得新增当期开课数据。

| 编号 | 来源 | 用途和适用范围 |
| --- | --- | --- |
| W1 | [浙江大学2026级新生第一学年秋冬选课通知](https://zdbk.zju.edu.cn/jwglxt/xtgl/xwck_ckLoginNews.html?xwbh=569D011381027E3FE06329B3CA0A448F) | 2026-07-15发布；用于了解通知覆盖范围内的预置课程、选课和学分上限等规则，不能泛化到所有学生与学期 |
| W2 | [浙江大学选课操作手册](https://zdbk.zju.edu.cn/jwglxt/zjdxFilemgr/file_download.html?uid=569CF3B93BFFD98CE0632AB3CA0A55C6) | W1公开附件，9页；第2页列表加载，第6页多志愿，第8页待定与已选状态；不证明当前DOM已适配 |
| W3 | [Chrome activeTab文档](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab) | 解释主动操作触发的临时当前页权限 |
| W4 | [Chrome Content scripts文档](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts) | 解释内容脚本读取DOM和传递信息的能力 |
| W5 | [OR-Tools排班示例](https://developers.google.com/optimization/scheduling/employee_scheduling) | 约束与偏好优化的可选实现参考，尚未在本项目应用 |

网页以普通URL保留，未将会话内部引用标记写入Markdown。在线资料可能更新，规则真正启用时需重新核对版本与适用性。本包没有下载这些网页的完整离线副本。

## 3. 待验证的业务页面入口

| 页面 | 去除个人身份参数后的定位 |
| --- | --- |
| 培养方案查询 | [培养方案查询入口](https://zdbk.zju.edu.cn/jwglxt/pyfagl/pyfaxxcx_cxPyfaxscxIndex.html?gnmkdm=N153020&layout=default) |
| 选课页面 | [选课入口](https://zdbk.zju.edu.cn/jwglxt/xsxk/zzxkghb_cxZzxkGhbIndex.html?gnmkdm=N253530&layout=default) |

以上只用于开发定位，已去除用户身份参数；未验证去参后的链接能否直接访问。2026-10-09本次云会话中原入口跳转登录，统一认证页面返回502。后续需要在可用、经授权的环境中重新验证，或使用用户提供的导出样本。未抓取真实教学班、未确认页面字段，也未执行任何选课或退课。

## 4. 提取与渲染备注

培养方案同时包含文字表格和图形，单纯文本提取可能漏掉学分结构图。上一轮环境使用Poppler渲染某些未嵌入的中文字体时出现方框，改用PyMuPDF后可正常查看；这属于当时的字体渲染问题，不是原PDF损坏的证据。后续提取需核对跨页表头、图形和水印，保存页码与原始附注。

文件和样本经过结构与出处核对。真实访谈、完整课程目录、个人学业记录、当期开课表、应用代码、网页插件、功能测试与性能指标仍需后续工作补齐。
