# 2026-09-14 本学期课程关系核对 / Current course-rule audit

状态：只读核对完成；代码修复与发布情况另行记录。此报告不构成生产数据迁移批准。

## 来源与范围

- [学校本科课程目录](https://pcc.hkust-gz.edu.cn/undergraduate/program-course-catalog)，公开目录搜索 API `POST /api/bdp/pg-course-catalog?size=1000&page=1`，筛选 `career_type=UG` 与 `term_code`。返回分页总数与记录数一致，无截断。
- 本学期 2610（2026–27 Fall）：266 门，学校 `adsSyncTime=2026-09-14 05:35:00`。
- 上学期 2530（2025–26 Spring）：224 门。共同课程 224 门，新增 42 门，未发现上学期课程从本学期目录消失。
- 逐门查询学校正式站 `/api/courses/by-code/<code>/overview`：266 门的 798 项先修／共修／互斥规则原文，统一空值和空白后，与本学期 PCC **零差异**。本次不需要导入、覆盖或删除生产数据。
- 原图谱仍读取 `/api/scheduler/map/components` 与 `/lines`：226 个组件、158 条几何线段，其中课程节点 128 个。几何线段数不等于课程关系数，不能与新图谱线段数直接相减作为学校调整数。
- 本报告核对本科课程关系，不将开课班次、选课名额或研究生目录混为本科目录规则。

## 学期之间的实际变化

16 门既有课程，14 项先修和 5 项互斥变化；共修无变化。后修由先修反向推导，不维护独立清单。以下 AND 为全部满足，OR 为任选一项；空表示学校该字段为空。

| 课程 | 关系 | 上学期 | 本学期 |
|---|---|---|---|
| AIAA2711 | 先修 | UFUG 1103 OR UFUG 1106 | (UFUG 1103 OR UFUG 1106) AND (UFUG 2102 OR UFUG 2103) |
| AIAA2711 | 互斥 | 空 | DSAA 2088 |
| DSAA2088 | 先修 | (DSAA 2011 OR DSAA 2012 OR AIAA 3111) AND UFUG 2103 AND UFUG 2104 | (DSAA 2011 OR DSAA 2012 OR AIAA 3111) AND (UFUG 2102 OR UFUG 2103) AND UFUG 2104 |
| DSAA2088 | 互斥 | 空 | AIAA 2711 |
| DSAA3055 | 先修 | DSAA 2012 OR AIAA 3111 | DSAA 2031 |
| DSAA4040 | 先修 | DSAA 2031 AND UFUG 1601 | DSAA 2031 AND (UFUG1601 OR UFUG1603) |
| FTEC2120 | 先修 | UFUG 2103 | (UFUG 1102 OR UFUG 1105) AND (UFUG 1103 OR UFUG 1106) |
| FTEC3310 | 先修 | UFUG 1103 OR UFUG 1106; UFUG 2103 | UFUG 1103 OR UFUG 1106 |
| MICS3090 | 先修 | ROAS 2200 | MICS 2010 |
| MICS4040 | 先修 | ROAS 2200 | MICS 2010 |
| ROAS4220 | 先修 | UFUG 2102 & UFUG 2601 | [UFUG 2102 OR UFUG 2103] AND [UFUG 2601 OR UFUG 2602] |
| UFUG1301 | 互斥 | UFUG 1302 and UFUG 1303 | 空 |
| UFUG1302 | 互斥 | UFUG 1301 | 空 |
| UFUG1303 | 先修 | UFUG 1302 | 空 |
| UFUG1303 | 互斥 | UFUG 1301 | 空 |
| UFUG2102 | 先修 | UFUG 1103 OR UFUG 1106 | 空 |
| UFUG2103 | 先修 | UFUG 1103 OR UFUG 1106 | 空 |
| UFUG2106 | 先修 | UFUG 1601 | UFUG 1601 or UFUG 1603 |
| UFUG2602 | 先修 | UFUG 1601 | UFUG 1601 or UFUG 1603 |

## 应修复的显示问题

1. 经典图谱必须读取与课程详情相同的规范课程关系，不能继续把历史 scheduler 图谱当作当前规则。
2. 学校的省略编号写法（例如 `UFUG 1103 or 1106`）、方括号和显式 `(a)…; and (b)…` 分组，应在只读展示时展开；原文及历史导入快照保持不变。否则会遗漏可替代的先修课程和相应后修链接。
3. 专业限制、考试成绩或其他复杂条件不能变成无条件必修边。保留原文，并把无法完整解析的课程引用标为条件引用，不显示确定先修箭头。
4. 按[教务处定义](https://ars.hkust-gz.edu.cn/programs-courses/guides-on-course-registration/faq-ug-validation/)，共修可提前或同期修读；原图例“必须同一学期”过严。
5. 官方图谱只显示正式目录目标课程与其直接引用的课程身份。3 个 UCUG 1052 字母后缀身份仍被当前学校互斥原文引用，应保留引用节点；不恢复这些历史身份的旧规则，也不据目录缺席推断已停课。

## 数据和回滚边界

代码只修改读路径与显示。目标生产数据库写入数：0；无 schema、种子、课程、排课或 curriculum 迁移。现有官方同步任务及持久解析器版本保持不变。图谱保留默认兼容接口，新增 `catalog=official` 读选项；经典前端使用该选项。

若以后学校原文发生变化，仍由已有受控官方同步流程处理，不能以本报告批准其他数据变更。代码回滚遵守既有配对 SHA 与学校 runbook；本次无数据库降级。

## 快照摘要

- `pcc-2530.json` SHA-256: `c61261f1082a9547746ffaef359ba0d6d30f2af15ad9f3ab0d85e9b396925e94`。
- `pcc-2610.json` SHA-256: `caff111ea484ac5a00f0c6e306ca17bc324f8214fb3bfb568f87cb0911d025f1`。

配套 `2026-09-14-course-rule-diff.json` 保存全部 19 项字段差异；完整原始只读证据保存在本次任务临时目录，不包含用户资料或凭据。
