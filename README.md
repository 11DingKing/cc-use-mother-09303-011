# 国际学生支持案件

本项目维护国际学生支持案件的领域约定、角色边界与样例数据，并提供一套从头建设的案件后端，供接口和自动化验证统一使用。当前契约覆盖国际学生、支持专员、项目协调员，并明确案件去重、同意范围、服务时限、并发认领、资料可见边界、来源保留等关键约束。

## 目录

- `domain/contract.json`：领域角色、状态、约束和样例。
- `src/domain_contract/`：契约读取与确定性校验。
- `src/case_backend/`：案件后端（领域模型、状态机、分派路由、服务、视图、HTTP 接口）。
- `tools/check_contract.py`：命令行摘要检查。
- `tools/demo_scenario.py`：端到端场景演示（重复求助 → 合并 → 统一进度 → 逾期与外部依赖）。
- `tests/`：契约完整性与后端行为回归测试。

## 后端能力

- **分派**：依据问题类型、机构权限（处理组可承接的类型）与学生同意范围自动路由；同意不足时由学生 `grant_consent` 后再分派。
- **时限**：按处理组 SLA 记录响应时限（受理→认领）与办结时限（受理→关闭）。
- **状态机**：求助 → 分派 → 处置，处置中可转介、暂停、升级，各状态可关闭，关闭可重开；全部推进留痕。
- **认领**：在仓储锁内原子判定，并发认领只成功一次，且仅限本分派组专员。
- **合并**：同一学生的重复线索可并入主案件；被合并线索以来源留痕（编号、来源办公室、行动流水）完整保留，不能重开。
- **外部依赖**：转介登记依赖与期望时间；存在未决依赖时不能关闭。
- **可见边界**：行动按 `student_visible` 与敏感级别标记；学生视图只含可见行动并按材料键去重，协调员视图只含调度字段（状态、时限、逾期、依赖），不输出描述与行动内容。

## 验证

测试命令：`python3 -m unittest discover -s tests -v`

编译命令：`python3 -m compileall -q src tools tests`

命令行检查：`python3 tools/check_contract.py domain/contract.json`

场景演示：`python3 tools/demo_scenario.py`

## HTTP 接口

启动：`PYTHONPATH=src python3 -m case_backend.api 127.0.0.1 8000`

身份通过请求头传递：`X-Actor-Id`、`X-Actor-Role`（`student` / `specialist` / `coordinator`）、`X-Group-Id`（专员必填）。

| 方法与路径 | 说明 |
| --- | --- |
| `POST /leads` | 学生求助，登记线索 |
| `POST /cases/{id}/triage` | 自动分派并设定时限 |
| `POST /cases/{id}/claim` | 专员认领（重复认领返回 409） |
| `POST /cases/{id}/actions` | 记录行动（可标记学生可见、材料键） |
| `POST /cases/{id}/refer` · `POST /cases/{id}/dependencies/{dep}/resolve` | 转介与了结外部依赖 |
| `POST /cases/{id}/pause` · `resume` · `escalate` · `deescalate` | 暂停、恢复、升级、退回 |
| `POST /cases/{id}/close` · `reopen` | 关闭（记录结果）与重开 |
| `POST /cases/{id}/consent` | 学生扩大同意范围 |
| `POST /cases/merge` | 合并重复线索（保留来源） |
| `GET /students/{id}/progress` | 学生统一进度 |
| `GET /coordinator/overview` | 协调员逾期与外部依赖概览 |
