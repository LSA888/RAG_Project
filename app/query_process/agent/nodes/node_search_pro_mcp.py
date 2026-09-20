import sys
import json
import asyncio
from app.utils.task_utils import add_done_task, add_running_task
from app.conf.bailian_mcp_config import mcp_config
from app.core.logger import logger

async def mcp_call(query):
    """
    异步调用百炼 MCP 联网搜索增强版（EnhancedSearch）的核心函数。
    
    - 协议：Streamable HTTP（百炼 MCP 已从旧 SSE 协议升级）
    - 端点：由 .env 中 MCP_DASHSCOPE_BASE_URL 决定
    - 工具名：search_pro（EnhancedSearch 服务的官方工具名）
    - 鉴权：Bearer + Model Studio API Key
    
    :param query: 搜索查询词（通常是经过改写后的精准Query）
    :return: MCP返回的原始结果对象 (包含 content, isError 等字段)
    """
    
    from agents.mcp import MCPServerStreamableHttp
    auth_header = f"Bearer {mcp_config.api_key}"
    search_mcp = MCPServerStreamableHttp(
        name="search_mcp",
        params={
            "url": mcp_config.mcp_base_url,
            "headers": {"Authorization": auth_header},
            "timeout": 300,
            "sse_read_timeout": 300
        }
    )

    try:
        logger.info(f"[MCP] 正在连接百炼 EnhancedSearch 服务: {mcp_config.mcp_base_url}")
        await search_mcp.connect()
        
        logger.info(f"[MCP] 连接成功，正在调用工具 'search_pro' 查询: {query[:50]}...")
        # EnhancedSearch 服务的工具名是 search_pro（不是旧 WebSearch 的 bailian_web_search）
        result = await search_mcp.call_tool(
            tool_name="search_pro", 
            # EnhancedSearch 的 search_pro 只支持 query 一个参数，不接受 count
            arguments={"query": query}
        )
        logger.info("[MCP] 工具调用完成，已获取返回结果")
        return result
        
    except Exception as e:
        # TaskGroup 异常只是外壳，真正原因藏在 sub-exception 里
        logger.error(f"[MCP] 调用过程中发生异常: {type(e).__name__}: {e}")
        # 展开 ExceptionGroup / TaskGroup 内部的真实异常
        # 注意：不能用 exc_info=sub 传给 loguru，因为 httpx.HTTPStatusError
        # 需要 request/response 两个 keyword-only 参数才能 pickle，多进程队列会报错
        if hasattr(e, 'exceptions'):
            for i, sub in enumerate(e.exceptions):
                logger.error(f"[MCP] sub-exception[{i}]: {type(sub).__name__}: {sub}")
                # 如果是 HTTPStatusError，额外打出 status_code 和 response body
                sub_str = str(sub)
                if '401' in sub_str:
                    logger.error("[MCP] → 鉴权失败(401)：检查 .env 中 MCP_API_KEY 是否为百炼 Key（不是 DeepSeek 的）")
                elif '404' in sub_str:
                    logger.error("[MCP] → URL 不存在(404)：检查 MCP_DASHSCOPE_BASE_URL 路径是否正确")
                elif '403' in sub_str:
                    logger.error("[MCP] → 禁止访问(403)：Key 可能无权限或额度不足")
                elif '429' in sub_str:
                    logger.error("[MCP] → 限流(429)：百炼 MCP 调用过于频繁")
        if hasattr(e, '__cause__') and e.__cause__:
            logger.error(f"[MCP] __cause__: {type(e.__cause__).__name__}: {e.__cause__}")
        # 打印当前 MCP 配置方便排查
        api_key_display = ('***' + mcp_config.api_key[-4:]) if (mcp_config.api_key and len(mcp_config.api_key) >= 4) else '(未配置)'
        logger.error(f"[MCP] 当前配置 url={mcp_config.mcp_base_url}, api_key={api_key_display}")
        return None
        
    finally:
        # 无论调用成功/失败，最终都关闭MCP连接（释放资源，异步方法）
        await search_mcp.cleanup()


def node_web_search_mcp(state):
    """
    LangGraph同步节点函数：处理MCP搜索逻辑，作为整个搜索流程的入口。
    
    该节点会调用 mcp_call 异步函数获取搜索结果，并将其解析为结构化数据存储到 state 中。
    
    :param state: LangGraph的全局状态对象，包含 session_id, rewritten_query 等信息
    :return: 字典，包含结构化的搜索结果 web_search_docs，供后续节点使用
    """
    logger.info("---node_web_search_mcp 开始处理---")
    
    # 1. 标记任务开始
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    # 2. 获取查询词
    query = state.get("rewritten_query", "")
    if not query:
        # 尝试回退到原始查询
        query = state.get("original_query", "")
        
    docs = []
    
    # 3. 执行搜索
    if query:
        try:
            # 同步-异步桥接：通过asyncio.run()执行异步的mcp_call函数
            logger.info(f"启动异步 MCP 调用，Query: {query}")
            
            # ======================================================================
            # MCP 返回结果格式解析说明
            # ----------------------------------------------------------------------
            # result 是一个 CallToolResult 对象 (定义在 agents.mcp.types 中)
            # result.content 是一个 TextContent 对象的列表，通常只有一项
            # result.content[0].text 是一个 JSON 字符串，包含实际的搜索结果
            #
            # 示例数据结构：
            # result.content[0].text = """
            # {
            #   "pages": [
            #     {
            #       "title": "HAK 180 烫金机使用手册",
            #       "url": "http://example.com/manual",
            #       "snippet": "在出厂默认状态下，若想设置局部转印..."
            #     },
            #     ...
            #   ]
            # }
            # """
            # ======================================================================
            result = asyncio.run(mcp_call(query))
            
            # 4. 解析结果
            if result and not result.isError and result.content:
                # 解析MCP原始结果：提取文本内容并转为JSON对象
                # result.content 通常是一个列表，第一项包含文本结果
                raw_text = result.content[0].text
                try:
                    data = json.loads(raw_text)
                    pages = data.get("pages") or []
                    
                    logger.info(f"MCP 返回原始页面数量: {len(pages)}")
                    
                    # 遍历结果，统一封装为结构化格式
                    for item in pages:
                        snippet = (item.get("snippet") or "").strip()
                        url = (item.get("url") or "").strip()
                        title = (item.get("title") or "").strip()
                        
                        # 过滤无核心摘要的结果
                        if not snippet:
                            continue
                            
                        docs.append({"title": title, "url": url, "snippet": snippet})
                        
                except json.JSONDecodeError:
                    logger.error(f"MCP 返回结果解析 JSON 失败: {raw_text[:100]}...")
            else:
                if result and result.isError:
                    logger.error(f"MCP 返回错误: {result}")
                else:
                    logger.warning("MCP 返回结果为空或无效")

            logger.info(f"结构化搜索结果数量: {len(docs)}")
            
        except Exception as e:
            logger.error(f"MCP 搜索节点执行异常: {e}", exc_info=True)
    else:
        logger.warning("查询词为空，跳过 MCP 搜索")

    # 5. 标记任务结束
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))
    
    logger.info("---node_web_search_mcp 处理结束---")
    
    # 若有有效搜索结果，返回结果供后续节点使用；无则返回空字典
    if docs:
        return {"web_search_docs": docs}
    return {}


if __name__ == '__main__':
    # 测试代码：单独运行该文件时，验证MCP搜索功能是否正常
    print("\n" + "="*50)
    print(">>> 启动 node_web_search_mcp 本地测试")
    print("="*50)
    
    test_state = {
        "session_id": "test_mcp_session",
        "rewritten_query": "HAK 180 在出厂默认状态下，若想在纸张上只把烫金膜转印到顶部 50 mm–170 mm 的局部区域，应在操作面板上如何设置",
        "is_stream": False
    }

    try:
        # 调用MCP搜索节点函数，执行测试
        result_state = node_web_search_mcp(test_state)

        print("\n" + "="*50)
        print(">>> 测试结果摘要:")
        search_results = result_state.get('web_search_docs', [])
        print(f"搜索结果数量: {len(search_results)}")
        if search_results:
            print("首条结果预览:")
            print(json.dumps(search_results[0], indent=2, ensure_ascii=False))
        else:
            print("未获取到搜索结果")
        print("="*50)
        
    except Exception as e:
        logger.exception(f"测试运行期间发生未捕获异常: {e}")
