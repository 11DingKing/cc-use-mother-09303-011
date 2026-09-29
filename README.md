# 国际学生支持案件

本项目维护国际学生支持案件的领域约定、角色边界与样例数据，供后端服务、接口和自动化验证统一使用。当前契约覆盖国际学生、支持专员、项目协调员，并明确案件去重、同意范围、服务时限、并发认领、可见边界等关键约束。

## 目录

- `domain/contract.json`：领域角色、状态、约束和样例。
- `src/domain_contract/`：契约读取与确定性校验。
- `src/case_backend/`：从零建设的案件后端（纯标准库、线程安全）。
- `tools/check_contract.py`：命令行摘要检查。
- `tools/demo_scenario.py`：双办公室重复求助场景的端到端演示。
- `tests/`：契约完整性与案件后端回归测试。

## 案件后端

`src/case_backend/` 按契约实现核心领域逻辑：

- `models.py`：状态（求助/分派/处置/转介/暂停/关闭）、问题类型、材料类别、同意范围、时限、外部依赖等模型。
- `routing.py`：按问题类型 × 机构权限 × 学生同意范围路由处理组。
- `service.py`：登记、重复线索合并（来源保留、同意取交集）、分派、认领（锁内检查，只成功一次）、转介、暂停、升级、关闭、重开。
- `privacy.py`：资料可见边界——学生见全部，专员只见同意类别，协调员只见元数据。
- `views.py`：学生统一进度（唯一负责方、公开时间线）与协调员总览（逾期、外部依赖、敏感字段脱敏）。

快速体验：`python3 tools/demo_scenario.py`

## 验证

测试命令：`python3 -m unittest discover -s tests -v`

编译命令：`python3 -m compileall -q src tools tests`

命令行检查：`python3 tools/check_contract.py domain/contract.json`
