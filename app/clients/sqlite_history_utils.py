# 对话历史记录存储 —— SQLite 版本
# ------------------------------------------------------------
# 提供对话历史的增/查/改/删接口（save_chat_message / get_recent_messages /
# clear_history / update_message_item_names），供查询服务和 LangGraph 节点调用。
#
# 特点：
#   ① 零额外依赖 —— sqlite3 是 Python 标准库，无需安装、无需 Docker；
#   ② 单文件存储 —— 默认落在 项目根/data/chat_history.db，备份就是复制一个文件；
#   ③ 懒加载 + 降级 —— 首次真正使用时才建库建表，初始化失败自动降级（不影响问答）；
#   ④ WAL 模式 + 线程锁 —— 适配 FastAPI 多线程/LangGraph 节点并发访问。

import os
import json
import sqlite3
import logging
import threading
from typing import List, Dict, Any
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# 全局单例连接与状态
_conn = None
_init_failed = False
_lock = threading.Lock()


def _default_db_path() -> str:
    """默认数据库路径：项目根/data/chat_history.db（不依赖启动时的工作目录）"""
    # 本文件位于 项目根/app/clients/，向上 3 级即项目根
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "data", "chat_history.db")


def _get_conn():
    """
    获取 SQLite 连接（懒加载单例）。
    初始化失败时打标记并返回 None，由上层函数做降级处理。
    """
    global _conn, _init_failed
    if _init_failed:
        return None
    if _conn is not None:
        return _conn
    try:
        db_path = os.getenv("HISTORY_SQLITE_PATH") or _default_db_path()
        # 确保父目录存在
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL 模式：读写互不阻塞，更适合并发场景
        conn.execute("PRAGMA journal_mode=WAL")

        # 建表：字段与 MongoDB 版文档结构一一对应
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_message (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                text            TEXT    DEFAULT '',
                rewritten_query TEXT    DEFAULT '',
                item_names      TEXT    DEFAULT '[]',   -- JSON 数组字符串
                image_urls      TEXT    DEFAULT '[]',   -- JSON 数组字符串
                ts              REAL    NOT NULL        -- 时间戳（秒）
            )
            """
        )
        # 复合索引：适配"按会话 + 时间"的核心查询场景
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_ts ON chat_message(session_id, ts DESC)"
        )
        conn.commit()

        _conn = conn
        logging.info(f"SQLite 历史记录存储已就绪: {db_path}")
        return _conn
    except Exception as e:
        _init_failed = True
        logging.warning(f"SQLite 历史记录初始化失败，对话历史功能自动降级（不影响问答）: {e}")
        return None


def save_chat_message(
        session_id: str,
        role: str,
        text: str,
        rewritten_query: str = "",
        item_names: List[str] = None,
        image_urls: List[str] = None,
        message_id: str = None
) -> str:
    """
    写入/更新单条会话记录。
    - 无 message_id：新增记录，返回新主键的字符串形式（对应 Mongo 版的 ObjectId 字符串）
    - 有 message_id：按主键更新整条记录，返回 message_id
    - 存储不可用时降级返回 message_id（不抛异常，不影响主问答流程）
    """
    ts = datetime.now().timestamp()
    conn = _get_conn()
    if conn is None:
        return message_id
    try:
        item_names_json = json.dumps(item_names or [], ensure_ascii=False)
        image_urls_json = json.dumps(image_urls or [], ensure_ascii=False)
        with _lock:
            if message_id:
                conn.execute(
                    """
                    UPDATE chat_message
                    SET session_id=?, role=?, text=?, rewritten_query=?,
                        item_names=?, image_urls=?, ts=?
                    WHERE id=?
                    """,
                    (session_id, role, text, rewritten_query or "",
                     item_names_json, image_urls_json, ts, int(message_id))
                )
                conn.commit()
                return message_id
            else:
                cur = conn.execute(
                    """
                    INSERT INTO chat_message
                        (session_id, role, text, rewritten_query, item_names, image_urls, ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, role, text, rewritten_query or "",
                     item_names_json, image_urls_json, ts)
                )
                conn.commit()
                return str(cur.lastrowid)
    except Exception as e:
        logging.error(f"保存对话消息失败: {e}")
        return message_id


def update_message_item_names(ids: List[str], item_names: List[str]) -> int:
    """
    批量更新历史记录的关联商品名称（语义与 Mongo 版一致：把这些记录的 item_names 设为同一个列表）。
    :return: 实际更新的行数，失败返回 0
    """
    conn = _get_conn()
    if conn is None:
        return 0
    if not ids:
        return 0
    try:
        int_ids = [int(i) for i in ids]
        placeholders = ",".join("?" * len(int_ids))
        with _lock:
            cur = conn.execute(
                f"UPDATE chat_message SET item_names=? WHERE id IN ({placeholders})",
                [json.dumps(item_names or [], ensure_ascii=False), *int_ids]
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        logging.error(f"批量更新商品名称失败: {e}")
        return 0


def get_recent_messages(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    查询指定会话最近 N 条记录，按时间正序（旧→新）返回，可直接拼进 LLM 上下文。
    返回的字典字段与 Mongo 版保持一致：_id / session_id / role / text /
    rewritten_query / item_names(list) / image_urls(list) / ts
    """
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with _lock:
            # 先按时间倒序取最近 N 条，再正序排列（保证喂给 LLM 的上下文从旧到新）
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM chat_message
                    WHERE session_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                ) ORDER BY ts ASC
                """,
                (session_id, limit)
            ).fetchall()

        messages = []
        for r in rows:
            try:
                item_names = json.loads(r["item_names"] or "[]")
            except Exception:
                item_names = []
            try:
                image_urls = json.loads(r["image_urls"] or "[]")
            except Exception:
                image_urls = []
            messages.append({
                "_id": r["id"],
                "session_id": r["session_id"],
                "role": r["role"],
                "text": r["text"],
                "rewritten_query": r["rewritten_query"],
                "item_names": item_names,
                "image_urls": image_urls,
                "ts": r["ts"],
            })
        return messages
    except Exception as e:
        logging.error(f"查询历史消息失败: {e}")
        return []


def clear_history(session_id: str) -> int:
    """
    清空指定会话的所有历史记录。
    :return: 实际删除的行数，失败返回 0
    """
    conn = _get_conn()
    if conn is None:
        return 0
    try:
        with _lock:
            cur = conn.execute("DELETE FROM chat_message WHERE session_id = ?", (session_id,))
            conn.commit()
            logging.info(f"已删除会话 {session_id} 的 {cur.rowcount} 条历史消息")
            return cur.rowcount
    except Exception as e:
        logging.error(f"清空历史记录失败 (session={session_id}): {e}")
        return 0


def get_all_sessions() -> List[Dict[str, Any]]:
    """
    获取所有会话列表，包含每个会话的消息数量、首条和末条时间、首条用户提问预览。
    用于「对话历史」页面展示会话列表。
    :return: List[Dict]，每个元素字段：session_id / message_count / first_ts / last_ts / first_user_question
    """
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with _lock:
            rows = conn.execute(
                """
                SELECT session_id,
                       COUNT(*) AS message_count,
                       MIN(ts) AS first_ts,
                       MAX(ts) AS last_ts
                FROM chat_message
                GROUP BY session_id
                ORDER BY last_ts DESC
                """
            ).fetchall()

        sessions = []
        for r in rows:
            sid = r["session_id"]
            # 取该会话最早的一条 user 消息作为预览
            preview = ""
            try:
                with _lock:
                    cur = conn.execute(
                        "SELECT text FROM chat_message WHERE session_id=? AND role='user' ORDER BY ts ASC LIMIT 1",
                        (sid,)
                    )
                    row = cur.fetchone()
                    if row:
                        preview = (row["text"] or "")[:80]
            except Exception:
                pass

            sessions.append({
                "session_id": sid,
                "message_count": r["message_count"],
                "first_ts": r["first_ts"],
                "last_ts": r["last_ts"],
                "preview": preview,
            })
        return sessions
    except Exception as e:
        logging.error(f"获取所有会话列表失败: {e}")
        return []


def delete_session(session_id: str) -> int:
    """
    删除整个会话的所有记录（比 clear_history 语义更明确，供前端调用）。
    :return: 实际删除行数
    """
    return clear_history(session_id)


if __name__ == "__main__":
    # 简单自测：写入 → 查询 → 更新商品名 → 清空
    sid = "test_sqlite_session"
    clear_history(sid)

    mid = save_chat_message(sid, "user", "HAK180烫金机多少钱？")
    print("插入用户消息, id =", mid)
    save_chat_message(sid, "assistant", "您好，HAK180烫金机售价请联系销售。", item_names=[])
    save_chat_message(sid, "user", "那它的供电电压呢？")

    print("更新商品名, 影响行数 =", update_message_item_names([mid], ["HAK180烫金机"]))

    msgs = get_recent_messages(sid, limit=10)
    print(f"查询到 {len(msgs)} 条记录：")
    for m in msgs:
        print(f"  [{m['role']}] {m['text']}  item_names={m['item_names']}  _id={m['_id']}")

    print("清空, 删除行数 =", clear_history(sid))
    print("清空调用后查询 =", len(get_recent_messages(sid)), "条")
