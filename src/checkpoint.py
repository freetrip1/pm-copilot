"""LangGraph SqliteSaver 持久化 helper（M5：续跑而非重跑）。

两种用法各取所需：
  - CLI（main.py）：用 ``with open_checkpointer() as cp`` 上下文管理器，
    进程结束即关闭连接。底层走 SqliteSaver.from_conn_string(path)。
  - server（server.py）：进程内长生命周期单例，跨请求复用同一连接，
    必须 ``check_same_thread=False`` 才能让 uvicorn 工作线程安全访问
    （verify 阶段实测多线程 invoke 无 "SQLite objects created in a thread" 报错）。

DB 文件默认放在项目根目录（os.getcwd()）的 checkpoints.sqlite。
"""
import os
import sqlite3
from contextlib import contextmanager

from langgraph.checkpoint.sqlite import SqliteSaver


def default_db_path() -> str:
    """checkpoint DB 默认路径：项目根目录下 checkpoints.sqlite。"""
    return os.path.join(os.getcwd(), "checkpoints.sqlite")


@contextmanager
def open_checkpointer(db_path: str | None = None):
    """CLI 用：上下文管理器形式拿 SqliteSaver，退出时自动关连接。

    用法：
        with open_checkpointer() as cp:
            graph = build_resumable_graph(cp)
            graph.invoke(...)
    """
    path = db_path or default_db_path()
    with SqliteSaver.from_conn_string(path) as cp:
        yield cp


def make_app_checkpointer(db_path: str | None = None) -> SqliteSaver:
    """server 用：返回进程级长生命周期 SqliteSaver（连接 check_same_thread=False）。

    不走上下文管理器，连接随应用进程存活；由 uvicorn 多线程共享，
    因此显式 check_same_thread=False 以允许跨线程访问同一连接。
    """
    path = db_path or default_db_path()
    conn = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(conn)
