"""GLM (Z.AI OpenAI 兼容接口) Chat 模型接入。复用 hotel-rag-agent/src/llm.py 模式。

Z.AI 提供 OpenAI 兼容端点，因此直接用 langchain_openai.ChatOpenAI 指过去即可。
"""
import os
from functools import lru_cache

from langchain_openai import ChatOpenAI

ZAI_BASE_URL = os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
GLM_CHAT_MODEL = os.getenv("GLM_CHAT_MODEL", "glm-5.2")


@lru_cache(maxsize=8)
def get_chat_llm(temperature: float = 0.3) -> ChatOpenAI:
    """返回一个指向 Z.AI GLM 的 ChatOpenAI 实例（按温度缓存复用）。"""
    return ChatOpenAI(
        model=GLM_CHAT_MODEL,
        temperature=temperature,
        openai_api_base=ZAI_BASE_URL,
        openai_api_key=os.environ.get("ZAI_API_KEY", ""),
    )


def chat(system: str, user: str, temperature: float = 0.3) -> str:
    """一次性 system+user 调用，返回文本内容。"""
    llm = get_chat_llm(temperature)
    resp = llm.invoke([("system", system), ("human", user)])
    return resp.content if isinstance(resp.content, str) else str(resp.content)
