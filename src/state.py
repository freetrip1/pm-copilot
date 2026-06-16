"""全流程共享状态。LangGraph 的 StateGraph 以此为 schema。

并行节点（三个 critic）各写一个独立 key（review_pm/dev/qa），互不冲突，
因此无需 reducer。total=False 允许节点只返回部分字段做增量更新。
"""
from typing import Any, Dict, List, TypedDict


class ProjectState(TypedDict, total=False):
    raw_input: str                  # 原始一句话需求
    structured_req: Dict[str, Any]  # ① 结构化需求
    research: Dict[str, Any]        # ② 调研（M1 接真实检索，M0 留空）
    prd_draft: str                  # ③ PRD 草稿
    current_prd: str                # M1 评审/收敛迭代中的“当前 PRD”
    iteration: int                  # M1 循环计数
    eval: Dict[str, Any]            # M1 evaluate 的打分与判定结果
    review_pm: List[Any]            # ④ 资深PM 视角问题清单
    review_dev: List[Any]           # ④ 开发 视角问题清单
    review_qa: List[Any]            # ④ QA 视角问题清单
    prd_final: str                  # ⑤ 收敛后终版 PRD
    issue_log: Dict[str, Any]       # ⑤ 问题处置记录
    breakdown: Dict[str, Any]       # ⑥ 用户故事/验收/测试用例
    prototype: Dict[str, Any]       # ⑧ 原型：workdir / build_spec / agent_ok / index_exists / trace
    acceptance_report: Dict[str, Any]  # ⑨ 验收：file_ok / html_bytes / coverage / passed / gaps / comment

    # ===== 新架构字段（M4：扇出终审 + 验收回路）=====
    prd_flowchart: str              # ③b 流程图（mermaid 源码）
    prd_iteration: int              # 五b→prd_draft 回路计数（上限 PRD_MAX_ITERS）
    eval_pm: Dict[str, Any]         # 五b 终审·产品视角投票 {verdict:"pass"|"fail", reasons:[...]}
    eval_dev: Dict[str, Any]        # 五b 终审·技术视角投票
    eval_qa: Dict[str, Any]         # 五b 终审·质量视角投票
    eval_gate: Dict[str, Any]       # 五b 闸门 {fail_count, verdict, iteration}
    proto_iteration: int            # 九→八 回路计数（上限 PROTO_MAX_ITERS）
    accept_pm: Dict[str, Any]       # 九 验收·产品视角投票
    accept_dev: Dict[str, Any]      # 九 验收·技术视角投票
    accept_qa: Dict[str, Any]       # 九 验收·质量视角投票
    accept_gate: Dict[str, Any]     # 九 闸门 {fail_count, verdict, iteration}

    # ===== 收敛即止：记上一轮 fail 票，用于"未改善就提前停" =====
    eval_prev_fail: int             # 五b 上一轮 fail 票数
    accept_prev_fail: int           # 九 上一轮 fail 票数
