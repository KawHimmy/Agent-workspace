from __future__ import annotations

import asyncio

from langchain_openai import ChatOpenAI

from .config import settings

_cached_model: ChatOpenAI | None = None


def get_model() -> ChatOpenAI | None:
    global _cached_model
    if not settings.glm_api_key:
        return None

    if _cached_model is None:
        _cached_model = ChatOpenAI(
            model=settings.glm_model,
            api_key=settings.glm_api_key,
            base_url=settings.glm_base_url,
            temperature=0.2,
        )
    return _cached_model


def _paper_excerpt(text: str) -> str:
    compact = text.strip()
    if len(compact) <= 26000:
        return compact

    # 标题、摘要通常在前部，实验和结论往往在末尾，截取两端最稳妥。
    return f"{compact[:19000]}\n\n[...content omitted for brevity...]\n\n{compact[-6000:]}"


async def summarize_text_with_llm(text: str) -> str | None:
    model = get_model()
    if not model:
        return None

    try:
        response = await asyncio.wait_for(
            model.ainvoke(
                [
                    (
                        "system",
                        "你是文档摘要助手。请用中文输出简洁摘要，包含："
                        "1. 一段总览；"
                        "2. 三条关键信息；"
                        "3. 这份文档最值得继续看的部分。"
                        "不要编造原文没有的信息。",
                    ),
                    ("human", text[:12000]),
                ]
            ),
            timeout=10,
        )
    except Exception:
        return None

    if isinstance(response.content, str):
        return response.content
    return str(response.content)


async def summarize_paper_with_llm(text: str) -> str | None:
    model = get_model()
    if not model:
        return None

    try:
        response = await asyncio.wait_for(
            model.ainvoke(
                [
                    (
                        "system",
                        "你是学术论文速读助手。请阅读论文内容，并用中文 Markdown 严格按以下结构输出：\n"
                        "## 标题\n"
                        "## 作者\n"
                        "## 一句话总结\n"
                        "## 论文要解决什么\n"
                        "## 方法亮点\n"
                        "## 实验里最重要的结论\n"
                        "## 这篇论文的价值与局限\n"
                        "## 建议重点看\n\n"
                        "要求：\n"
                        "- 面向读论文的人，不要写“Agent 可用信息”或系统提示语。\n"
                        "- “论文要解决什么 / 方法亮点 / 实验里最重要的结论 / 这篇论文的价值与局限 / 建议重点看”使用项目符号。\n"
                        "- 即使原文是英文，也要整理成自然中文。\n"
                        "- 不要泛泛而谈，尽量给出这篇论文真正值得看的信息。\n"
                        "- 信息不明确时写“未明确给出”，不要编造。",
                    ),
                    ("human", _paper_excerpt(text)),
                ]
            ),
            timeout=18,
        )
    except Exception:
        return None

    if isinstance(response.content, str):
        return response.content
    return str(response.content)
