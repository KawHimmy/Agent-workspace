from __future__ import annotations
# 作用：
# 让类型注解延迟解析。
# 好处是：
# 1. 可以更灵活地写类型提示
# 2. 避免某些类型在运行时还没定义就报错
# 3. 在较新的 Python 类型写法里更方便，比如 str | None、list[Path]

import os
# 用来读取环境变量，比如 os.getenv("PORT")

import re
# 正则表达式模块，用来在文本里搜索密钥模式

from dataclasses import dataclass
# dataclass 用来快速定义“只存数据”的类，省去手写 __init__ 等代码

from pathlib import Path
# Path 比传统字符串路径更清晰，适合拼接目录、判断文件是否存在等

from dotenv import load_dotenv
# 从 .env 文件中加载环境变量


load_dotenv()
# 执行后会读取项目中的 .env 文件，
# 把里面的键值对加载到当前进程环境变量中。
# 例如 .env 里有：
# PORT=3000
# GLM_API_KEY=xxx
# 那么后面 os.getenv("PORT") 就能读到它。


# __file__ 表示当前文件自己的路径
# resolve() 会得到绝对路径
# parents[2] 表示往上找第 3 层父目录（索引从 0 开始）
# 这里通常是为了拿到项目根目录
ROOT_DIR = Path(__file__).resolve().parents[2]

# 项目中的 data 目录
DATA_DIR = ROOT_DIR / "data"

# 上传文件存储目录
UPLOADS_DIR = ROOT_DIR / "storage" / "uploads"

# 应用状态存储文件
STORE_FILE = DATA_DIR / "app-store.json"

# Web 静态资源目录
WEB_DIR = ROOT_DIR / "apps" / "web" / "static"

# 认证服务地址：
# 优先从环境变量 AUTH_SERVICE_URL 读取
# 如果没有配置，就默认使用本机 3001 端口
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:3001")


# 允许扫描的“文本文件”后缀集合
# 后面在自动发现密钥时，只会尝试读取这些文本类型文件
TEXT_EXTENSIONS = {
    ".env",
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".js",
    ".ts",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".html",
    ".css",
}


def _walk_text_files(start_dir: Path, depth: int = 0) -> list[Path]:
    """
    递归扫描目录，找出其中的文本文件。

    参数：
    - start_dir: 起始目录
    - depth: 当前递归深度，默认从 0 开始

    返回：
    - 一个 Path 列表，里面是找到的文本文件路径

    设计细节：
    - 如果目录不存在，直接返回空列表
    - 最大递归深度限制为 4，防止扫描过深导致性能问题
    """
    if not start_dir.exists() or depth > 4:
        return []

    results: list[Path] = []

    # 遍历当前目录下的所有文件和文件夹
    for entry in start_dir.iterdir():
        if entry.is_dir():
            # 如果是文件夹，就继续递归向下扫描
            results.extend(_walk_text_files(entry, depth + 1))
        elif entry.suffix.lower() in TEXT_EXTENSIONS or entry.name == ".env":
            # 如果是普通文件，并且后缀属于文本类型，就加入结果
            # 这里额外判断 entry.name == ".env"，是为了确保 .env 文件一定被识别
            results.append(entry)

    return results


def _discover_secret(pattern: str, directories: list[Path]) -> str | None:
    """
    在若干目录中递归扫描文本文件，按给定正则表达式搜索“密钥”。

    参数：
    - pattern: 正则表达式字符串，用来描述目标密钥格式
    - directories: 需要扫描的目录列表

    返回：
    - 找到时，返回第一个匹配到的字符串
    - 没找到时，返回 None

    处理逻辑：
    1. 遍历所有候选目录
    2. 递归找出其中所有文本文件
    3. 优先按 utf-8 读取文件
    4. 如果 utf-8 解码失败，再尝试 gbk
    5. 对文件内容做正则搜索
    6. 找到第一个匹配项就立即返回
    """
    regex = re.compile(pattern)

    for directory in directories:
        for file_path in _walk_text_files(directory):
            try:
                # 优先尝试用 utf-8 编码读取文本文件
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # 如果 utf-8 解码失败，可能是中文 Windows 常见的 gbk 编码
                try:
                    content = file_path.read_text(encoding="gbk")
                except Exception:
                    # 仍然失败就跳过这个文件
                    continue
            except Exception:
                # 其他任何读取异常也直接跳过
                continue

            # 在文件内容中搜索是否存在符合正则模式的字符串
            match = regex.search(content)
            if match:
                # 只返回第一个匹配到的完整字符串
                return match.group(0)

    # 全部扫描完还没找到，则返回 None
    return None


def _find_candidate_directories(*keywords: str) -> list[Path]:
    """
    根据关键字，在 ROOT_DIR 的一级子目录中找“候选目录”。

    参数：
    - *keywords: 可变参数，表示若干关键字，例如 "langgraph", "mem0"

    返回：
    - 目录名中包含任意关键字的目录列表

    例如：
    如果 ROOT_DIR 下有：
    - langgraph-demo
    - mem0-service
    - docs
    传入 keywords=("langgraph", "mem0")
    则返回前两个目录
    """
    keywords_lower = [keyword.lower() for keyword in keywords]
    candidates: list[Path] = []

    # 遍历项目根目录的一级内容
    # 这里只检查一级目录，不会继续向下递归
    for entry in ROOT_DIR.iterdir():
        if not entry.is_dir():
            continue

        # 转成小写，做大小写不敏感匹配
        normalized_name = entry.name.lower()

        # 如果目录名中包含任意一个关键字，就认为它是候选目录
        if any(keyword in normalized_name for keyword in keywords_lower):
            candidates.append(entry)

    return candidates


def _discover_glm_key() -> str | None:
    """
    自动发现 GLM API Key。

    搜索策略：
    1. 先找目录名中包含 "langgraph" 或 "mem0" 的目录
    2. 在这些目录下的文本文件中搜索符合 GLM key 格式的字符串

    正则：
    \\b[a-f0-9]{32}\\.[A-Za-z0-9]+\\b

    大致匹配类似这种格式：
    xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.yyyyyyyy
    前半部分是 32 位十六进制字符
    后半部分是点号后的一串字母数字
    """
    candidates = _find_candidate_directories("langgraph", "mem0")
    return _discover_secret(r"\b[a-f0-9]{32}\.[A-Za-z0-9]+\b", candidates)


def _discover_mem0_key() -> str | None:
    """
    自动发现 Mem0 API Key。

    搜索策略：
    1. 找目录名中包含 "mem0" 的目录
    2. 在这些目录下的文本文件中搜索符合 Mem0 key 格式的字符串

    正则：
    \\bm0-[A-Za-z0-9]+\\b

    大致匹配类似：
    m0-abc123xyz
    """
    return _discover_secret(r"\bm0-[A-Za-z0-9]+\b", _find_candidate_directories("mem0"))


@dataclass(frozen=True)
class Settings:
    """
    项目配置类。

    使用 dataclass 的好处：
    - 自动生成初始化方法
    - 结构清晰，适合集中管理配置项

    frozen=True 表示“冻结”：
    - 实例创建后，属性不能再被修改
    - 相当于只读配置，更安全
    """

    # 服务启动端口
    # 优先读取环境变量 PORT，否则默认 3000
    # os.getenv 返回字符串，所以这里再转成 int
    port: int = int(os.getenv("PORT", "3000"))

    # 应用对外访问地址
    # 优先读取 APP_URL，否则默认 http://localhost:3000
    app_url: str = os.getenv("APP_URL", "http://localhost:3000")

    # 认证服务地址
    # 前面已经统一算过 AUTH_SERVICE_URL，这里直接复用
    auth_service_url: str = AUTH_SERVICE_URL

    # 内部服务通信使用的密钥
    # 如果没配置，就使用开发环境默认值
    # 注意：这个默认值通常只适合本地开发，不适合生产环境
    internal_service_secret: str = os.getenv("INTERNAL_SERVICE_SECRET", "dev-internal-secret")

    # 默认使用的 GLM 模型名
    glm_model: str = os.getenv("GLM_MODEL", "glm-4.7")

    # GLM 服务的基础接口地址
    glm_base_url: str = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")

    # GLM API Key 的获取优先级：
    # 1. 优先使用环境变量 GLM_API_KEY
    # 2. 如果环境变量没有，再调用 _discover_glm_key() 自动扫描项目目录查找
    # 3. 如果还是没找到，则为 None
    glm_api_key: str | None = os.getenv("GLM_API_KEY") or _discover_glm_key()

    # Mem0 API Key 的获取优先级：
    # 1. 优先使用环境变量 MEM0_API_KEY
    # 2. 如果环境变量没有，再调用 _discover_mem0_key() 自动扫描查找
    # 3. 如果没找到，则为 None
    mem0_api_key: str | None = os.getenv("MEM0_API_KEY") or _discover_mem0_key()

    # 应用状态存储文件路径
    store_file: Path = STORE_FILE

    # 上传目录路径
    uploads_dir: Path = UPLOADS_DIR

    # Web 静态资源目录路径
    web_dir: Path = WEB_DIR

    # 项目根目录路径
    root_dir: Path = ROOT_DIR


# 创建一个全局配置对象
# 项目中其他文件可以直接：
# from .config import settings
# 然后通过 settings.port、settings.root_dir 等方式访问配置
settings = Settings()