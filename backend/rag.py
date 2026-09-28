from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from neo4j import GraphDatabase
import time
import os
import json

app = Flask(__name__,
            static_folder='.',
            static_url_path='',
            template_folder='.')
CORS(app)

# --- 配置部分 ---

# OpenAI 客户端配置
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
)

# Neo4j 数据库配置 (请根据实际情况修改)
# 如果是在本地运行 Neo4j Desktop，通常是 bolt://localhost:7687
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")  # 请修改为你的数据库密码

# 初始化 Neo4j 驱动
driver = None
try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    print(f"[Backend] 成功连接到 Neo4j: {NEO4J_URI}")
except Exception as e:
    print(f"[Backend] Neo4j 连接失败: {e}")


def close_driver():
    if driver:
        driver.close()


# --- 数据库查询逻辑 ---

def run_cypher_query(query, params=None):
    """执行 Cypher 查询的通用辅助函数"""
    if not driver:
        print("[Error] 数据库驱动未连接")
        return []

    with driver.session() as session:
        result = session.run(query, params or {})
        return [record.data() for record in result]


def _retrieve_scholar_data(scholar_name: str) -> dict:
    """
    真正从 Neo4j 数据库检索学者数据。
    包含：
    1. 学者基本信息 (PageRank, ID)
    2. 可视化数据 (Visual Data): 合作网络, 主题演变
    3. RAG 上下文 (Retrieval Context): 关键论文, 核心合作者
    """
    print(f"[Backend] 正在从 Neo4j 检索 '{scholar_name}' 的数据...")
    start_time = time.time()

    # 1. 查找核心学者节点 (模糊匹配或精确匹配)
    # 假设节点标签为 :Author，属性为 name, pagerank
    find_author_query = """
    MATCH (a:Author)
    WHERE toLower(a.name) CONTAINS toLower($name)
    RETURN a.id as id, a.name as name, a.pagerank as pagerank
    ORDER BY a.pagerank DESC
    LIMIT 1
    """
    authors = run_cypher_query(find_author_query, {"name": scholar_name})

    if not authors:
        print(f"[Backend] 未找到学者: {scholar_name}")
        return {}

    target_author = authors[0]
    author_id = target_author['id']
    author_real_name = target_author['name']
    author_pagerank = target_author.get('pagerank', 0.0)

    # 2. 获取合作网络 (Collaboration Network)
    # 逻辑：查询该作者 -> 写过论文 -> 该论文的其他作者
    # 限制：取合作次数最多的前 10 位
    collaboration_query = """
    MATCH (a:Author {id: $id})-[:WROTE]->(p:Work)<-[:WROTE]-(co:Author)
    WHERE co.id <> $id
    WITH co, count(p) as strength
    ORDER BY strength DESC
    LIMIT 10
    RETURN co.name as name, co.pagerank as pagerank, strength
    """
    collaborators_data = run_cypher_query(collaboration_query, {"id": author_id})

    # 构建 visual_data -> collaboration_network 结构
    nodes = [{"id": author_real_name, "pagerank": author_pagerank, "group": 1}]
    edges = []

    # 用于 RAG 上下文的文本列表
    rag_collaborators = []

    for co in collaborators_data:
        nodes.append({
            "id": co['name'],
            "pagerank": co.get('pagerank', 0.5),
            "group": 2
        })
        edges.append({
            "source": author_real_name,
            "target": co['name'],
            "strength": co['strength']
        })
        rag_collaborators.append(f"{co['name']} ({co['strength']} papers)")

    # 3. 获取主题演变 (Topic Evolution)
    # 逻辑：作者 -> 论文 -> 关联的主题 (Concept/Topic)
    # 按年份和主题分组统计
    topic_query = """
    MATCH (a:Author {id: $id})-[:WROTE]->(p:Work)-[:HAS_TOPIC]->(t:Topic)
    WHERE p.publication_year IS NOT NULL
    WITH p.publication_year as year, t.display_name as topic, count(p) as papers
    ORDER BY year ASC, papers DESC
    RETURN year, topic, papers
    """
    # 注意：如果没有 :HAS_TOPIC 关系，可能需要根据实际 Schema 调整，比如 :DEALS_WITH
    raw_topics = run_cypher_query(topic_query, {"id": author_id})

    # 简单过滤：每年只取 Top 3 主题，防止数据量过大
    topic_evolution = []
    years_processed = {}  # {2019: 0, 2020: 0...} 计数器

    for row in raw_topics:
        y = row['year']
        if y not in years_processed:
            years_processed[y] = 0

        if years_processed[y] < 3:  # 每年最多保留前3个热门主题
            topic_evolution.append({
                "year": y,
                "topic": row['topic'],
                "papers": row['papers']
            })
            years_processed[y] += 1

    # 4. 获取关键论文 (Key Papers for RAG)
    # 逻辑：引用量最高的 5 篇论文
    papers_query = """
    MATCH (a:Author {id: $id})-[:WROTE]->(p:Work)
    RETURN p.display_name as title, p.abstract_inverted_index as abstract_raw, p.cited_by_count as citations
    ORDER BY p.cited_by_count DESC
    LIMIT 5
    """
    # OpenAlex 有时存储的是倒排索引摘要，这里假设已经处理或者是纯文本摘要
    # 如果数据库里没有 abstract 字段，可以用 title 代替
    papers_data = run_cypher_query(papers_query, {"id": author_id})

    rag_papers = []
    for p in papers_data:
        # 简化摘要处理，如果没有摘要则只用标题
        abstract_text = "Abstract not available"
        if p.get('abstract_raw'):
            # 这里略过倒排索引还原的复杂逻辑，实际中可能需要还原
            abstract_text = "Abstract content..."

        rag_papers.append(f"Paper: '{p['title']}' (Citations: {p.get('citations', 0)})")

    print(f"[Backend] 检索完成，耗时 {time.time() - start_time:.2f}s")

    # 组装最终数据结构
    return {
        "visual_data": {
            "collaboration_network": {
                "nodes": nodes,
                "edges": edges
            },
            "topic_evolution": topic_evolution
        },
        "retrieval_context": {
            "name": author_real_name,
            "pagerank": author_pagerank,
            "key_collaborators": rag_collaborators,
            "key_papers": rag_papers
        }
    }


def _build_rag_prompt(user_question: str, context: dict) -> str:
    """
    (私有函数) 增强 (Augment) 步骤。
    """
    print(f"[Backend] 正在为 '{user_question}' 构建RAG提示词...")

    system_prompt = """You are Scholar Compass, an AI assistant for advisor discovery.
Based *only* on the context provided below (retrieved from our academic graph database), answer the user's query.
If the context doesn't have the answer, admit it politely.
Be concise, professional, and factual.

--- CONTEXT START ---
"""

    context_str = f"Scholar Name: {context.get('name', 'N/A')}\n"
    context_str += f"Overall PageRank Impact: {context.get('pagerank', 'N/A')}\n"

    collabs = context.get('key_collaborators', [])
    if collabs:
        context_str += f"Top Collaborators: {', '.join(collabs[:8])}\n"  # 限制长度

    papers = context.get('key_papers', [])
    if papers:
        context_str += f"Key Papers:\n - " + "\n - ".join(papers) + "\n"

    final_prompt = f"{system_prompt}\n{context_str}\n--- CONTEXT END ---\n\nUser Query: {user_question}"

    return final_prompt


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/get_visuals', methods=['POST'])
def get_visuals():
    print("[Backend] /api/get_visuals 已被调用")
    try:
        data = request.json
        query = data.get('query')
        if not query:
            return jsonify({'error': 'No query provided'}), 400

        # 调用真实的检索函数
        scholar_data = _retrieve_scholar_data(query)

        # 检查是否为空（未找到学者）
        if not scholar_data:
            return jsonify({'error': 'Scholar not found in Neo4j database'}), 404

        visual_data = scholar_data.get('visual_data')

        return jsonify({
            'success': True,
            'visual_data': visual_data
        })

    except Exception as e:
        print(f"Error in /get_visuals: {str(e)}")
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/qanda', methods=['POST'])
def qanda():
    """
    智能问答接口 (Smart Endpoint)
    RAG 流程：检索(Neo4j) -> 增强 -> 生成(GPT-4o)
    """
    print("[Backend] /api/qanda 已被调用")
    try:
        data = request.json
        scholar_name = data.get('scholar_name')
        user_question = data.get('question')

        if not scholar_name or not user_question:
            return jsonify({'error': 'Fields "scholar_name" and "question" are required'}), 400

        # 1. 检索 (Retrieve)
        scholar_data = _retrieve_scholar_data(scholar_name)
        context = scholar_data.get('retrieval_context')

        if not context:
            return jsonify({
                'success': True,
                'llm_answer': "I'm sorry, I couldn't find detailed data for this scholar in our database to answer your question."
            })

        # 2. 增强 (Augment)
        prompt = _build_rag_prompt(user_question, context)

        # 3. 生成 (Generate)
        print(f"[Backend] 发送 Prompt 到 LLM...")
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful academic assistant named Scholar Compass."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        assistant_message = completion.choices[0].message.content
        print(f"[Backend] LLM 回复: {assistant_message[:50]}...")

        return jsonify({
            'success': True,
            'llm_answer': assistant_message
        })

    except Exception as e:
        print(f"Error in /qanda: {str(e)}")
        return jsonify({'error': str(e), 'success': False}), 500


if __name__ == '__main__':
    print("Starting Scholar Compass Backend with Neo4j Integration...")
    # 注册关闭数据库驱动的钩子（可选，视部署方式而定）
    try:
        app.run(debug=True, host='0.0.0.0', port=5001)
    finally:
        close_driver()