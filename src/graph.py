"""M4 主链路图装配（扇出终审 + 验收回路）。

连线（节点 id 即 LangGraph 节点名）：
  START → structure → research → prd_draft
  prd_draft →(扇出 critic_pm/dev/qa)→ converge
  converge →(扇出 eval_pm/dev/qa)→ eval_gate
  eval_gate →(条件 route_eval) { "revise": prd_draft, "done": breakdown }
  with_prototype=False: breakdown → END
  with_prototype=True : breakdown → prototype
      prototype →(扇出 accept_pm/dev/qa)→ accept_gate
      accept_gate →(条件 route_accept) { "revise": prototype, "done": END }

两个 evaluator-optimizer 回路：
  五b 回路：critic 由 prd_draft 直接扇出；终审三票汇入 eval_gate，
           ≥2 fail 且未超 PRD_MAX_ITERS 时条件边回 prd_draft（带反馈定向改进）。
  九 回路：原型由 breakdown 进入；验收三票汇入 accept_gate，
          ≥2 fail 且未超 PROTO_MAX_ITERS 时条件边回 prototype（带反馈重造）。
三个 critic / 三个 eval / 三个 accept 各写独立 state key，并行无冲突，无需 reducer。
"""
from langgraph.graph import StateGraph, START, END

from .state import ProjectState
from .nodes import (
    structure,
    research,
    prd_draft,
    critic_pm,
    critic_dev,
    critic_qa,
    converge,
    eval_pm,
    eval_dev,
    eval_qa,
    eval_gate,
    route_eval,
    breakdown,
    prototype,
    accept_pm,
    accept_dev,
    accept_qa,
    accept_gate,
    route_accept,
)


def _assemble(with_prototype: bool) -> StateGraph:
    """装配主链路拓扑并返回未编译的 StateGraph。

    with_prototype=False：eval_gate(done) → breakdown → END。
    with_prototype=True ：breakdown → prototype → 九验收回路 → END（完整图）。

    供 build_graph（编译即返回）与 build_resumable_graph（带 checkpointer +
    interrupt_before 再编译）复用，保证两条入口的拓扑严格一致。
    """
    g = StateGraph(ProjectState)

    # 主干节点
    g.add_node("structure", structure)
    g.add_node("research", research)
    g.add_node("prd_draft", prd_draft)
    g.add_node("critic_pm", critic_pm)
    g.add_node("critic_dev", critic_dev)
    g.add_node("critic_qa", critic_qa)
    g.add_node("converge", converge)
    g.add_node("eval_pm", eval_pm)
    g.add_node("eval_dev", eval_dev)
    g.add_node("eval_qa", eval_qa)
    g.add_node("eval_gate", eval_gate)
    g.add_node("breakdown", breakdown)

    # 主干：① → ② → ③
    g.add_edge(START, "structure")
    g.add_edge("structure", "research")
    g.add_edge("research", "prd_draft")

    # ④ 扇出：prd_draft 直接触发三 critic 并行（亦是五b 回路重入点）
    g.add_edge("prd_draft", "critic_pm")
    g.add_edge("prd_draft", "critic_dev")
    g.add_edge("prd_draft", "critic_qa")

    # ⑤ fan-in：三 critic 汇入 converge
    g.add_edge("critic_pm", "converge")
    g.add_edge("critic_dev", "converge")
    g.add_edge("critic_qa", "converge")

    # ⑤b 扇出：converge 触发三终审评委并行
    g.add_edge("converge", "eval_pm")
    g.add_edge("converge", "eval_dev")
    g.add_edge("converge", "eval_qa")

    # ⑤b fan-in：三票汇入 eval_gate
    g.add_edge("eval_pm", "eval_gate")
    g.add_edge("eval_dev", "eval_gate")
    g.add_edge("eval_qa", "eval_gate")

    # ⑤b 条件边：构成 PRD evaluator-optimizer 回路
    g.add_conditional_edges(
        "eval_gate",
        route_eval,
        {"revise": "prd_draft", "done": "breakdown"},
    )

    if with_prototype:
        g.add_node("prototype", prototype)
        g.add_node("accept_pm", accept_pm)
        g.add_node("accept_dev", accept_dev)
        g.add_node("accept_qa", accept_qa)
        g.add_node("accept_gate", accept_gate)

        # ⑥ → ⑧ 原型
        g.add_edge("breakdown", "prototype")

        # ⑨ 扇出：prototype 触发三验收评委并行
        g.add_edge("prototype", "accept_pm")
        g.add_edge("prototype", "accept_dev")
        g.add_edge("prototype", "accept_qa")

        # ⑨ fan-in：三票汇入 accept_gate
        g.add_edge("accept_pm", "accept_gate")
        g.add_edge("accept_dev", "accept_gate")
        g.add_edge("accept_qa", "accept_gate")

        # ⑨ 条件边：构成原型验收回路
        g.add_conditional_edges(
            "accept_gate",
            route_accept,
            {"revise": "prototype", "done": END},
        )
    else:
        g.add_edge("breakdown", END)

    return g


def build_graph(with_prototype: bool = False):
    """构建并编译主链路 graph，返回可 invoke 的编译产物。

    with_prototype=False（默认）：eval_gate(done) → breakdown → END。
    with_prototype=True：breakdown → prototype → 验收回路 → END。

    保持原签名与行为不变（向后兼容/测试用），不带 checkpointer、不带中断。
    """
    return _assemble(with_prototype).compile()


def build_resumable_graph(checkpointer):
    """构建【完整图】（含 prototype→三 accept→accept_gate→END，即 with_prototype=True
    的全拓扑），编译时挂上 checkpointer 并在 prototype 节点前中断。

    这样默认 invoke({"raw_input": ...}, config) 会一路跑到 breakdown 后、
    prototype 前暂停存档（human-in-the-loop）；要原型时用同一 thread_id
    invoke(None, config) 续跑八/九，不重跑 PRD。

    断点用 interrupt_after=["breakdown"]（而非 interrupt_before=["prototype"]）：
    breakdown 在整条链里只执行一次（PRD 回路在它之前、accept 回路在它之后），
    所以暂停只发生一次；而 ⑨ accept_gate 判 revise 时回退到 prototype 不经过
    breakdown，不会再次触发断点——否则 prototype 前的断点会把 ⑨ 返工回路也截停。
    暂停后 get_state().next 即为 ("prototype",)，故 _is_paused 的 "prototype" 判定不变。
    """
    return _assemble(with_prototype=True).compile(
        checkpointer=checkpointer,
        interrupt_after=["breakdown"],
    )
