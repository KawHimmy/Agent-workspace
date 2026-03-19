from __future__ import annotations
# 启用延迟解析类型注解。
# 好处是类型提示不会在定义函数/类时立刻求值，
# 对前向引用、更复杂的类型注解更友好。

import os
# 导入 os 模块，用于获取系统环境变量等功能。

from typing import Any
# 导入 Any 类型，表示“任意类型”。

from mcp import ClientSession
# 从 mcp 包里导入客户端会话类。
# 它用于和 MCP 服务端建立会话并进行通信。

from mcp.client.stdio import StdioServerParameters, stdio_client
# 导入：
# 1. StdioServerParameters：定义如何启动一个基于 stdio 的 MCP 服务端
# 2. stdio_client：用于启动服务端并建立标准输入/输出通信

from .config import settings
# 从当前包下的 config 模块中导入 settings 配置对象。
# 这里预计 settings 里至少有 root_dir 和 store_file 这两个配置项。


def _server_params() -> StdioServerParameters:
    # 定义一个辅助函数，用来生成 MCP 服务端的启动参数。
    # 返回值类型是 StdioServerParameters。

    return StdioServerParameters(
        command="node",
        # 启动命令是 node，说明服务端是一个 Node.js 程序。

        args=["packages/mcp-servers/src/document-server.js"],
        # 传给 node 的参数，即实际启动的脚本文件。
        # 最终效果相当于执行：
        # node packages/mcp-servers/src/document-server.js

        cwd=str(settings.root_dir),
        # 设置当前工作目录。
        # 因为脚本路径是相对路径，所以需要指定工作目录。

        env={
            **os.environ,
            # 继承当前进程的全部环境变量。

            "APP_STORE_FILE": str(settings.store_file),
            # 额外传入一个环境变量 APP_STORE_FILE，
            # 告诉 document-server.js 文档存储文件在哪里。
        },
    )


async def call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    # 定义一个异步函数，用于调用某个 MCP 工具。
    #
    # 参数：
    # - name: 工具名
    # - arguments: 传给工具的参数字典
    #
    # 返回值：
    # - 工具调用结果，类型不固定，所以写成 Any

    async with stdio_client(_server_params()) as (read_stream, write_stream):
        # 使用前面定义的服务端参数启动 MCP 服务端，
        # 并建立基于标准输入/输出的通信流。
        #
        # read_stream: 从服务端读取数据
        # write_stream: 向服务端写入数据

        async with ClientSession(read_stream, write_stream) as session:
            # 基于这两个通信流创建一个客户端会话 session。
            # session 就表示“这一次和 MCP 服务端之间的会话”。

            await session.initialize()
            # 初始化会话。
            # 这是正式调用工具前的握手/初始化步骤。

            return await session.call_tool(name, arguments)
            # 调用指定名称的 MCP 工具，并传入参数。
            # 等服务端执行完后，把结果返回。


async def list_mcp_tools() -> list[dict[str, Any]]:
    # 定义一个异步函数，用于列出 MCP 服务端当前提供的所有工具。
    #
    # 返回值是一个列表，列表中每一项都是字典，
    # 字典里包含工具的 JSON 格式信息。

    async with stdio_client(_server_params()) as (read_stream, write_stream):
        # 启动 MCP 服务端，并建立标准输入/输出通信流。

        async with ClientSession(read_stream, write_stream) as session:
            # 创建客户端会话对象 session，用于和服务端通信。

            await session.initialize()
            # 初始化会话，完成连接后的准备工作。

            result = await session.list_tools()
            # 向服务端请求“当前有哪些工具可用”。

            return [tool.model_dump(mode="json") for tool in result.tools]
            # 把服务端返回的每个工具对象转成 JSON 兼容的字典格式，
            # 最终返回一个字典列表。