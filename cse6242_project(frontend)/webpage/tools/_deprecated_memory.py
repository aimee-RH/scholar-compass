#!/usr/bin/env python3
"""
Memory Module - Session Management & Conversation Memory for LangGraph Agent

基于 Neo4j 的会话记忆管理模块,支持:
1. 多轮对话上下文持久化
2. 基于 LLM 的指代消解 ("他"/"这个学者" -> 具体学者名)
3. 用户与会话追踪
4. 对话历史检索与分析

Schema:
- (:User)-[:HAS_SESSION]->(:Session)
- (:Session)-[:HAS_MESSAGE]->(:Message)
- (:Message)-[:REFERENCES]->(:Author)

Author: Scholar Compass Team
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

from tools.neo4j_connector import neo4j_connector


logger = logging.getLogger("scholar.memory")


# ========================================
# LLM 客户端协议 (依赖注入)
# ========================================

class LLMClient(Protocol):
    """
    LLM 客户端协议。任何实现了 `complete(prompt: str) -> str` 的对象都可以注入。

    这样做的好处是不绑定到具体框架 (OpenAI / LangChain / Anthropic 等),
    你在外面做一层适配即可。
    """

    def complete(self, prompt: str) -> str: ...


# ========================================
# 数据结构
# ========================================

@dataclass
class ResolvedMessage:
    """指代消解的结果。"""
    original: str
    resolved: str
    referenced_authors: List[str]

    @property
    def was_resolved(self) -> bool:
        """是否真的发生了改写 (原文与消解后不同)。"""
        return self.original != self.resolved


# ========================================
# 会话管理
# ========================================

def create_user(user_id: Optional[str] = None) -> str:
    """
    创建或获取用户节点。

    Args:
        user_id: 用户 ID,为 None 时自动生成 UUID

    Returns:
        用户 ID
    """
    if user_id is None:
        user_id = f"user_{uuid.uuid4().hex[:8]}"

    query = """
    MERGE (u:User {user_id: $user_id})
    ON CREATE SET u.created_at = datetime(),
                  u.last_active = datetime(),
                  u._is_new = true
    ON MATCH  SET u.last_active = datetime(),
                  u._is_new = false
    WITH u, u._is_new as is_new
    REMOVE u._is_new
    RETURN u.user_id as user_id, is_new
    """

    try:
        result = neo4j_connector.execute_query(query, {"user_id": user_id})
        is_new = result[0].get("is_new", False) if result else False
        logger.info("用户%s: %s", "已创建" if is_new else "已存在", user_id)
        return user_id
    except Exception:
        logger.exception("创建用户失败: user_id=%s", user_id)
        raise


def create_session(
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    创建新会话。

    Args:
        user_id: 用户 ID,默认 "default_user"
        metadata: 会话元数据 (会被 JSON 序列化后存储)

    Returns:
        session_id
    """
    if user_id is None:
        user_id = "default_user"

    create_user(user_id)

    session_id = str(uuid.uuid4())
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

    query = """
    MATCH (u:User {user_id: $user_id})
    CREATE (s:Session {
        session_id: $session_id,
        user_id: $user_id,
        created_at: datetime(),
        updated_at: datetime(),
        metadata: $metadata
    })
    CREATE (u)-[:HAS_SESSION]->(s)
    RETURN s.session_id as session_id
    """

    try:
        neo4j_connector.execute_query(query, {
            "user_id": user_id,
            "session_id": session_id,
            "metadata": metadata_json,
        })
        logger.info("会话已创建: session_id=%s user_id=%s", session_id, user_id)
        return session_id
    except Exception:
        logger.exception("创建会话失败: user_id=%s", user_id)
        raise


def get_session_info(session_id: str) -> Optional[Dict[str, Any]]:
    """获取会话基本信息(含消息计数)。"""
    # 先检查 Session 是否存在
    check_query = """
    MATCH (s:Session {session_id: $session_id})
    RETURN count(s) as session_exists
    """
    try:
        check_result = neo4j_connector.execute_query(check_query, {"session_id": session_id})
        if not check_result or check_result[0].get("session_exists", 0) == 0:
            return None
    except Exception:
        logger.exception("检查会话失败: session_id=%s", session_id)
        raise

    query = """
    MATCH (s:Session {session_id: $session_id})
    OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:Message)
    RETURN s.session_id   as session_id,
           s.user_id      as user_id,
           s.created_at   as created_at,
           s.updated_at   as updated_at,
           s.metadata     as metadata,
           count(m)       as message_count
    """
    try:
        results = neo4j_connector.execute_query(query, {"session_id": session_id})
        return results[0] if results else None
    except Exception:
        logger.exception("获取会话信息失败: session_id=%s", session_id)
        raise


def _ensure_session_exists(session_id: str) -> None:
    """确保 Session 存在，如果不存在则创建一个。"""
    check_query = """
    MERGE (s:Session {session_id: $session_id})
    ON CREATE SET s.created_at = datetime(), s.updated_at = datetime()
    ON MATCH SET s.updated_at = datetime()
    RETURN s.session_id
    """
    try:
        neo4j_connector.execute_query(check_query, {"session_id": session_id})
    except Exception:
        logger.exception("确保 Session 存在失败: session_id=%s", session_id)


def _touch_session(session_id: str) -> None:
    """内部方法: 更新会话的 updated_at 时间戳。"""
    query = """
    MATCH (s:Session {session_id: $session_id})
    SET s.updated_at = datetime()
    """
    try:
        # 先检查是否存在
        check = neo4j_connector.execute_query(
            "MATCH (s:Session {session_id: $session_id}) RETURN count(s) as c",
            {"session_id": session_id}
        )
        if check and check[0].get("c", 0) > 0:
            neo4j_connector.execute_query(query, {"session_id": session_id})
    except Exception:
        pass  # 静默失败，不影响主流程


def list_user_sessions(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """列出用户的会话,按最近活跃排序。"""
    # 先检查 User 是否存在
    check_query = """
    MATCH (u:User {user_id: $user_id})
    RETURN count(u) as user_exists
    """
    try:
        check_result = neo4j_connector.execute_query(check_query, {"user_id": user_id})
        if not check_result or check_result[0].get("user_exists", 0) == 0:
            return []
    except Exception:
        return []

    query = """
    MATCH (u:User {user_id: $user_id})-[:HAS_SESSION]->(s:Session)
    OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:Message)
    WITH s, count(m) as msg_count
    RETURN s.session_id  as session_id,
           s.created_at  as created_at,
           s.updated_at  as updated_at,
           s.metadata    as metadata,
           msg_count     as message_count
    ORDER BY s.updated_at DESC
    LIMIT $limit
    """
    try:
        return neo4j_connector.execute_query(query, {"user_id": user_id, "limit": limit})
    except Exception:
        logger.exception("列出用户会话失败: user_id=%s", user_id)
        return []


# ========================================
# 消息管理
# ========================================

_VALID_ROLES = {"user", "assistant"}


def save_message(
    session_id: str,
    content: str,
    role: str,
    referenced_authors: Optional[List[str]] = None,
    resolved_message: Optional[str] = None,
) -> str:
    """
    保存一条消息,并可选地建立到 Author 节点的引用关系。

    Args:
        session_id: 会话 ID
        content: 消息原文
        role: 'user' 或 'assistant'
        referenced_authors: 消息中提及的学者名列表
        resolved_message: 指代消解后的消息 (可选)

    Returns:
        message_id
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r}, must be one of {_VALID_ROLES}")

    # 确保 Session 存在
    _ensure_session_exists(session_id)

    message_id = str(uuid.uuid4())

    create_query = """
    MATCH (s:Session {session_id: $session_id})
    CREATE (m:Message {
        message_id: $message_id,
        content: $content,
        role: $role,
        resolved_message: $resolved_message,
        created_at: datetime()
    })
    CREATE (s)-[:HAS_MESSAGE]->(m)
    """

    try:
        neo4j_connector.execute_query(create_query, {
            "session_id": session_id,
            "message_id": message_id,
            "content": content,
            "role": role,
            "resolved_message": resolved_message,
        })

        if referenced_authors:
            for name in referenced_authors:
                _link_author_reference(message_id, name)

        _touch_session(session_id)

        logger.info(
            "消息已保存: message_id=%s role=%s session_id=%s (refs=%d)",
            message_id, role, session_id, len(referenced_authors or []),
        )
        return message_id
    except Exception:
        logger.exception("保存消息失败: session_id=%s role=%s", session_id, role)
        raise


def _link_author_reference(message_id: str, author_name: str) -> None:
    """
    将消息关联到 Author 节点,优先匹配精确名称,其次走包含关系。

    使用 MERGE 避免重复关系 (同一消息多次保存同一学者只会有一条边)。
    """
    query = """
    MATCH (m:Message {message_id: $message_id})
    MATCH (a:Author)
    WHERE toLower(a.display_name) = toLower($author_name)
       OR toLower(a.display_name) CONTAINS toLower($author_name)
    WITH m, a,
         CASE WHEN toLower(a.display_name) = toLower($author_name) THEN 0 ELSE 1 END as priority,
         size(a.display_name) as name_len
    ORDER BY priority ASC, name_len ASC
    LIMIT 1
    MERGE (m)-[:REFERENCES]->(a)
    RETURN a.display_name as matched_author
    """
    try:
        result = neo4j_connector.execute_query(query, {
            "message_id": message_id,
            "author_name": author_name,
        })
        if result:
            logger.debug("作者引用已建立: %r -> %r", author_name, result[0]["matched_author"])
        else:
            logger.debug("未找到匹配的 Author 节点: %r", author_name)
    except Exception:
        logger.exception("建立作者引用失败: message_id=%s author=%s", message_id, author_name)


def get_conversation_history(session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    获取会话历史,按时间正序排列 (最早的消息在前)。

    注意: 我们先在子查询里按时间取最近 N 条,再按正序返回,
    避免 `ORDER BY ASC LIMIT N` 拿到的是最早 N 条而不是最近 N 条。
    """
    # 先检查 Session 是否存在，避免产生警告
    check_query = """
    MATCH (s:Session {session_id: $session_id})
    RETURN count(s) as session_exists
    """
    try:
        check_result = neo4j_connector.execute_query(check_query, {"session_id": session_id})
        if not check_result or check_result[0].get("session_exists", 0) == 0:
            logger.debug("会话不存在，返回空历史: session_id=%s", session_id)
            return []
    except Exception:
        logger.exception("检查会话历史失败: session_id=%s", session_id)
        raise

    query = """
    MATCH (s:Session {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
    WITH m ORDER BY m.created_at DESC LIMIT $limit
    OPTIONAL MATCH (m)-[:REFERENCES]->(a:Author)
    WITH m, collect(DISTINCT a.display_name)[0..5] as referenced_authors
    RETURN m.message_id        as message_id,
           m.content            as content,
           m.role               as role,
           m.resolved_message   as resolved_message,
           referenced_authors,
           m.created_at         as created_at
    ORDER BY m.created_at ASC
    """
    try:
        results = neo4j_connector.execute_query(query, {"session_id": session_id, "limit": limit})
        logger.debug("检索到 %d 条历史消息: session_id=%s", len(results), session_id)
        return results
    except Exception:
        logger.exception("获取会话历史失败: session_id=%s", session_id)
        raise


def get_last_n_messages(session_id: str, n: int = 5) -> List[Dict[str, Any]]:
    """
    获取最近 N 条消息,按正序返回 (最早的在前),便于直接喂给 LLM。
    """
    # 先检查 Session 是否存在
    check_query = """
    MATCH (s:Session {session_id: $session_id})
    RETURN count(s) as session_exists
    """
    try:
        check_result = neo4j_connector.execute_query(check_query, {"session_id": session_id})
        if not check_result or check_result[0].get("session_exists", 0) == 0:
            return []
    except Exception:
        return []

    query = """
    MATCH (s:Session {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
    WITH m ORDER BY m.created_at DESC LIMIT $n
    OPTIONAL MATCH (m)-[:REFERENCES]->(a:Author)
    WITH m, collect(DISTINCT a.display_name)[0..5] as referenced_authors
    RETURN m.content            as content,
           m.role               as role,
           m.resolved_message   as resolved_message,
           referenced_authors,
           m.created_at         as created_at
    ORDER BY m.created_at ASC
    """
    try:
        return neo4j_connector.execute_query(query, {"session_id": session_id, "n": n})
    except Exception:
        logger.exception("获取最近消息失败: session_id=%s", session_id)
        return []


def clear_session(session_id: str) -> int:
    """
    清空会话的所有消息 (保留会话本身)。

    Returns:
        被删除的消息数量
    """
    # 先 count 再 delete,避免删完之后 count 永远为 0
    query = """
    MATCH (s:Session {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
    WITH collect(m) as msgs, count(m) as deleted_count
    FOREACH (n IN msgs | DETACH DELETE n)
    RETURN deleted_count
    """
    try:
        result = neo4j_connector.execute_query(query, {"session_id": session_id})
        deleted = result[0]["deleted_count"] if result else 0
        logger.info("会话已清空: session_id=%s deleted=%d", session_id, deleted)
        return deleted
    except Exception:
        logger.exception("清空会话失败: session_id=%s", session_id)
        return 0


def delete_session(session_id: str) -> bool:
    """删除会话及其所有消息。"""
    # 先检查是否存在
    check_query = """
    MATCH (s:Session {session_id: $session_id})
    RETURN count(s) as session_exists
    """
    try:
        check_result = neo4j_connector.execute_query(check_query, {"session_id": session_id})
        if not check_result or check_result[0].get("session_exists", 0) == 0:
            return False  # Session 不存在，视为删除成功
    except Exception:
        return False

    query = """
    MATCH (s:Session {session_id: $session_id})
    OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:Message)
    DETACH DELETE s, m
    """
    try:
        neo4j_connector.execute_query(query, {"session_id": session_id})
        logger.info("会话已删除: session_id=%s", session_id)
        return True
    except Exception:
        logger.exception("删除会话失败: session_id=%s", session_id)
        return False


# ========================================
# 基于 LLM 的指代消解
# ========================================

_RESOLVER_SYSTEM_PROMPT = """你是学者检索系统的指代消解助手。你的任务是:
1. 结合最近的对话历史,识别用户当前消息中的代词和模糊指代 (例如 "他"、"她"、"这个学者"、"这两位"、"该学者" 等)。
2. 将这些指代替换为具体的学者姓名。
3. 从原始消息 + 消解后的消息中,提取所有被提及的学者姓名。

必须严格返回一个 JSON 对象,不要任何额外文字、Markdown 代码块或解释。
JSON 格式如下:
{
  "resolved": "消解后的完整消息 (若无需消解则与原文相同)",
  "authors": ["学者姓名1", "学者姓名2"]
}

规则:
- 如果当前消息里没有任何指代,也没有新提到学者,authors 可以是空数组 []。
- 只替换真正的指代,不要把 "这个问题"、"这样做" 这种非学者指代也改掉。
- 保留原消息的其他部分不变 (包括标点、语气)。
- 作者姓名使用对话中出现过的完整写法 (例如 "Yao Xie" 而不是 "Yao")。
"""


_RESOLVER_USER_TEMPLATE = """[对话历史]
{history}

[当前用户消息]
{current}

请输出 JSON。"""


class ReferenceResolver:
    """
    基于 LLM 的指代消解器。

    使用方式:
        resolver = ReferenceResolver(llm_client=my_llm)
        result = resolver.resolve(session_id, "他的Top合作者是谁?")
        # result.resolved -> "Yao Xie的Top合作者是谁?"
        # result.referenced_authors -> ["Yao Xie"]
    """

    def __init__(self, llm_client: LLMClient, context_window: int = 6):
        """
        Args:
            llm_client: 实现了 `complete(prompt: str) -> str` 的对象
            context_window: 喂给 LLM 的最近消息条数
        """
        self._llm = llm_client
        self._context_window = context_window

    def resolve(self, session_id: str, current_message: str) -> ResolvedMessage:
        """
        对当前消息做指代消解。

        任何错误 (LLM 调用失败 / JSON 解析失败) 都会 fallback 到原始消息,
        这样上游调用方不需要处理消解失败的情况。
        """
        if not current_message or not current_message.strip():
            return ResolvedMessage(current_message, current_message, [])

        history = get_last_n_messages(session_id, self._context_window)
        history_text = self._format_history(history)

        prompt = _RESOLVER_SYSTEM_PROMPT + "\n\n" + _RESOLVER_USER_TEMPLATE.format(
            history=history_text or "(无历史对话)",
            current=current_message,
        )

        try:
            raw = self._llm.complete(prompt)
        except Exception:
            logger.exception("LLM 调用失败,回退到原文: session_id=%s", session_id)
            return ResolvedMessage(current_message, current_message, [])

        parsed = self._parse_llm_output(raw)
        if parsed is None:
            logger.warning("LLM 输出无法解析,回退到原文。raw=%r", raw[:200])
            return ResolvedMessage(current_message, current_message, [])

        resolved = parsed.get("resolved") or current_message
        authors = parsed.get("authors") or []
        # 规范化: 去重 + 去空白 + 保持顺序
        authors = self._dedup_preserve_order(
            a.strip() for a in authors if isinstance(a, str) and a.strip()
        )

        result = ResolvedMessage(
            original=current_message,
            resolved=resolved,
            referenced_authors=authors,
        )
        if result.was_resolved:
            logger.info("指代消解: %r -> %r (authors=%s)",
                        current_message, resolved, authors)
        else:
            logger.debug("无需消解: %r (authors=%s)", current_message, authors)
        return result

    @staticmethod
    def _format_history(history: List[Dict[str, Any]]) -> str:
        """把历史消息拼成 LLM 友好的多行文本。"""
        if not history:
            return ""
        lines = []
        for msg in history:
            role = "用户" if msg.get("role") == "user" else "助手"
            # 历史消息用已消解的版本 (如果有),这样上下文更清晰
            content = msg.get("resolved_message") or msg.get("content", "")
            line = f"{role}: {content}"
            refs = msg.get("referenced_authors") or []
            if refs:
                line += f"  [提及: {', '.join(refs[:3])}]"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _parse_llm_output(raw: str) -> Optional[Dict[str, Any]]:
        """
        解析 LLM 输出为 JSON。容错:
        - 直接解析
        - 去掉 markdown 代码围栏后再解析
        - 从文本中抽出第一个 {...} 块解析
        """
        if not raw:
            return None

        # 去掉常见的 markdown 围栏
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        # 直接尝试
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 抽第一个大括号块
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _dedup_preserve_order(items) -> List[str]:
        seen = set()
        out = []
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out


# ========================================
# 上下文构建 (给 LLM 做 prompt)
# ========================================

def build_conversation_context(
    session_id: str,
    max_messages: int = 10,
    include_authors: bool = True,
) -> str:
    """
    构建格式化的会话上下文字符串,用于注入到下游 LLM 的 prompt。

    优先使用消解后的消息 (resolved_message),使上下文语义更明确。
    """
    history = get_conversation_history(session_id, max_messages)
    if not history:
        return "[会话历史: 无]"

    lines = ["[会话历史]"]
    for msg in history:
        role = "用户" if msg["role"] == "user" else "助手"
        content = msg.get("resolved_message") or msg["content"]
        line = f"{role}: {content}"
        if include_authors:
            refs = msg.get("referenced_authors") or []
            if refs:
                line += f"  [提及学者: {', '.join(refs[:3])}]"
        lines.append(line)
    return "\n".join(lines)


# ========================================
# 分析
# ========================================

def get_session_analytics(session_id: str) -> Dict[str, Any]:
    """
    获取会话分析指标。

    注意: 改用 sum(CASE WHEN ...) 替代 pattern comprehension,
    兼容性更好且可读性更强。
    """
    query = """
    MATCH (s:Session {session_id: $session_id})
    OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:Message)
    OPTIONAL MATCH (m)-[:REFERENCES]->(a:Author)
    WITH
        count(DISTINCT m) as total_messages,
        count(DISTINCT a) as unique_authors_mentioned,
        collect(DISTINCT a.display_name)[0..10] as top_authors,
        sum(CASE WHEN m.role = 'user'      THEN 1 ELSE 0 END) as user_messages,
        sum(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END) as assistant_messages
    RETURN total_messages,
           unique_authors_mentioned,
           [name IN top_authors WHERE name IS NOT NULL] as top_authors,
           user_messages,
           assistant_messages
    """
    try:
        results = neo4j_connector.execute_query(query, {"session_id": session_id})
        return results[0] if results else {}
    except Exception:
        logger.exception("获取会话分析失败: session_id=%s", session_id)
        return {}


def get_most_discussed_authors(session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """获取会话中讨论最多的学者。"""
    # 先检查 Session 是否存在
    check_query = """
    MATCH (s:Session {session_id: $session_id})
    RETURN count(s) as session_exists
    """
    try:
        check_result = neo4j_connector.execute_query(check_query, {"session_id": session_id})
        if not check_result or check_result[0].get("session_exists", 0) == 0:
            return []
    except Exception:
        return []

    query = """
    MATCH (s:Session {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[:REFERENCES]->(a:Author)
    WITH a.display_name as author_name, count(m) as mention_count
    ORDER BY mention_count DESC
    LIMIT $limit
    RETURN author_name, mention_count
    """
    try:
        return neo4j_connector.execute_query(query, {"session_id": session_id, "limit": limit})
    except Exception:
        logger.exception("获取热门学者失败: session_id=%s", session_id)
        return []


def extract_referenced_authors(message_content: str) -> List[str]:
    """从消息内容中提取可能提及的学者姓名。
    
    Args:
        message_content: 用户消息内容
        
    Returns:
        可能提及的学者姓名列表
    """
    import re
    author_patterns = [
        r'([A-Z][a-z]+\s+[A-Z][a-z]+)',
        r'([\u4e00-\u9fa5]{2,4})',
    ]
    
    authors = []
    for pattern in author_patterns:
        matches = re.findall(pattern, message_content)
        authors.extend(matches)
    
    return list(set(authors))[:10]


def resolve_references(session_id: str, message: str) -> str:
    """解析消息中的指代（如"他"、"这个学者"等），返回消解后的消息。
    
    Args:
        session_id: 会话ID
        message: 需要进行指代消解的消息
        
    Returns:
        消解后的消息
    """
    # 获取最近讨论的学者
    recent_authors = get_most_discussed_authors(session_id, limit=3)
    
    if not recent_authors:
        return message
    
    # 简单的指代消解规则
    resolved = message
    
    # 替换 "他" / "她" / "这个学者" 等为最近提到的学者
    reference_keywords = {
        '他': recent_authors[0]['author_name'],
        '她': recent_authors[0]['author_name'],
        '这个学者': recent_authors[0]['author_name'],
        '该学者': recent_authors[0]['author_name'],
    }
    
    for keyword, author_name in reference_keywords.items():
        if keyword in resolved:
            resolved = resolved.replace(keyword, author_name)
    
    return resolved
