from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
import os

# 加载 .env 文件（必须在其他导入之前完成）
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[app.py] .env file loaded successfully")
except ImportError:
    print("[app.py] python-dotenv not installed, using system environment variables only")

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from graph.graph import graph_app
from prompts.system_prompt import SYSTEM_PROMPT
from typing import List, Dict, Any

# 导入记忆管理模块（已废弃，保留旧代码参考）
# from tools._deprecated_memory import (
#     create_session,
#     save_message,
#     get_conversation_history,
#     resolve_references,
#     extract_referenced_authors,
#     get_session_info
# )

# 导入事实校验模块（已废弃，保留旧代码参考）
# from tools._deprecated_fact_checker import fact_check_answer

# 临时占位符，用于兼容代码（4C 清理后这些功能暂时禁用）
def create_session(*args, **kwargs): return "deprecated_session"
def save_message(*args, **kwargs): pass
def get_conversation_history(*args, **kwargs): return []
def resolve_references(*args, **kwargs): return ""
def extract_referenced_authors(*args, **kwargs): return []
def get_session_info(*args, **kwargs): return {}
def fact_check_answer(*args, **kwargs): return (True, {"summary": "Fact check disabled (4C cleanup)"})

# 导入 Phase 1 & 2 新模块（保留文件，但 /api/chat 不再使用）
# from utils.query_rewriter import rewrite_query  # 步骤 4C: 路由层已集成，不再单独调用
# from utils.task_classifier import classify_query  # 步骤 4C: 路由层已集成，不再单独调用
import re

# 导入 Session 持久化模块（步骤 4C 新增）
from utils.session_store import load_routing_state, save_routing_state


# ========================================
# Response Cleaning - 移除 thinking 标签和格式化
# ========================================

def clean_agent_response(response_text: str) -> str:
    """
    清理 Agent 响应，移除任何残留的 thinking 标签或 markdown 格式。

    Args:
        response_text: 原始响应文本

    Returns:
        清理后的响应文本
    """
    if not response_text:
        return response_text

    # 移除 <thinking>...</thinking> 标签
    response_text = re.sub(r'<thinking>.*?</thinking>', '', response_text, flags=re.DOTALL | re.IGNORECASE)
    response_text = re.sub(r'<thinking>.*', '', response_text, flags=re.DOTALL | re.IGNORECASE)
    response_text = re.sub(r'</thinking>', '', response_text, flags=re.IGNORECASE)

    # 移除 ```thinking...``` 块
    response_text = re.sub(r'```thinking\s*\n.*?\n```', '', response_text, flags=re.DOTALL | re.IGNORECASE)
    response_text = re.sub(r'```thinking\s*\n.*', '', response_text, flags=re.DOTALL | re.IGNORECASE)

    # 移除 markdown 代码块（如果响应被包裹在 ``` 中）
    # 但保留内容（移除 ```json 或 ```text 开头和结尾）
    if response_text.strip().startswith('```') and '```' in response_text[3:]:
        # 提取代码块内容
        parts = response_text.split('```')
        for i, part in enumerate(parts):
            part = part.strip()
            if i > 0 and part:  # 跳过空的第一部分（开头 ``` 之前）
                # 检查是否是语言标识符
                first_line = part.split('\n')[0].lower()
                if first_line in ('json', 'text', 'markdown'):
                    # 移除语言标识符行
                    lines = part.split('\n')
                    content = '\n'.join(lines[1:]) if len(lines) > 1 else part
                    return content.strip()
                return part

    # 移除多余的空行
    response_text = re.sub(r'\n{3,}', '\n\n', response_text)

    return response_text.strip()


# ========================================
# Phase 3: 对比任务格式化函数
# ========================================

def format_comparison_table(
    response_text: str,
    referenced_scholars: List[str],
    agent_result: Dict
) -> str:
    """
    为对比任务生成格式化的表格输出

    Args:
        response_text: Agent 生成的原始回答
        referenced_scholars: 引用的学者列表
        agent_result: Agent 执行的完整结果（包含工具调用数据）

    Returns:
        格式化后的对比回答（包含表格）
    """
    try:
        # 尝试从 agent_result 中提取工具调用结果
        tool_calls = getattr(agent_result.get("messages", [])[-1], "tool_calls", None)

        if tool_calls:
            # 查找 dynamic_graph_retrieval 的调用结果
            for tool_call in tool_calls:
                if hasattr(tool_call, 'name') and tool_call.name == "dynamic_graph_retrieval":
                    # 获取工具返回的数据
                    if hasattr(tool_call, 'result'):
                        result_data = tool_call.result

                        # 如果结果包含结构化数据，尝试格式化为表格
                        if isinstance(result_data, str) and "|" in result_data:
                            # 已经是表格格式，直接使用
                            if "| 指标 |" in result_data or "| Metric |" in result_data:
                                return f"{response_text}\n\n{result_data}"

                        # 如果是 JSON 格式的数据，尝试解析并格式化
                        try:
                            import json
                            if result_data.strip().startswith("{"):
                                data = json.loads(result_data)
                                if "scholar_name" in str(data) or "name" in str(data):
                                    # 构建表格
                                    table = "\n\n### 📊 学者对比表\n\n"
                                    table += "| 指标 | " + " | ".join(referenced_scholars) + " |\n"
                                    table += "|" + "---|" * (len(referenced_scholars) + 1) + "\n"

                                    # 提取并显示关键指标
                                    metrics = ['论文数', 'paper_count', '总引用', 'total_citations', 'FWCI', 'fwci']
                                    for metric in metrics:
                                        if metric in str(data):
                                            # 添加行
                                            table += f"| {metric} |"
                                            for scholar in referenced_scholars:
                                                table += " (见详细数据) |"
                                            table += "\n"
                                            break

                                    return f"{response_text}{table}"
                        except:
                            pass

        # 如果无法提取结构化数据，返回原始回答
        return response_text

    except Exception as e:
        print(f"[Phase 3] 格式化对比表格失败: {str(e)}")
        return response_text


def extract_comparison_data_from_text(text: str) -> Dict:
    """
    从 Agent 回答中提取对比数据

    Args:
        text: Agent 的回答文本

    Returns:
        提取的对比数据字典
    """
    data = {
        "scholars": [],
        "metrics": {}
    }

    # 尝试提取学者信息
    # 简单实现：查找 "学者: 论文数" 等模式
    lines = text.split('\n')
    for line in lines:
        # 匹配 "Yao Xie: 78 papers" 这样的模式
        match = re.search(r'([A-Z][a-z]+\s+[A-Z][a-z]+):?\s*(\d+)\s*(papers?|篇|papers)', line)
        if match:
            scholar = match.group(1)
            papers = match.group(2)
            if scholar not in data["scholars"]:
                data["scholars"].append(scholar)
            data["metrics"].setdefault("paper_count", {})[scholar] = papers

    return data

app = Flask(__name__,
            static_folder='.',
            static_url_path='',
            template_folder='.')
CORS(app)

# Set API key for MiniMax (should be set from environment variable)
os.environ.setdefault("MINIMAX_API_KEY", "")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    """
    LangGraph Agent chat endpoint - 路由层集成版本 (步骤 4C)

    新架构：
    - Session management with routing state persistence
    - 路由层处理：指代消解 + 任务分类 + 实体识别（一体化）
    - 消歧快速路径（<100ms）
    - Neo4j Session 存储 resolved_entities 和 pending_clarification
    """
    try:
        data = request.json
        user_message = data.get('message', '')
        session_id = data.get('session_id')
        chat_history = data.get('history', [])

        print(f"\n{'='*60}")
        print(f"[API] /api/chat called (路由层集成版本)")
        print(f"[API] User message: {user_message[:50]}...")
        print(f"[API] Session ID: {session_id if session_id else 'None (will create)'}")
        print(f"{'='*60}\n")

        # === Step 1: 会话管理 ===
        if not session_id:
            session_id = create_session(user_id="default_user")
            print(f"[Memory] 创建新会话: {session_id[:8]}...")

        session_info = get_session_info(session_id)
        if not session_info:
            session_id = create_session(user_id="default_user")
            print(f"[Memory] Session无效，创建新会话: {session_id[:8]}...")

        # === Step 2: 从 Neo4j 加载 routing state ===
        print(f"[SessionStore] 加载 routing state...")
        routing_state = load_routing_state(session_id)
        resolved_entities = routing_state["resolved_entities"]
        pending_clarification = routing_state["pending_clarification"]
        print(f"[SessionStore] 已解析实体: {list(resolved_entities.keys())}")
        print(f"[SessionStore] 待消歧: {pending_clarification.entity_name if pending_clarification else 'None'}")

        # === Step 3: 获取对话历史（用于 LangGraph） ===
        if not chat_history:
            db_history = get_conversation_history(session_id, limit=10)
            if db_history:
                chat_history = [
                    {'role': msg['role'], 'content': msg['content']}
                    for msg in db_history
                ]
                print(f"[Memory] 加载 {len(chat_history)} 条历史消息")

        # 构建历史消息列表（LangChain 格式）
        history_messages = []
        for msg in chat_history:
            if msg['role'] == 'user':
                history_messages.append(HumanMessage(content=msg['content']))
            elif msg['role'] == 'assistant':
                history_messages.append(AIMessage(content=msg['content']))

        # === Step 4: 构建 LangGraph 初始 state ===
        state = {
            "messages": history_messages + [HumanMessage(content=user_message)],
            "routing_decision": None,
            "resolved_entities": resolved_entities,
            "pending_clarification": pending_clarification,
        }

        # === Step 5: 调用 LangGraph ===
        print(f"[Agent] 调用路由层...")
        print("-"*60)

        result = graph_app.invoke(state)

        # === Step 6: 保存更新后的 routing state ===
        new_resolved_entities = result.get("resolved_entities", {})
        new_pending_clarification = result.get("pending_clarification")

        save_routing_state(
            session_id=session_id,
            resolved_entities=new_resolved_entities,
            pending_clarification=new_pending_clarification,
        )
        print(f"[SessionStore] 保存 routing state: {len(new_resolved_entities)} entities")

        # === Step 7: 提取最终回复 ===
        ai_messages = [m for m in result["messages"] if m.type == "ai"]
        if not ai_messages:
            response_text = "⚠️ 系统未产出回复，请重试"
        else:
            response_text = ai_messages[-1].content

        print(f"[Agent] 回答: {response_text[:100]}...")

        # === Step 8: 清理响应 ===
        response_text = clean_agent_response(response_text)

        # === Step 9: 保存消息到 Neo4j ===
        save_message(session_id, user_message, "user")
        save_message(session_id, response_text, "assistant")
        print(f"[Memory] 消息已保存到会话: {session_id[:8]}...")

        # === Step 10: 事实校验（保留原有逻辑，从 routing_decision 提取实体） ===
        routing_decision = result.get("routing_decision")
        referenced_authors = []
        if routing_decision and routing_decision.entities:
            referenced_authors = [
                a.name for a in routing_decision.entities.authors
                if a.author_id  # 只取已解析的作者
            ]

        fact_check_enabled = os.getenv("ENABLE_FACT_CHECK", "true").lower() == "true"
        fact_check_result = None

        if fact_check_enabled and referenced_authors:
            try:
                print(f"[FactCheck] 开始事实校验...")
                is_factual, report = fact_check_answer(
                    answer=response_text,
                    referenced_scholars=referenced_authors,
                    strict_mode=False
                )

                fact_check_result = {
                    "is_factual": is_factual,
                    "summary": report.get("summary", ""),
                    "accuracy_rate": report.get("accuracy_rate", ""),
                    "verified_count": report.get("verified_count", 0),
                    "fact_count": report.get("fact_count", 0)
                }

                print(f"[FactCheck] 校验完成: {report.get('summary')}")

            except Exception as e:
                print(f"[FactCheck] 校验失败: {str(e)}")

        # === Step 11: 构建响应数据 ===
        # 提取路由层元数据
        query_type = routing_decision.task_type if routing_decision else "unknown"
        has_coreference = routing_decision.has_coreference if routing_decision else False

        # 构建推理信息块
        reasoning_blocks = []
        if routing_decision and routing_decision.reasoning:
            reasoning_blocks.append({
                "step": "routing_layer",
                "description": "路由层决策",
                "reasoning": routing_decision.reasoning,
                "details": {
                    "task_type": query_type,
                    "resolved_query": routing_decision.resolved_query,
                    "has_coreference": has_coreference,
                    "ambiguity_reason": routing_decision.ambiguity_reason,
                    "routing_confidence": routing_decision.routing_confidence
                }
            })

        # Fact Check 的 reasoning（如果有）
        if fact_check_result and fact_check_result.get('summary'):
            reasoning_blocks.append({
                "step": "fact_checking",
                "description": "事实校验",
                "reasoning": fact_check_result.get('summary'),
                "details": {
                    "is_factual": fact_check_result.get('is_factual'),
                    "accuracy_rate": fact_check_result.get('accuracy_rate'),
                    "verified_count": fact_check_result.get('verified_count'),
                    "fact_count": fact_check_result.get('fact_count')
                }
            })

        response_data = {
            'message': response_text,
            'success': True,
            'session_id': session_id,
            'tool_calls': None,  # 路由层不使用 LangChain tool_calls
            'query_type': query_type,
            'referenced_scholars': referenced_authors,
            'rewriter_info': {
                'has_coreference': has_coreference,
                'original_query': user_message,
                'rewritten_query': routing_decision.resolved_query if routing_decision else user_message
            } if has_coreference else None,
            'reasoning': reasoning_blocks if reasoning_blocks else None,
            # 新增：路由层元数据（供前端调试）
            '_routing_meta': {
                'task_type': query_type,
                'has_coreference': has_coreference,
                'resolved_entities_count': len(new_resolved_entities),
                'pending_clarification': new_pending_clarification.entity_name if new_pending_clarification else None
            }
        }

        if fact_check_result is not None:
            response_data['fact_check'] = fact_check_result

        return jsonify(response_data)

    except Exception as e:
        print(f"[Error] in /api/chat: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

@app.route('/api/history', methods=['GET'])
def get_history():
    """
    获取会话历史记录

    Query Parameters:
        - session_id: 会话ID（必需）
        - limit: 返回消息数量（可选，默认20）

    Returns:
        JSON响应，包含历史消息列表
    """
    try:
        session_id = request.args.get('session_id')
        limit = int(request.args.get('limit', 20))

        if not session_id:
            return jsonify({
                'error': 'session_id parameter is required',
                'success': False
            }), 400

        print(f"[API] /api/history called for session: {session_id[:8] if session_id else 'None'}...")

        # 从Neo4j获取历史
        history = get_conversation_history(session_id, limit=limit)

        # 格式化为前端需要的格式
        formatted_history = []
        for msg in history:
            formatted_history.append({
                'role': msg['role'],
                'content': msg['content'],
                'created_at': msg.get('created_at'),
                'referenced_authors': msg.get('referenced_authors', [])
            })

        print(f"[API] 返回 {len(formatted_history)} 条历史消息")

        return jsonify({
            'success': True,
            'history': formatted_history,
            'total': len(formatted_history)
        })

    except Exception as e:
        print(f"[Error] in /api/history: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

@app.route('/api/session', methods=['POST'])
def create_new_session():
    """
    创建新会话

    Body:
        - user_id: 用户ID（可选，默认"default_user"）
        - metadata: 会话元数据（可选）

    Returns:
        JSON响应，包含新创建的session_id
    """
    try:
        data = request.json
        user_id = data.get('user_id', 'default_user')
        metadata = data.get('metadata', {})

        session_id = create_session(user_id=user_id, metadata=metadata)

        print(f"[API] 创建新会话: {session_id[:8]}... (用户: {user_id})")

        return jsonify({
            'success': True,
            'session_id': session_id,
            'user_id': user_id
        })

    except Exception as e:
        print(f"[Error] in /api/session: {str(e)}")
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

@app.route('/api/session/<session_id>', methods=['GET'])
def get_session_details(session_id):
    """
    获取会话详情

    Parameters:
        - session_id: 会话ID

    Returns:
        JSON响应，包含会话信息
    """
    try:
        session_info = get_session_info(session_id)

        if not session_info:
            return jsonify({
                'error': 'Session not found',
                'success': False
            }), 404

        return jsonify({
            'success': True,
            'session': session_info
        })

    except Exception as e:
        print(f"[Error] in /api/session: {str(e)}")
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """
    系统健康检查端点

    Returns:
        JSON响应，包含各服务的健康状态
    """
    from datetime import datetime
    from tools import TOOLS

    health_status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0',
        'services': {}
    }

    # 检查Neo4j连接
    try:
        from tools.neo4j_connector import neo4j_connector
        neo4j_connector.execute_query("RETURN 1 as test")
        health_status['services']['neo4j'] = 'connected'
    except Exception as e:
        health_status['services']['neo4j'] = f'error: {str(e)}'
        health_status['status'] = 'degraded'

    # 检查LLM配置
    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        health_status['services']['llm'] = 'configured'
    else:
        health_status['services']['llm'] = 'missing_api_key'
        health_status['status'] = 'degraded'

    # 检查工具注册（新工具在 tools/_new/ 下，通过 handler 直接调用）
    health_status['services']['tools'] = 'routing_layer_active (4C cleanup)'

    # 检查功能模块（已废弃，保留健康检查字段）
    health_status['services']['memory'] = 'deprecated (4C cleanup)'
    health_status['services']['fact_checker'] = 'deprecated (4C cleanup)'

    # 根据状态返回相应的HTTP状态码
    status_code = 200 if health_status['status'] == 'healthy' else 503

    return jsonify(health_status), status_code

@app.errorhandler(404)
def not_found(error):
    """处理404错误"""
    return jsonify({
        'error': 'Endpoint not found',
        'success': False,
        'available_endpoints': [
            'GET  /',
            'POST /api/chat',
            'GET  /api/history',
            'POST /api/session',
            'GET  /api/session/<session_id>',
            'GET  /api/health'
        ]
    }), 404

@app.errorhandler(500)
def internal_error(error):
    """处理500错误"""
    return jsonify({
        'error': 'Internal server error',
        'success': False,
        'details': str(error) if os.getenv('DEBUG') == 'true' else 'Enable DEBUG mode for details'
    }), 500

if __name__ == '__main__':
    print("="*60)
    print("🚀 Starting Enhanced LangGraph Agent with Memory Support")
    print("="*60)
    print("✅ Features:")
    print("   - Dynamic Graph Retrieval (Text2Cypher)")
    print("   - Session Management (Neo4j)")
    print("   - Reference Resolution (他/这位学者 → 具体学者名)")
    print("   - Multi-turn Conversation")
    print("="*60)
    print("📱 Open your browser: http://localhost:5001")
    print("="*60)
    app.run(
        debug=os.getenv("FLASK_DEBUG") == "1",
        host="0.0.0.0",
        port=int(os.getenv("PORT", os.getenv("FLASK_PORT", "5001"))),
    )
