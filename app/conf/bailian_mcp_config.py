# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


# 定义mcp的服务配置
@dataclass
class McpConfig:
    mcp_base_url: str
    api_key : str

mcp_config = McpConfig(
    mcp_base_url=os.getenv("MCP_DASHSCOPE_BASE_URL"),
    # 百炼MCP鉴权使用独立Key（.env中MCP_API_KEY），与LLM的DeepSeek Key分开，避免切换LLM后搜索鉴权失效
    api_key=os.getenv("MCP_API_KEY")
)
