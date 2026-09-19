# 🧠 掌柜智库 - RAG 智能问答系统

一个基于 **LangGraph + Milvus + BGE-M3** 的本地知识问答系统。上传产品手册、安全规范等 PDF，系统自动解析 → 向量化 → 存入向量库，随后可通过自然语言检索到具体段落并由大模型生成答案。

## 🌟 特性

- 📄 **文档自动解析**：支持 PDF / Markdown 上传，PDF 调用 MinerU 在线 API 解析
- 📚 **大 PDF 自动分片**：超过 200 页（MinerU 单文件上限）的 PDF 本地自动按 190 页拆分，逐片解析后合并，无需手动拆分
- 🧩 **智能切分 + 商品识别**：LLM 辅助识别文档对应的产品名称，建库时一并写入
- 🔍 **混合检索 + 重排序**：BGE-M3 稠密/稀疏向量混合检索 + bge-reranker-large 精排 + RRF 融合
- 💬 **前端界面**：系统首页、文档导入页、知识库管理页、对话历史页、系统状态页、流式问答页（SSE）

## 🏗️ 架构

```
┌──────────────────────┐    ┌──────────────────────┐
│   导入服务 :8000      │    │   查询服务 :8001      │
│  FastAPI + LangGraph │    │  FastAPI + LangGraph │
└─────────┬────────────┘    └─────────┬────────────┘
          │                           │
   ┌──────┴──────┐             ┌──────┴──────┐
   │ BGE-M3      │             │ BGE-M3      │
   │ 向量生成    │             │ 向量生成    │
   └──────┬──────┘             └──────┬──────┘
          │                           │
   ┌──────┴───────────────────────────┴──────┐
   │         Milvus (Docker :19530)           │
   │         kb_chunks / kb_item_names        │
   └──────────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │  bge-reranker   │
              │  -large 精排    │
              └────────┬────────┘
                       │
              ┌────────┴────────┐
              │ 阿里云 DashScope│
              │  qwen-flash 生成│
              └─────────────────┘
```

## 🧰 技术栈

| 类别 | 组件 | 版本 |
|------|------|------|
| 语言 | Python | ≥ 3.11 |
| 依赖管理 | uv | - |
| Web 框架 | FastAPI + Uvicorn | ≥ 0.135 |
| Agent 编排 | LangGraph | ≥ 1.1 |
| 向量数据库 | Milvus (standalone) | v2.6.0 |
| 对象存储 | MinIO | - |
| 向量模型 | BAAI/bge-m3 | 本地 CPU/GPU |
| 重排序模型 | BAAI/bge-reranker-large | 本地 CPU/GPU |
| LLM | 阿里云百炼 qwen-flash | DashScope API |
| PDF 解析 | MinerU (magic-pdf) | 在线 API |
| 容器化 | Docker Desktop | - |

## 📦 前置条件

1. **Python 3.11+**（推荐 3.12）
2. **Docker Desktop**（Milvus + MinIO 跑在容器里）
3. **uv**（包管理，比 pip 快很多）
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
4. **阿里云百炼 API Key**（意图识别 + 答案生成）：https://bailian.console.aliyun.com/
5. **MinerU API Token**（PDF 解析）：https://mineru.net

## 🚀 快速启动（Windows）

项目已提供一键脚本，三条命令搞定。

### 1. 安装依赖

```powershell
cd RAG_Project
uv sync
```

### 2. 下载本地模型（首次）

```powershell
# 向量模型 (~2GB)
$env:MODELSCOPE_OFFLINE="0"
uv run python app/tool/download_bgem3.py

# 重排序模型 (~2GB)
uv run python app/tool/download_reranker.py
```

模型默认下载到 `D:\ai_models\modelscope_cache`，路径可在 `.env` 中修改。

### 3. 配置环境变量

复制 `.env` 并填入你的密钥：

```ini
# 必填：阿里云百炼 API Key
OPENAI_API_KEY=sk-你的百炼key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 必填：MinerU API Token（PDF 解析）
MINERU_API_TOKEN=你的MinerUtoken
MINERU_BASE_URL=https://mineru.net/api/v4

# Milvus 本地 Docker
MILVUS_URL=http://127.0.0.1:19530

# BGE-M3 本地路径
BGE_M3_PATH=D:\ai_models\modelscope_cache\models\BAAI\bge-m3

# bge-reranker 本地路径
BGE_RERANKER_LARGE=D:\ai_models\modelscope_cache\models\rerank\BAAI\bge-reranker-large
```

### 4. 一键启动（Windows 脚本）

项目提供 3 个 `.bat` 脚本，**双击即可执行**（文件资源管理器中双击，或在 CMD/PowerShell 中运行都行）。

```powershell
# 使用顺序：打开 Docker Desktop → 启动脚本 → 使用 → 停止脚本
.\start.bat       # 启动（Milvus + 导入服务 + 查询服务）
.\stop.bat        # 日常关闭（只停 RAG，保留 Milvus 快速重启）
.\stop-milvus.bat # 彻底关闭（连 Milvus 也停掉，释放全部内存）
```

#### 📜 三个脚本详解

##### `start.bat` — 一键启动全部

启动顺序：① 检查 `.venv` 虚拟环境 → ② 启动 Milvus 的 3 个 Docker 容器（etcd / MinIO / Milvus standalone）→ ③ 清理 8000/8001 端口的残留进程 → ④ 弹出**两个独立的黑色命令行窗口**分别运行导入服务和查询服务。

首次启动后约 1 分钟才能访问，因为 BGE-M3 + reranker 两个本地模型需要加载到内存。

> 💡 如果 Docker Desktop 没开，脚本会自动跳过 Milvus 启动并给出提示；等手动打开 Docker 后再重新运行本脚本即可。

##### `stop.bat` — 日常快速关闭

只停止 8000/8001 两个 RAG 服务进程，**Milvus 容器继续保留在后台运行**。下次 `start.bat` 时 Milvus 已经在跑，整体启动时间能省掉 1-2 分钟的容器等待。适用场景：日常用完就关、过会儿还要再开。

##### `stop-milvus.bat` — 彻底关闭

先停 8000/8001，再执行 `docker compose stop` 把 Milvus 的 3 个容器也停掉。数据卷（etcd_data / minio_data / milvus_data）不会删除，下次 start 自动恢复。适用场景：关机前、长时间不用、或想腾出内存给别的程序。

---

#### 🛠️ 手动启动（不通过脚本）

如果想在 PyCharm 里调试、或脚本异常时排查，按以下步骤手动启动。

**第 1 步：启动 Milvus（Docker Desktop 已打开前提下）**

```powershell
cd deploy\milvus
docker compose up -d
# 等待 docker compose ps 显示 3 个服务 healthy
```

**第 2 步：启动导入服务（新开一个终端窗口）**

```powershell
cd c:\Users\LSA00\Desktop\RAG_Project
.venv\Scripts\activate.bat
set PYTHONPATH=c:\Users\LSA00\Desktop\RAG_Project
python app\import_process\api\file_import_service.py
```

**第 3 步：启动查询服务（再新开一个终端窗口）**

```powershell
cd c:\Users\LSA00\Desktop\RAG_Project
.venv\Scripts\activate.bat
set PYTHONPATH=c:\Users\LSA00\Desktop\RAG_Project
python app\query_process\api\query_service.py
```

**PyCharm 里启动更简单**：右下角解释器选 `.venv` → 分别右键 `file_import_service.py` 和 `query_service.py` → Run 即可（PyCharm 会自动处理 PYTHONPATH）。

**手动关闭**：在终端里按 `Ctrl + C`，或直接关闭窗口；PyCharm 点运行窗口左下角红色 ⏹。

---

启动后访问：

| 页面 | 地址 |
|------|------|
| 系统首页 | http://127.0.0.1:8000/ |
| 上传文档 | http://127.0.0.1:8000/import.html |
| 知识库管理 | http://127.0.0.1:8000/kb.html |
| 对话历史 | http://127.0.0.1:8000/history.html |
| 系统状态 | http://127.0.0.1:8000/system.html |
| 智能问答 | http://127.0.0.1:8001/chat.html |
| 导入 Swagger | http://127.0.0.1:8000/docs |
| 查询 Swagger | http://127.0.0.1:8001/docs |

## 📁 项目结构

```
RAG_Project/
├── app/
│   ├── import_process/          # 文档导入流程
│   │   ├── agent/
│   │   │   ├── main_graph.py    # LangGraph 流程图
│   │   │   └── nodes/           # 各处理节点（PDF转MD、切分、向量生成、入库…）
│   │   ├── api/                 # FastAPI 入口
│   │   └── page/                # 前端 HTML
│   ├── query_process/           # 智能问答流程
│   │   ├── agent/
│   │   │   ├── main_graph.py    # LangGraph 流程图
│   │   │   └── nodes/           # HyDE改写、向量检索、重排序、RRF融合、答案生成…
│   │   ├── api/
│   │   └── page/
│   ├── lm/                      # 大模型封装（Embedding / LM / Reranker）
│   ├── clients/                 # Milvus / MinIO / Mongo / Neo4j 客户端
│   ├── conf/                    # .env 配置加载
│   ├── core/                    # 日志、Prompt 加载
│   ├── utils/                   # 通用工具
│   └── tool/                    # 模型下载脚本
├── deploy/milvus/               # Milvus 本地 Docker 部署
│   └── docker-compose.yml
├── prompts/                     # LangGraph 各节点的 Prompt 模板
├── .env.example                 # 环境变量模板
├── pyproject.toml               # 依赖声明
├── start.bat                    # 一键启动脚本（Windows）
├── stop.bat                     # 停止 RAG 服务
└── stop-milvus.bat              # 停止全部
```

## 🔌 API 端点

### 导入服务 (8000)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload` | 上传文件并启动导入流程（支持多文件批量） |
| GET  | `/status/{task_id}` | 查询导入任务状态 |
| GET  | `/api/kb_stats` | 知识库统计（文档数、分块数） |
| GET  | `/api/kb_chunks` | 知识库分块明细 |
| GET  | `/api/system_status` | 服务/模型/数据库状态 |
| GET  | `/`、`/import.html`、`/kb.html`、`/history.html`、`/system.html` | 前端页面 |

### 查询服务 (8001)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/query` | 提问（同步） |
| GET  | `/stream/{session_id}` | 流式问答（SSE） |
| GET  | `/health` | 健康检查 |
| GET  | `/history/{session_id}` | 获取指定会话历史 |
| DELETE | `/history/{session_id}` | 清空指定会话历史 |
| GET  | `/api/all_history` | 获取所有会话列表（对话历史页） |
| DELETE | `/api/session/{session_id}` | 删除整个会话 |
| GET  | `/chat.html` | 智能问答页 |

## ⚠️ 注意事项

- **对话历史用 SQLite 存储**（零依赖、零配置）：数据库文件自动生成在 `data/chat_history.db`，无需安装任何数据库；多轮对话上下文、刷新页面后的历史恢复均由此支持。可用环境变量 `HISTORY_SQLITE_PATH` 自定义路径。
- **知识图谱节点为预留空节点**：LangGraph 流程图中的 `node_query_kg`（Neo4j 知识图谱查询）当前是空实现（占位），不影响问答主链路。
- **每次修改 `.env` 后必须重启服务**才会生效
- CPU 上加载 BGE-M3 + reranker 两个模型约需 1 分钟，首次启动请耐心等待
- MinerU 在线解析 PDF 单文件上限 200 页，超过后系统会自动按 190 页分片逐个解析再合并（无需手动拆分）；解析仍消耗云端额度

## 📄 License

MIT
