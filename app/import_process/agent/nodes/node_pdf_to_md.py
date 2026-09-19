# ========== 文件最顶部：Windows控制台编码修复，解决PDF特殊字符日志输出报错 ==========
import os
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
os.environ["PYTHONUTF8"] = "1"

import time
import requests
import zipfile
import shutil
from pathlib import Path

# 新增PDF分片依赖
import pdfplumber
from PyPDF2 import PdfReader, PdfWriter

# 项目内部库
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.utils.format_utils import format_state
from app.utils.task_utils import add_running_task, add_done_task
from app.conf.mineru_config import mineru_config
from app.core.logger import logger

# MinerU配置（缓存配置信息）
MINERU_BASE_URL = mineru_config.base_url
MINERU_API_TOKEN = mineru_config.api_key

# 业务常量：MinerU单文件最大200页，分片取190留余量
MAX_SINGLE_PDF_PAGE = 200
CHUNK_PAGE_SIZE = 190


def split_pdf_into_chunks(pdf_path_obj: Path, out_dir: Path) -> list[Path]:
    """
    本地拆分大PDF，每块最多CHUNK_PAGE_SIZE页
    :param pdf_path_obj: 原始PDF路径
    :param out_dir: 分片PDF输出目录
    :return: 分片后的子PDF Path对象列表
    """
    log_prefix = "[split_pdf_into_chunks] "
    reader = None
    try:
        reader = PdfReader(pdf_path_obj)
        total_pages = len(reader.pages)
        chunk_files = []

        # 创建分片临时子目录
        chunk_dir = out_dir / "pdf_chunks"
        if chunk_dir.exists():
            shutil.rmtree(chunk_dir)
        chunk_dir.mkdir(parents=True, exist_ok=True)

        for start_idx in range(0, total_pages, CHUNK_PAGE_SIZE):
            end_idx = min(start_idx + CHUNK_PAGE_SIZE, total_pages)
            writer = PdfWriter()
            for page_no in range(start_idx, end_idx):
                writer.add_page(reader.pages[page_no])
            chunk_filename = f"{pdf_path_obj.stem}_part_{start_idx + 1}_{end_idx}.pdf"
            chunk_path = chunk_dir / chunk_filename
            with open(chunk_path, "wb") as f_out:
                writer.write(f_out)
            chunk_files.append(chunk_path)
            logger.info(f"{log_prefix}生成分片文件：{chunk_filename}，页码范围 {start_idx+1} ~ {end_idx}")

        logger.info(f"{log_prefix}PDF拆分完成，共生成 {len(chunk_files)} 个分片文件")
        return chunk_files
    finally:
        # 手动释放，防止句柄泄露
        if reader is not None and hasattr(reader, "stream"):
            reader.stream.close()



def parse_single_pdf(pdf_path_obj: Path, output_dir_obj: Path) -> str:
    """
    封装【单个PDF完整解析流程】：上传‑轮询‑下载解压‑返回md绝对路径
    复用原有step2、step3逻辑，分片循环时调用本函数
    """
    zip_url = step_2_upload_and_poll(pdf_path_obj, output_dir_obj)
    md_path = step_3_download_and_extract(zip_url, output_dir_obj, pdf_path_obj.stem)
    return md_path


def step_1_validate_paths(state):
    """
    步骤1：校验PDF文件路径和输出目录
    核心职责：参数非空校验 | PDF文件有效性校验 | 输出目录自动创建
    返回：合法的PDF文件Path对象、输出目录Path对象、pdf总页数
    异常：ValueError(参数缺失)、FileNotFoundError(文件无效)
    """
    log_prefix = "[step_1_validate_paths] "
    pdf_path = state.get("pdf_path", "").strip()
    local_dir = state.get("local_dir", "").strip()

    # 参数非空校验
    if not pdf_path:
        raise ValueError(f"{log_prefix}工作流状态缺失有效参数：pdf_path，当前值：{repr(pdf_path)}")
    if not local_dir:
        raise ValueError(f"{log_prefix}工作流状态缺失有效参数：local_dir，当前值：{repr(local_dir)}")

    # 转换为Path对象统一处理路径
    pdf_path_obj = Path(pdf_path)
    output_dir_obj = Path(local_dir)

    # PDF文件有效性校验（存在且为文件，非目录）
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"{log_prefix}PDF文件不存在，绝对路径：{pdf_path_obj.absolute()}")
    if not pdf_path_obj.is_file():
        raise FileNotFoundError(f"{log_prefix}指定路径非文件（是目录），绝对路径：{pdf_path_obj.absolute()}")

    # 确保输出目录存在，不存在则递归创建
    if not output_dir_obj.exists():
        logger.info(f"{log_prefix}输出目录不存在，自动创建：{output_dir_obj.absolute()}")
        output_dir_obj.mkdir(parents=True, exist_ok=True)

    # 读取PDF总页数
    try:
        with pdfplumber.open(pdf_path_obj) as pdf_reader:
            total_pdf_pages = len(pdf_reader.pages)
        logger.info(f"{log_prefix}PDF总页数：{total_pdf_pages}")
    except Exception as e:
        logger.warning(f"{log_prefix}读取PDF页数失败，设置总页数为0，不做分片：{str(e)}")
        total_pdf_pages = 0

    return pdf_path_obj, output_dir_obj, total_pdf_pages


def step_2_upload_and_poll(pdf_path_obj: Path, output_dir_obj: Path):
    """
    步骤2：上传PDF至MinerU并轮询解析任务状态
    核心流程：配置校验 → 获取上传链接 → 文件上传（含重试） → 任务轮询（直至完成/失败/超时）
    参数：pdf_path_obj‑已校验的PDF Path对象；output_dir_obj‑输出目录Path对象
    返回：解析结果ZIP包下载链接full_zip_url
    异常：ValueError(配置缺失)、RuntimeError(请求/上传失败)、TimeoutError(任务超时)
    """
    # 前置配置校验，拦截无效配置
    if not MINERU_BASE_URL or not MINERU_API_TOKEN:
        raise ValueError("MinerU配置缺失：请在.env中正确配置MINERU_BASE_URL和MINERU_API_TOKEN")
    logger.info(f"[配置校验] MinerU基础配置加载成功，开始处理文件：{pdf_path_obj.name}")

    # 构造请求头（符合HTTP规范，Bearer鉴权，全部改为半角横杠）
    request_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MINERU_API_TOKEN}"
    }

    # 1. 调用批量接口，获取上传Signed URL和任务batch_id
    url_get_upload = f"{MINERU_BASE_URL}/file-urls/batch"
    req_data = {
        "files": [{"name": pdf_path_obj.name}],
        "model_version": "vlm"  # 官方推荐解析模型
    }
    logger.debug(f"[获取上传链接] 调用接口：{url_get_upload}，请求参数：{req_data}")
    resp = requests.post(url=url_get_upload, headers=request_headers, json=req_data, timeout=30)

    # 响应校验：先验HTTP状态，再验业务返回码
    if resp.status_code != 200:
        raise RuntimeError(f"[获取上传链接] 网络请求失败，状态码：{resp.status_code}，响应内容：{resp.text}")

    resp_data = resp.json()
    if resp_data["code"] != 0:
        raise RuntimeError(f"[获取上传链接] API业务错误，返回数据：{resp_data}")

    # 提取核心数据：上传链接和任务唯一标识
    signed_url = resp_data["data"]["file_urls"][0]
    batch_id = resp_data["data"]["batch_id"]
    logger.info(f"[获取上传链接] 成功，batch_id：{batch_id}，上传链接已生成")

    # 2. 读取PDF二进制数据，准备上传
    logger.info(f"[文件上传] 开始读取PDF文件：{pdf_path_obj.name}")
    with open(pdf_path_obj, "rb") as f:
        file_data = f.read()

    # 创建Session（复用TCP连接，禁用代理避免签名验证失败）
    upload_session = requests.Session()
    upload_session.trust_env = False

    try:
        # 首次上传：自动识别文件类型
        put_resp = upload_session.put(url=signed_url, data=file_data, timeout=60)
        # 重试逻辑：首次失败则强制指定PDF的Content‑Type
        if put_resp.status_code != 200:
            logger.warning(f"[文件上传] 首次上传失败（状态码：{put_resp.status_code}），强制指定PDF类型重试")
            pdf_headers = {"Content-Type": "application/pdf"}
            put_resp = upload_session.put(url=signed_url, data=file_data, headers=pdf_headers, timeout=60)
            # 重试仍失败则抛出异常
            if put_resp.status_code != 200:
                raise RuntimeError(f"[文件上传] 重试后仍失败，状态码：{put_resp.status_code}，响应内容：{put_resp.text}")
        logger.info(f"[文件上传] 成功，文件{pdf_path_obj.name}已存入云存储")
    except Exception as e:
        raise RuntimeError(f"[文件上传] 网络异常导致上传失败，错误信息：{str(e)}")
    finally:
        # 无论成败，关闭Session释放网络连接，避免资源泄漏
        upload_session.close()

    # 3. 根据batch_id轮询任务状态，直至完成/失败/超时
    poll_url = f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}"
    start_time = time.time()
    timeout_seconds = 600  # 最大超时时间10分钟
    poll_interval = 3  # 轮询间隔3秒（平衡查询频率和服务端压力）
    logger.info(f"[任务轮询] 开始监控任务状态，batch_id：{batch_id}，最大超时：{timeout_seconds}s")

    while True:
        # 超时检查：超过最大时间直接终止轮询
        elapsed_time = time.time() - start_time
        if elapsed_time > timeout_seconds:
            raise TimeoutError(f"[任务轮询] 超时！任务处理超{int(timeout_seconds)}秒，batch_id：{batch_id}")

        # 发起轮询请求，短超时10秒，异常则重试
        try:
            poll_resp = requests.get(url=poll_url, headers=request_headers, timeout=10)
        except Exception as e:
            logger.warning(f"[任务轮询] 网络请求异常，{poll_interval}秒后重试：{str(e)}")
            time.sleep(poll_interval)
            continue

        # 处理HTTP响应错误：5xx服务端繁忙则重试，其他错误直接抛出
        if poll_resp.status_code != 200:
            if 500 <= poll_resp.status_code < 600:
                logger.warning(f"[任务轮询] 服务端繁忙（状态码：{poll_resp.status_code}），{poll_interval}秒后重试")
                time.sleep(poll_interval)
                continue
            else:
                raise RuntimeError(f"[任务轮询] HTTP请求失败，状态码：{poll_resp.status_code}，响应内容：{poll_resp.text}")

        # 解析轮询结果，校验业务状态
        poll_data = poll_resp.json()
        if poll_data["code"] != 0:
            raise RuntimeError(f"[任务轮询] API业务错误，返回数据：{poll_data}")

        extract_results = poll_data["data"]["extract_result"]
        # 结果暂空，继续轮询
        if not extract_results:
            logger.debug(f"[任务轮询] 结果暂为空，已耗时{int(elapsed_time)}s，继续等待")
            time.sleep(poll_interval)
            continue
        # 解析任务状态，分支处理
        result_item = extract_results[0]
        state_status = result_item["state"]
        # 状态1：任务完成，提取ZIP下载链接
        if state_status == "done":
            logger.info(f"[任务轮询] 解析任务完成！总耗时：{int(elapsed_time)}s，batch_id：{batch_id}")
            full_zip_url = result_item.get("full_zip_url")
            if not full_zip_url:
                # 修复：增加f-string格式化
                raise RuntimeError(f"[任务轮询] 任务完成但未返回ZIP包下载链接，batch_id：{batch_id}")
            logger.info(f"[任务轮询] 结果ZIP包下载链接：{full_zip_url}...")
            return full_zip_url
        # 状态2：任务失败，提取错误信息抛出
        elif state_status == "failed":
            err_msg = result_item.get("err_msg", "未知错误，无具体信息")
            raise RuntimeError(f"[任务轮询] 解析任务失败，batch_id：{batch_id}，错误信息：{err_msg}")
        # 状态3：处理中
        else:
            logger.debug(
                f"[任务轮询] 处理中（已耗时{int(elapsed_time)}s），状态：{state_status} | 刷新间隔{poll_interval}s"
            )
            time.sleep(poll_interval)


def step_3_download_and_extract(zip_url: str, output_dir_obj: Path, pdf_stem: str) -> str:
    """
    步骤3：下载MinerU解析结果ZIP包并解压，提取目标MD文件（重命名统一规范）
    核心流程：下载ZIP → 清理旧目录并解压 → 查找MD文件（按优先级） → 重命名统一为PDF同名
    参数：zip_url‑ZIP包下载链接；output_dir_obj‑输出目录Path；pdf_stem‑PDF无后缀纯名称
    返回：最终MD文件的字符串格式绝对路径
    异常：RuntimeError(下载失败)、FileNotFoundError(无MD文件)
    """
    logger.info(f"===== 开始处理[{pdf_stem}]的MinerU解析结果 =====")

    # 1. 下载解析结果ZIP包，120秒超时适配大文件
    logger.info(f"[步骤1/4] 开始下载ZIP包，链接：{zip_url}...")
    resp = requests.get(zip_url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"[步骤1/4] ZIP包下载失败，HTTP状态码：{resp.status_code}")

    # 拼接ZIP包保存路径，按PDF名称唯一命名
    zip_save_path = output_dir_obj / f"{pdf_stem}_result.zip"
    with open(zip_save_path, "wb") as f:
        f.write(resp.content)
    logger.info(f"[步骤1/4] ZIP包下载成功，保存路径：{zip_save_path}")

    # 2. 清理旧解压目录并解压ZIP包（避免旧文件干扰，为每个PDF创建专属目录）
    logger.info(f"[步骤2/4] 开始解压ZIP包...")
    extract_target_dir = output_dir_obj / pdf_stem

    # 清理旧目录，异常则警告不终止
    if extract_target_dir.exists():
        try:
            shutil.rmtree(extract_target_dir)
            logger.info(f"[步骤2/4] 已清理旧的解压目录：{extract_target_dir}")
        except Exception as e:
            logger.warning(f"[步骤2/4] 清理旧目录失败，可能不影响新文件解压：{str(e)}")

    # 重新创建解压目录
    extract_target_dir.mkdir(parents=True, exist_ok=True)

    # 核心解压操作，保留原目录结构
    with zipfile.ZipFile(zip_save_path, 'r') as zip_file_obj:
        zip_file_obj.extractall(extract_target_dir)
    logger.info(f"[步骤2/4] ZIP包解压完成，解压目录：{extract_target_dir}")

    # 3. 递归查找解压目录下所有MD文件（适配子目录结构）
    logger.info(f"[步骤3/4] 开始查找解压目录中的MD文件...")
    md_file_list = list(extract_target_dir.rglob("*.md"))
    if not md_file_list:
        raise FileNotFoundError(f"[步骤3/4] 解压目录中未找到任何.md格式文件：{extract_target_dir}")
    logger.info(f"[步骤3/4] 共找到{len(md_file_list)}个MD文件，按优先级匹配目标文件")

    # 4. 按优先级匹配目标MD文件（同名→full.md→第一个，兜底避免流程中断）
    target_md_file = None
    # 优先级1：与PDF纯名称完全同名的MD文件
    for md_file in md_file_list:
        if md_file.stem == pdf_stem:
            target_md_file = md_file
            logger.info(f"[步骤4/4] 匹配到优先级1目标：与PDF同名的MD文件 {target_md_file.name}")
            break
    # 优先级2：MinerU默认生成的full.md（不区分大小写）
    if not target_md_file:
        for md_file in md_file_list:
            if md_file.name.lower() == "full.md":
                target_md_file = md_file
                logger.info(f"[步骤4/4] 匹配到优先级2目标：MinerU默认文件 {target_md_file.name}")
                break
    # 优先级3：兜底取第一个MD文件
    if not target_md_file:
        target_md_file = md_file_list[0]
        logger.info(f"[步骤4/4] 未匹配到前两级目标，兜底取第一个MD文件 {target_md_file.name}")

    # 重命名MD文件：统一为PDF纯名称，便于后续流程处理（仅不同名时执行）
    if target_md_file.stem != pdf_stem:
        logger.info(f"[步骤4/4] 开始重命名MD文件，统一为PDF同名：{pdf_stem}.md")
        new_md_path = target_md_file.with_name(f"{pdf_stem}.md")
        try:
            target_md_file.rename(new_md_path)
            target_md_file = new_md_path
            logger.info(f"[步骤4/4] MD文件重命名成功：{pdf_stem}.md")
        except OSError as e:
            logger.warning(f"[步骤4/4] MD文件重命名失败，将使用原文件名继续流程：{str(e)}")

    # 转换为字符串绝对路径返回
    final_md_path = str(target_md_file.absolute())
    logger.info(f"===== [{pdf_stem}]解析结果处理完成，最终MD文件路径：{final_md_path} =====")
    return final_md_path


def node_pdf_to_md(state: ImportGraphState) -> ImportGraphState:
    """
    LangGraph工作流节点：PDF转MD核心处理节点
    新增自动分片合并：超过200页本地拆分，分别解析后合并输出完整md
    参数：state‑工作流状态对象，需包含pdf_path/local_dir/task_id
    返回：更新后的工作流状态，新增md_path/md_content
    """

    func_name = sys._getframe().f_code.co_name

    logger.debug(f"【{func_name}】节点启动，\n当前工作流状态：{format_state(state)}")

    add_running_task(state["task_id"], func_name)
    try:
        # 步骤1校验，额外拿到总页数
        pdf_path_obj, output_dir_obj, total_pages = step_1_validate_paths(state)

        md_content_list = []
        final_md_path = ""

        if 0 < total_pages <= MAX_SINGLE_PDF_PAGE:
            # 页数正常：原有单文件流程
            logger.info(f"【{func_name}】页数 {total_pages} ≤ {MAX_SINGLE_PDF_PAGE}，直接解析原始PDF")
            zip_url = step_2_upload_and_poll(pdf_path_obj, output_dir_obj)
            md_path = step_3_download_and_extract(zip_url, output_dir_obj, pdf_path_obj.stem)
            final_md_path = md_path
            with open(md_path, "r", encoding="utf-8") as f:
                md_content_list.append(f.read())
        else:
            # 页数超限：本地分片
            logger.info(f"【{func_name}】页数 {total_pages} > {MAX_SINGLE_PDF_PAGE}，执行本地PDF分片预处理")
            chunk_pdf_paths = split_pdf_into_chunks(pdf_path_obj, output_dir_obj)
            # 循环逐个解析分片（串行，避免短时间大量请求打满MinerU）
            for idx, chunk_pdf in enumerate(chunk_pdf_paths):
                logger.info(f"【{func_name}】开始处理分片 {idx+1}/{len(chunk_pdf_paths)} : {chunk_pdf.name}")
                chunk_md_path = parse_single_pdf(chunk_pdf, output_dir_obj)
                with open(chunk_md_path, "r", encoding="utf-8") as f:
                    chunk_text = f.read()
                # 增加分片分隔标记，方便阅读
                md_content_list.append(f"\n\n<!-- ========= PDF分片 {idx+1} 开始 ========= -->\n\n")
                md_content_list.append(chunk_text)
                md_content_list.append(f"\n\n<!-- ========= PDF分片 {idx+1} 结束 ========= -->\n\n")

            # 合并全部分片md，写出最终完整md文件
            merged_md_path = output_dir_obj / f"{pdf_path_obj.stem}_merged_all.md"
            full_text = "".join(md_content_list)
            with open(merged_md_path, "w", encoding="utf-8") as f_out:
                f_out.write(full_text)
            final_md_path = str(merged_md_path.absolute())
            logger.info(f"【{func_name}】分片全部解析完成，已合并输出完整MD：{final_md_path}")

        # 更新state，对外接口保持不变
        state["md_path"] = final_md_path
        state["md_content"] = "".join(md_content_list)
        logger.info(f"【{func_name}】MD文件生成成功，路径：{final_md_path}，总字符长度：{len(state['md_content'])}")

        logger.info(f"【{func_name}】节点执行完成，更新后工作流状态键：{list(state.keys())}")

    except Exception as e:
        logger.error(f"【{func_name}】PDF转MD流程执行失败：{str(e)}", exc_info=True)
        raise
    finally:
        add_done_task(state["task_id"], func_name)
        logger.debug(f"【{func_name}】节点执行完成，\n更新后工作流状态：{format_state(state)}")

    return state


if __name__ == "__main__":
    # 单元测试：验证PDF转MD全流程
    logger.info("===== 开始node_pdf_to_md节点单元测试 =====")

    from app.utils.path_util import PROJECT_ROOT
    logger.info(f"测试获取根地址：{PROJECT_ROOT}")

    test_pdf_name = os.path.join("doc", "hak180产品安全手册.pdf")
    test_pdf_path = os.path.join(PROJECT_ROOT, test_pdf_name)

    # 构造测试状态
    test_state = create_default_state(
        task_id="test_pdf2md_task_001",
        pdf_path=test_pdf_path,
        local_dir=os.path.join(PROJECT_ROOT, "output")
    )

    node_pdf_to_md(test_state)

    logger.info("===== 结束node_pdf_to_md节点单元测试 =====")
