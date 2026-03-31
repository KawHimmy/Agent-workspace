from __future__ import annotations
# 让类型注解延迟解析：
# 1. 可以更方便地写现代类型标注，比如 dict[str, Any] | None
# 2. 避免某些类型在定义时还没准备好就被立刻求值

import asyncio
# asyncio：Python 异步编程标准库
# 这里主要用它的 Lock，避免多个协程同时读写同一个 JSON 文件造成数据覆盖

import json
# json：负责 Python 对象 和 JSON 字符串 之间的互相转换

import uuid
# uuid：用于生成全局唯一 ID，适合给会话、消息、任务等记录当主键

from copy import deepcopy
# deepcopy：深拷贝，避免直接复用 INITIAL_STORE 导致原始模板被修改

from datetime import datetime, timezone
# datetime / timezone：用于生成带时区的当前时间
# 这里统一使用 UTC 时间，便于后续排序和跨时区处理

from typing import Any
# Any：类型注解中表示“任意类型”

from .config import settings
# 从当前项目的 config 模块导入 settings
# 这里最关键的是 settings.store_file，它表示 JSON 存储文件的路径


# 整个 JSON 存储文件的初始结构
# 外层是一个字典，每个 key 对应一类“表”
# 每一类表的值都是一个列表，列表中每个元素都是一条记录（字典）
INITIAL_STORE: dict[str, list[dict[str, Any]]] = {
    "conversations": [],     # 会话列表
    "messages": [],          # 消息列表
    "agentRuns": [],         # 智能体运行记录
    "documents": [],         # 文档记录
    "backgroundJobs": [],    # 后台任务记录
    "userPreferences": [],   # 用户偏好设置
}

# 全局异步锁
# 用来保护对 store 文件的读写，避免多个协程同时操作文件引发数据竞争
_store_lock = asyncio.Lock()


def _now() -> str:
    """
    返回当前 UTC 时间的 ISO 格式字符串。
    例如：'2026-03-22T08:30:15.123456+00:00'
    """
    return datetime.now(timezone.utc).isoformat()


async def ensure_store() -> None:
    """
    确保存储文件存在。

    逻辑：
    1. 先确保 store 文件所在目录存在
    2. 如果 store 文件不存在，则创建一个初始 JSON 文件
    """
    # parent 取到文件所在目录
    # mkdir(parents=True, exist_ok=True) 表示：
    # - 如果父目录不存在，就连父目录一起创建
    # - 如果目录已经存在，不报错
    settings.store_file.parent.mkdir(parents=True, exist_ok=True)

    # 如果存储文件还不存在，则写入一个初始结构
    if not settings.store_file.exists():
        _write_store_unlocked(deepcopy(INITIAL_STORE))


def _normalize_json(raw: str) -> str:
    """
    清理读取出来的 JSON 文本，避免一些常见问题。

    处理内容：
    1. 去掉 UTF-8 BOM（某些 Windows 编辑器会自动在文件开头加这个）
    2. 去掉首尾空白字符

    如果不去掉 BOM，json.loads 可能会报错。
    """
    return raw.lstrip("\ufeff").strip()


def _write_store_unlocked(store: dict[str, list[dict[str, Any]]]) -> None:
    """
    底层写文件函数：把整个 store 写入 JSON 文件。

    注意：
    - 这个函数本身“不加锁”
    - 调用者必须自己保证调用时是安全的
    - 所以一般只在已经持有锁的上下文里调用
    """
    settings.store_file.write_text(
        # ensure_ascii=False：让中文直接写入，不转成 \\uXXXX
        # indent=2：格式化 JSON，缩进 2 个空格，便于人工查看
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def read_store() -> dict[str, list[dict[str, Any]]]:
    """
    读取整个 JSON 存储，并返回 Python 字典。

    具备以下保护逻辑：
    1. 自动确保文件存在
    2. 自动处理空文件
    3. 自动处理 BOM
    4. 如果 JSON 损坏，则重置为初始结构

    返回值始终是一个合法的 store 字典结构。
    """
    async with _store_lock:
        # 确保存储文件已经存在
        await ensure_store()

        # 读取原始文本
        raw = settings.store_file.read_text(encoding="utf-8")

        # 清理 BOM 和首尾空白
        normalized = _normalize_json(raw)

        # 如果文件是空的，直接重置为初始结构并返回
        if not normalized:
            _write_store_unlocked(deepcopy(INITIAL_STORE))
            return deepcopy(INITIAL_STORE)

        try:
            # 尝试把 JSON 字符串解析成 Python 字典
            return json.loads(normalized)
        except json.JSONDecodeError:
            # 如果文件内容不是合法 JSON，则直接重置
            # 这是一个“容错优先”的设计：宁可清空也不让程序崩掉
            _write_store_unlocked(deepcopy(INITIAL_STORE))
            return deepcopy(INITIAL_STORE)


async def write_store(store: dict[str, list[dict[str, Any]]]) -> None:
    """
    安全地把整个 store 写回 JSON 文件。

    和 _write_store_unlocked 的区别：
    - 这个函数会先加锁
    - 更适合作为对外统一写入入口
    """
    async with _store_lock:
        _write_store_unlocked(store)


def _sorted_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    按更新时间倒序排序记录。

    排序优先级：
    1. updatedAt
    2. createdAt
    3. 空字符串（兜底）

    reverse=True 表示降序，也就是“最新的排前面”。
    """
    return sorted(
        items,
        key=lambda item: item.get("updatedAt") or item.get("createdAt") or "",
        reverse=True,
    )


async def list_conversations_by_user(user_id: str) -> list[dict[str, Any]]:
    """
    获取某个用户的所有会话，并按最近更新时间倒序返回。
    """
    store = await read_store()

    # 只筛选出 userId 匹配的会话
    return _sorted_desc(
        [item for item in store["conversations"] if item["userId"] == user_id]
    )


async def create_conversation(user_id: str, title: str = "新的任务") -> dict[str, Any]:
    """
    创建一个新的会话记录。

    参数：
    - user_id：所属用户 ID
    - title：会话标题，默认是“新的任务”

    返回：
    - 新创建的会话字典
    """
    store = await read_store()
    now = _now()

    # 构造一条新的会话记录
    conversation = {
        "id": str(uuid.uuid4()),  # 会话唯一 ID
        "userId": user_id,        # 所属用户
        "title": title,           # 会话标题
        "createdAt": now,         # 创建时间
        "updatedAt": now,         # 更新时间
    }

    # 追加到 conversations 表
    store["conversations"].append(conversation)

    # 写回文件
    await write_store(store)

    return conversation


async def get_conversation_by_id(
    conversation_id: str, user_id: str
) -> dict[str, Any] | None:
    """
    根据会话 ID + 用户 ID 获取单个会话详情。

    额外返回：
    - 该会话下的 messages
    - 该会话下的 documents

    如果没找到，返回 None。
    """
    store = await read_store()

    # 只允许获取当前用户自己的会话，避免越权读取
    conversation = next(
        (
            item
            for item in store["conversations"]
            if item["id"] == conversation_id and item["userId"] == user_id
        ),
        None,
    )

    if not conversation:
        return None

    # 返回时，把原 conversation 展开，
    # 再附带该会话关联的 messages 和 documents
    return {
        **conversation,
        "messages": [
            item
            for item in store["messages"]
            if item["conversationId"] == conversation_id
        ],
        "documents": _sorted_desc(
            [
                item
                for item in store["documents"]
                if item["conversationId"] == conversation_id
                and item["userId"] == user_id
            ]
        ),
    }


async def append_message(
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    向某个会话中追加一条消息。

    参数：
    - conversation_id：所属会话 ID
    - user_id：所属用户 ID
    - role：消息角色，例如 'user' / 'assistant' / 'system'
    - content：消息文本内容
    - metadata：附加元数据，可选

    额外逻辑：
    - 会更新会话的 updatedAt
    - 如果是用户发的第一条有效消息，且会话标题还是默认“新的任务”，
      则自动把标题改成消息前 30 个字符
    """
    store = await read_store()
    now = _now()

    # 构造消息记录
    message = {
        "id": str(uuid.uuid4()),          # 消息唯一 ID
        "conversationId": conversation_id, # 所属会话
        "userId": user_id,                # 所属用户
        "role": role,                     # 角色
        "content": content,               # 文本内容
        "metadata": metadata or {},       # 没传 metadata 时默认空字典
        "createdAt": now,                 # 创建时间
        "updatedAt": now,                 # 更新时间
    }

    # 追加到 messages 表
    store["messages"].append(message)

    # 顺便更新对应会话的更新时间
    for conversation in store["conversations"]:
        if conversation["id"] == conversation_id:
            conversation["updatedAt"] = now

            # 如果满足以下条件，则自动用用户输入生成标题：
            # 1. 当前消息角色是 user
            # 2. 内容去掉空白后不为空
            # 3. 当前标题还是默认“新的任务”
            if role == "user" and content.strip() and conversation["title"] == "新的任务":
                conversation["title"] = content[:30]
            break

    await write_store(store)
    return message


async def create_agent_run(
    conversation_id: str, user_id: str, prompt: str
) -> dict[str, Any]:
    """
    创建一条智能体执行记录（agent run）。

    用于记录一次 agent/助手任务的运行过程。
    """
    store = await read_store()
    now = _now()

    run = {
        "id": str(uuid.uuid4()),   # 运行记录 ID
        "conversationId": conversation_id,  # 所属会话
        "userId": user_id,         # 所属用户
        "prompt": prompt,          # 本次运行对应的 prompt
        "status": "running",       # 初始状态：运行中
        "toolCalls": [],           # 运行期间调用过的工具列表
        "memoryContext": "",       # 运行时记忆上下文
        "result": "",              # 最终结果
        "createdAt": now,
        "updatedAt": now,
    }

    store["agentRuns"].append(run)
    await write_store(store)
    return run


async def update_agent_run(run_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """
    更新一条智能体运行记录。

    参数：
    - run_id：要更新的运行记录 ID
    - updates：要合并进去的字段字典

    返回：
    - 更新后的 run
    - 如果找不到则返回 None
    """
    store = await read_store()

    # 找到目标 run
    run = next((item for item in store["agentRuns"] if item["id"] == run_id), None)
    if not run:
        return None

    # 合并更新字段
    run.update(updates)

    # 刷新更新时间
    run["updatedAt"] = _now()

    await write_store(store)
    return run


async def create_document(record: dict[str, Any]) -> dict[str, Any]:
    """
    创建一条文档记录。

    record 参数允许调用方传入额外字段，例如：
    - userId
    - conversationId
    - filename
    - mimeType
    - path
    等等

    注意：
    **record 放在最后展开，因此如果 record 中包含同名字段，
    会覆盖这里提供的默认值。**
    """
    store = await read_store()
    now = _now()

    document = {
        "id": str(uuid.uuid4()),   # 文档 ID
        "status": "queued",        # 默认状态：排队中
        "summary": "",             # 文档摘要
        "extractedText": "",       # 文档提取文本
        "createdAt": now,
        "updatedAt": now,
        **record,                  # 调用方传入的字段覆盖默认值
    }

    store["documents"].append(document)
    await write_store(store)
    return document


async def update_document(
    document_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    """
    更新一条文档记录。

    找不到对应 document 时返回 None。
    """
    store = await read_store()

    document = next(
        (item for item in store["documents"] if item["id"] == document_id), None
    )
    if not document:
        return None

    # 更新字段
    document.update(updates)

    # 刷新更新时间
    document["updatedAt"] = _now()

    await write_store(store)
    return document


async def get_document_by_id(
    document_id: str, user_id: str
) -> dict[str, Any] | None:
    """
    根据 document_id + user_id 获取某个文档。

    只允许读取当前用户自己的文档。
    """
    store = await read_store()

    return next(
        (
            item
            for item in store["documents"]
            if item["id"] == document_id and item["userId"] == user_id
        ),
        None,
    )


async def list_documents_by_user(user_id: str) -> list[dict[str, Any]]:
    """
    获取某个用户的全部文档，并按时间倒序返回。
    """
    store = await read_store()
    return _sorted_desc(
        [item for item in store["documents"] if item["userId"] == user_id]
    )


async def create_background_job(record: dict[str, Any]) -> dict[str, Any]:
    """
    创建一条后台任务记录。

    record 可由调用方传入额外字段，例如：
    - userId
    - type
    - payload
    - conversationId
    等
    """
    store = await read_store()
    now = _now()

    job = {
        "id": str(uuid.uuid4()),   # 任务 ID
        "status": "queued",        # 默认状态：排队中
        "output": None,            # 任务输出
        "error": None,             # 错误信息
        "createdAt": now,
        "updatedAt": now,
        **record,                  # 调用方字段覆盖默认值
    }

    store["backgroundJobs"].append(job)
    await write_store(store)
    return job


async def update_background_job(
    job_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    """
    更新一条后台任务记录。

    找不到时返回 None。
    """
    store = await read_store()

    job = next(
        (item for item in store["backgroundJobs"] if item["id"] == job_id), None
    )
    if not job:
        return None

    # 合并更新字段
    job.update(updates)

    # 更新时间
    job["updatedAt"] = _now()

    await write_store(store)
    return job


async def list_background_jobs_by_user(user_id: str) -> list[dict[str, Any]]:
    """
    获取某个用户的所有后台任务，并按时间倒序返回。
    """
    store = await read_store()
    return _sorted_desc(
        [item for item in store["backgroundJobs"] if item["userId"] == user_id]
    )


async def upsert_preference(
    user_id: str, key: str, value: str, source: str = "app"
) -> None:
    """
    新增或更新用户偏好（upsert = update + insert）。

    逻辑：
    - 如果该用户已存在相同 key 的偏好，则更新 value/source/updatedAt
    - 如果不存在，则新建一条偏好记录

    参数：
    - user_id：用户 ID
    - key：偏好项名称，例如 'theme'
    - value：偏好值，例如 'dark'
    - source：来源，默认 'app'
    """
    store = await read_store()
    now = _now()

    # 查找该用户是否已经存在相同 key 的偏好记录
    existing = next(
        (
            item
            for item in store["userPreferences"]
            if item["userId"] == user_id and item["key"] == key
        ),
        None,
    )

    if existing:
        # 已存在：更新值
        existing.update({"value": value, "source": source, "updatedAt": now})
    else:
        # 不存在：新增一条记录
        store["userPreferences"].append(
            {
                "id": str(uuid.uuid4()),
                "userId": user_id,
                "key": key,
                "value": value,
                "source": source,
                "createdAt": now,
                "updatedAt": now,
            }
        )

    await write_store(store)


async def list_preferences_by_user(user_id: str) -> list[dict[str, Any]]:
    """
    获取某个用户的全部偏好设置，并按时间倒序返回。
    """
    store = await read_store()
    return _sorted_desc(
        [item for item in store["userPreferences"] if item["userId"] == user_id]
    )