# AI Dungeon Master (AI-DM)

这是一个基于 D&D 5e 规则的 AI 地下城主系统。它能够主持游戏、生成剧情、绘制场景插画，并协助处理战斗规则。

## 🚀 快速开始

### 1. 环境准备

确保你已经安装了 Python 3.10+，然后安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 启动项目

使用以下命令启动 API 服务：

```bash
uvicorn app.main:app --reload
```

服务启动后，可以通过浏览器访问前端界面（默认端口为 8000）。

---

## 🧠 核心架构 (Core Architecture)

本项目采用了 **Modern Agent Architecture (LangGraph)**，实现了状态驱动的 AI 决策流：

### 1. 叙事 Agent (`Narrative Graph`)
- **位置**: `app/engine/agents/narrative.py`
- **架构**: `LoadContext -> Decision (LLM) -> ToolExecution -> Response`
- **功能**: 
  - 负责非战斗场景的剧情推进、NPC 扮演和环境描述。
  - 管理故事流向 (Story Graph) 和节点跳转。
  - **能力检定**: 独立的 Tool Node 处理力量、敏捷等属性检定 (D20 + 调整值)，实现“思考-行动-观察”循环。

### 2. 战斗 Agent (`Combat Graph`)
- **位置**: `app/engine/agents/combat.py`
- **架构**: `LoadContext -> Planner (LLM) -> Simulator (Code) -> Narrator (LLM)`
- **功能**:
  - **Planner**: 分析局势，生成结构化的攻防计划。
  - **Simulator**: 确定性的 Python 引擎执行攻击判定 (AC vs D20) 和伤害计算，保证规则准确性。
  - **Narrator**: 根据模拟结果生成生动的战斗解说。

### 3. 多语言支持 (i18n)
- **位置**: `app/engine/i18n.py`
- **功能**: 全面支持中文（简体）和英文。AI 的系统指令 (System Prompts) 和上下文描述会根据 Session 语言设置自动切换，确保沉浸式体验。

---

## 📚 RAG 组件与数据目录 (Catalog)

项目内置了强大的 RAG (检索增强生成) 系统，基于 Open5e 数据集构建。

- **位置**: `app/engine/catalog.py`
- **数据源**: `data/dnd_library/` (包含 JSONL, SQLite 和 lookup tables)

### 支持的知识库类型
系统支持以下 D&D 5e 资源的检索：
- **Monsters** (怪物) / **Spells** (法术) / **Equipment** (装备)
- **Classes** (职业) & **Races** (种族)
- **Conditions** (状态) & **Planes** (位面)

---

## 📂 项目结构摘要

```
/workspace/
  ├── app/
  │   ├── main.py            # FastAPI 入口
  │   ├── engine/            
  │   │   ├── agents/        # LangGraph Agents (Narrative, Combat)
  │   │   ├── ai_dm.py       # Wrapper for Narrative Agent
  │   │   ├── fight_agent.py # Wrapper for Combat Agent
  │   │   ├── state.py       # Graph State Definitions
  │   │   └── catalog.py     # RAG Tools
  │   └── services/          # 辅助服务 (PDF解析, 故事生成)
  ├── data/
  │   ├── dnd_library/       # Open5e 规则数据库
  │   ├── stories/           # 剧本与生成资源
  │   └── DnDcharacters/     # 玩家角色卡
  └── static/                # 前端页面
```

---

## 🗺️ Roadmap / 后续计划

我们将持续迭代项目，拓展 AI DM 的能力边界：

- [x] **多语言支持 (i18n)**
  - 全面支持中文/英文切换，适配 System Prompt 和战斗日志。
  
- [x] **重构现代 Agent 范式 (Modern Agent Architecture)**
  - 迁移至 **LangGraph**。
  - 实现结构化的 Planning -> Simulation -> Narration 战斗流。

- [ ] **多规则系统适配 (Multi-System Support)**
  - Pathfinder (1e/2e)
  - World of Darkness (nWoD / WoD)
  - 能够根据不同规则集切换底层判定逻辑。

- [ ] **增强长期记忆 (Long-term Memory)**
  - 引入向量数据库记录长期剧情关键点。
