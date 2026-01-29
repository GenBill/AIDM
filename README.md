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

## 🧠 核心能力 (Skills)

本项目采用了多 Agent 协作的架构，主要包含以下核心组件：

### 1. 叙事与扮演 (Narrative DM)
- **位置**: `app/engine/ai_dm.py`
- **功能**: 
  - 负责非战斗场景的剧情推进、NPC 扮演和环境描述。
  - 管理故事流向 (Story Graph) 和节点跳转。
  - **能力检定 (Ability Checks)**: 处理力量、敏捷、智力等非战斗属性检定 (D20 + 调整值)。
  - **艺术生成 (GenAI)**: 集成 Google GenAI，根据当前场景动态生成遭遇战插画。

### 2. 战斗系统 (Combat Engine)
- **位置**: `app/engine/combat.py`, `app/engine/fight_agent.py`
- **功能**:
  - 提供确定性的骰子系统 (`roll_dice`)，支持复杂的骰子表达式。
  - **状态管理**: 追踪角色和怪物的 HP、临时 HP 和异常状态 (Conditions)。
  - **攻击解析**: 原子化的 `resolve_attack` 函数，处理命中判定 (AC vs D20) 和伤害计算。
  - **战斗状态持久化**: 将战斗快照保存到本地 JSON 数据库。

### 3. 规则助手 (Rule Assistant)
- **位置**: `app/engine/agent_workflow.py`
- **功能**:
  - 一个基于 ReACT 架构的智能体，专门回答 D&D 5e 规则和设定问题。
  - 能够自主调用 RAG 工具查询本地资料库，提供准确的规则解释。

---

## 📚 RAG 组件与数据目录 (Catalog)

项目内置了强大的 RAG (检索增强生成) 系统，基于 Open5e 数据集构建。

- **位置**: `app/engine/catalog.py`
- **数据源**: `data/dnd_library/` (包含 JSONL, SQLite 和 lookup tables)

### 支持的知识库类型
系统支持以下 D&D 5e 资源的检索：
- **Monsters** (怪物)
- **Spells** (法术)
- **Equipment** (装备)
- **Classes** (职业) & **Races** (种族)
- **Backgrounds** (背景) & **Feats** (专长)
- **Conditions** (状态) & **Planes** (位面)
- **Documents** (规则文档 SRD)

### 检索工具 (Tools)
Agent 使用以下工具来获取知识：

1. **`look_table(type, query)`**
   - 模糊搜索名称。例如：查找名字中包含 "Goblin" 的所有怪物。
   
2. **`search_table(type, name_or_slug)`**
   - 精确解析。在 SQLite 或 JSONL 中定位唯一条目，获取 API URL 或摘要。
   
3. **`fetch_and_cache(type, slug)`**
   - 获取详情。从 Open5e API 拉取完整 JSON 数据并缓存到本地 (`data/dnd_library/cache/`)，避免重复请求。

---

## 📂 项目结构摘要

```
/workspace/
  ├── app/
  │   ├── main.py            # FastAPI 入口
  │   ├── engine/            # 核心逻辑 (AI DM, Combat, Catalog)
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
  - 增加对中文的全面支持（界面与叙事）。
  
- [ ] **多规则系统适配 (Multi-System Support)**
  - Pathfinder (1e/2e)
  - World of Darkness (nWoD / WoD)
  - 能够根据不同规则集切换底层判定逻辑。

- [ ] **重构现代 Agent 范式 (Modern Agent Architecture)**
  - 引入更先进的 Planning 能力 (Plan-and-Solve)。
  - 增强长期记忆 (Long-term Memory) 与上下文管理。
  - 支持多模态输入/输出（语音交互、实时地图操作）。
