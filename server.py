"""FastAPI 后端：把 M0/M1/M2 LangGraph 管线以 SSE 流式暴露给前端。

约束：load_dotenv() 必须在 import src.graph 之前执行，确保 ZAI key 等
环境变量在 graph 模块（及其内部的 LLM 客户端）导入时已就绪。

端点：
  GET /api/health → {"ok": true}
  GET /api/run?requirement=<text>&prototype=<true|false> → SSE 事件流
      跑到 prototype 前中断时发 {type:"paused", thread_id}；prototype=true 时
      暂停后立即续跑直到 {type:"done"}。
  GET /api/resume?thread_id=<tid> → SSE 事件流，从存档续跑 ⑧/⑨ 到 {type:"done"}
"""
import json
import os
import sys

# Windows 控制台默认 GBK：节点里 print 的中文/符号（如 ↻ ↻）会触发
# UnicodeEncodeError 并把 graph.stream 打断。强制 stdout/stderr 用 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from dotenv import load_dotenv

# 关键：先加载环境变量，再 import 触发 LLM 客户端初始化的模块。
load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse  # noqa: E402

import uuid  # noqa: E402

from src.graph import build_resumable_graph  # noqa: E402
from src.checkpoint import make_app_checkpointer  # noqa: E402

app = FastAPI(title="PM Copilot API")

# 应用级长生命周期 SqliteSaver + 完整可续跑图（interrupt_before=["prototype"]）。
# 连接 check_same_thread=False，供 uvicorn 多线程跨请求共享同一 DB。
_CHECKPOINTER = make_app_checkpointer()
_GRAPH = build_resumable_graph(_CHECKPOINTER)


def _is_paused(config) -> bool:
    """图是否停在 prototype 前的中断点。"""
    return "prototype" in (_GRAPH.get_state(config).next or ())


def _thread_exists(config) -> bool:
    """该 thread_id 是否已有存档。"""
    return _GRAPH.get_state(config).created_at is not None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/prototype")
def prototype_html():
    """直接吐出 ⑧ 原型节点生成的自包含单文件 HTML，供浏览器新标签页渲染运行。

    供浏览器导航（非 fetch）使用，不依赖 CORS。文件不存在时返回 404。
    """
    path = os.path.join(os.getcwd(), "prototype_out", "index.html")
    if not os.path.isfile(path):
        return JSONResponse({"error": "原型尚未生成"}, status_code=404)
    return FileResponse(path, media_type="text/html")


def _sse(payload: dict) -> str:
    """把一个事件序列化成 SSE 帧。ensure_ascii=False 保留中文。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_updates(payload, config):
    """流式跑一段图，把每个节点更新 yield 成 SSE node 事件。

    payload=None 时为 resume（从存档续跑）；否则为带 raw_input 的初次 invoke。
    """
    for chunk in _GRAPH.stream(payload, config, stream_mode="updates"):
        # chunk 形如 {node_name: update_dict}
        for node, update in chunk.items():
            yield _sse({"type": "node", "node": node, "data": update})


@app.get("/api/run")
def run(requirement: str, prototype: str = "false"):
    with_prototype = prototype.lower() == "true"
    tid = uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": tid}}

    def gen():
        try:
            yield _sse({"type": "start", "thread_id": tid})
            # 跑到 breakdown 后、prototype 前中断
            yield from _stream_updates({"raw_input": requirement}, config)

            if with_prototype:
                # 一气呵成：暂停后立即续跑 ⑧/⑨ 直到真正结束
                if _is_paused(config):
                    yield from _stream_updates(None, config)
                yield _sse({"type": "done", "thread_id": tid})
            else:
                # 关原型：跑到 paused 暂停，交由前端按钮续跑
                if _is_paused(config):
                    yield _sse({"type": "paused", "thread_id": tid})
                else:
                    # 理论上不会发生（完整图必经 prototype），兜底也发 done
                    yield _sse({"type": "done", "thread_id": tid})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/resume")
def resume(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}

    def gen():
        try:
            if not _thread_exists(config):
                yield _sse({"type": "error", "message": f"thread {thread_id} 不存在"})
                return
            if not _is_paused(config):
                yield _sse({"type": "error", "message": f"thread {thread_id} 无可续跑步骤（已完成或非暂停态）"})
                return
            yield _sse({"type": "start", "thread_id": thread_id})
            # 续跑 prototype→三 accept→accept_gate
            yield from _stream_updates(None, config)
            yield _sse({"type": "done", "thread_id": thread_id})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
