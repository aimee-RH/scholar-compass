# Scholar Compass 🎓

> **基于 GraphRAG 的学术导师发现系统**  
> 帮助申请者通过自然语言多轮对话，发现、分析和对比 Georgia Tech 的研究学者。

<!-- Demo Screenshot -->
<!-- ![Scholar Compass Demo](docs/demo.png) -->
<!-- 录完 demo 视频后替换上面这行 -->

---

## ✨ 核心特性

- **多轮对话**：跨轮次指代消解（"她的论文" → 自动关联上一轮选定的学者）
- **重名消歧**：4,903 个重名作者组，基于代表作 + 引用数交互式消歧
- **GraphRAG 混合检索**：BGE-M3 向量检索 + Neo4j 图结构过滤
- **智能路由**：单次 LLM 调用完成意图分类 + 指代消解 + 实体识别
- **导师推荐**：综合语义相关度、引用影响力、FWCI、近期活跃度的多维评分

---

## 🏗️ 系统架构

```
用户输入
   ↓
路由层（Claude Haiku + Pydantic v2）
   ├── 指代消解
   ├── 意图分类（FACTUAL / SEMANTIC / ANALYSIS / CLARIFICATION / COMPLEX）
   └── 实体识别 + Neo4j ID 解析
   ↓
LangGraph 状态机（5 节点）
   ├── clarification_handler  →  重名消歧 / 缺少上下文
   ├── factual_handler        →  模板化 Cypher + text2cypher fallback
   ├── semantic_handler       →  BGE-M3 向量检索
   ├── analysis_handler       →  研究轨迹 / 合作网络 / 学者对比 / 导师推荐
   └── fallback_handler       →  兜底
   ↓
Session 持久化（Neo4j Session 节点）
   ↓
回复用户
```

---

## 📊 数据规模

| 维度 | 数量 |
|------|------|
| 论文（Paper） | 45,009 篇 |
| 学者（Author） | 115,627 位 |
| 关系（AUTHORED） | 288,046 条 |
| 子领域（Subfield） | 231 个 |
| 论文向量维度 | 1,024 维（BGE-M3） |
| 向量覆盖率 | 100%（45,009/45,009） |

数据来源：[OpenAlex](https://openalex.org/)，Georgia Tech 机构学者。

---

## ⚡ 性能指标

| 场景 | 延迟 | 说明 |
|------|------|------|
| 消歧快速路径（"1"/"第二个"） | avg **5.4ms** | 正则匹配，0 次 LLM 调用 |
| 高频事实查询（模板命中） | **< 50ms** | 模板化 Cypher，0 次 LLM 调用 |
| 事实查询（text2cypher fallback） | ~1.7s | 1 次 LLM 调用 |
| 语义检索 | ~3-5s | BGE-M3 encode + 向量索引 |
| 路由层 LLM 调用次数 | **1 次** | 原架构 3 次，优化后合并 |

---

## 🧪 Benchmark 与公开复现

以下数字是早期项目记录。原始 32-case 数据集、运行结果和 Neo4j 数据库未随仓库提供，因此这些数字不能从公开仓库独立复现。仓库现在提供一个独立的 [5-case 起始回归集](eval/benchmark.json)，不附带预设成绩；它不能替代原始实验。配置好完整应用后，在 `webpage/` 目录运行 `python eval/run_eval.py`，结果写入 `eval/results/`。根目录的自动测试只检查预览、会话 API 和评估集格式，不调用外部模型或数据库。

### 历史记录（未在公开仓库复现）

32 个测试用例，覆盖 21 个 query 类别：

| 指标 | 结果 |
|------|------|
| 语义路由准确率（SEMANTIC_SEARCH） | **100%**（13/13） |
| 消歧意图识别（CLARIFICATION_NEEDED） | **100%**（8/8） |
| 复杂查询识别（COMPLEX） | **100%**（1/1） |
| 消歧快速路径延迟 p95 | **5.5ms** |
| Semantic retrieval avg top-1 score | **0.80**（范围 0.78-0.83） |
| LLM 路由意图准确率 | **100%** |
| 系统端到端准确率 | **75%**（含 post-resolution escalation） |

> **关于 75% 端到端准确率的说明**：  
> 7/10 的 FACTUAL_QUERY 被升级为 CLARIFICATION_NEEDED，原因是数据集中 10% 的学者存在重名，系统优先保证正确性（不返回错误学者的数据）而非精度。LLM 路由意图准确率仍为 100%，这是一个有意为之的 precision/correctness 权衡。

---

## 🔍 核心工程亮点

### 1. BGE-M3 检索退化诊断与修复

发现 CV 查询 top-50 中仅 1 篇相关论文。通过定量诊断（对比 CV 论文与对照组和 query 的余弦相似度分布），查阅 BGE-M3 原论文定位根因：**双塔不对称架构**的 query 侧需要 retrieval instruction prefix。

一行代码修复，无需重跑 45K 篇 embedding：

```python
# 修复前：query 直接 encode，向量和 document 不对齐
query_emb = model.encode(query_text)

# 修复后：加 retrieval instruction prefix
BGE_QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query: "
query_emb = model.encode(BGE_QUERY_INSTRUCTION + query_text)
```

**效果**：top-1 余弦相似度 0.33 → 0.82，CV 查询召回精度 2% → 100%。

---

### 2. 统一路由层（3 次 LLM 调用 → 1 次）

原架构将 Query Rewriter、Task Classifier、Entity Extractor 作为三个独立 LLM 调用串联。

合并后用 Pydantic v2 structured output 作为确定性兜底：

```python
class RoutingDecision(BaseModel):
    task_type: Literal["FACTUAL_QUERY", "SEMANTIC_SEARCH", "ANALYSIS",
                       "COMPLEX", "CLARIFICATION_NEEDED"]
    resolved_query: str          # 指代消解后的查询
    entities: EntitySet          # 识别出的实体
    ambiguity_reason: Optional[str]
    reasoning: str
```

解析失败自动重试，最终降级到 CLARIFICATION_NEEDED。

---

### 3. 消歧快速路径（1000× 加速）

```python
DISAMBIGUATION_PATTERNS = [
    r"^[1-9]$",           # "1", "2"
    r"^第[一二三四五]个?$",  # "第一个"
    r"^选?\s*[1-9]号?$",   # "选1"
    r"^(first|second)$",  # 英文
]
```

命中后直接从 session 取对应 candidate，**avg 5.4ms（vs ~5s LLM 基线，约 1000×）**，覆盖约 15-20% 的交互流量。

---

### 4. recommend_advisors 综合评分

```
composite_score =
  0.4 × normalized_semantic_similarity  （语义相关度）
+ 0.3 × normalized_citations            （引用影响力）
+ 0.2 × normalized_fwci                 （领域标准化影响力）
+ 0.1 × normalized_recency              （近期活跃度）
```

---

## 🛠️ 技术栈

| 层次 | 技术 |
|------|------|
| 图数据库 | Neo4j 5.15 Community |
| Embedding | BGE-M3（本地，MPS 加速） |
| 向量索引 | Neo4j 原生 HNSW（1024 维，cosine） |
| LLM | Claude Haiku 4.5（路由 + text2cypher） |
| Agent 框架 | LangGraph + LangChain |
| Schema 验证 | Pydantic v2 |
| 可观测性 | LangSmith |
| 后端 | Flask + Python 3.11 |
| 开发工具 | Claude Code |

---

## 🚀 本地运行

### 前置条件

- Python 3.9+
- Docker（运行 Neo4j）
- Anthropic API Key
- LangSmith API Key（可选）

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/aimee-RH/scholar-compass.git
cd 'scholar-compass/cse6242_project(frontend)/webpage'

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API keys

# 4. 启动 Neo4j
docker run -d \
  --name academic_graph \
  --restart unless-stopped \
  -p 7474:7474 -p 7688:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  -e 'NEO4J_PLUGINS=["apoc"]' \
  neo4j:5.15.0

# 5. 启动应用
python app.py
```

> **注意**：本地运行需要自行准备 Neo4j 数据（45K 论文 + embedding）。
> 完整数据导入流程见 `scripts/` 目录。

---

## 📁 项目结构

```
webpage/
├── graph/                    # LangGraph 核心层
│   ├── graph.py              # 主流程
│   ├── nodes.py              # 节点实现
│   ├── schemas.py            # Pydantic 契约
│   └── node_helpers/         # 路由辅助模块
├── tools/
│   └── _new/                 # 当前工具
│       ├── factual_query.py
│       ├── semantic_search.py
│       ├── analyze_author_trajectory.py
│       ├── analyze_collaboration.py
│       ├── compare_scholars.py
│       └── recommend_advisors.py
├── prompts/
│   └── routing_prompt.py     # 路由层 prompt
├── utils/
│   └── session_store.py      # Neo4j session 持久化
├── eval/
│   ├── benchmark.json        # 5-case 公开起始回归集
│   └── run_eval.py           # 评估脚本
└── scripts/                  # 数据导入 + 调试脚本
```

---

## 📝 开发日志

| 阶段 | 内容 |
|------|------|
| 数据层 | OpenAlex API 抓取 + Neo4j 图建模 + abstract 补全 |
| Embedding | BGE-M3 全量向量化（M4 MPS，2h51min）+ 调试 query instruction bug |
| 路由层 | 统一路由层设计 + Pydantic v2 结构化输出 + 消歧快速路径 |
| 工具层 | 6 个工具 + LangGraph 状态机接入 |
| 评估 | 公开起始回归集 + 历史 benchmark 记录 |

---

## 📄 License

MIT
