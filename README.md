# 条款草案协作台（本地版）

面向**小型标准工作组**的条款草案 Web 应用：Python 标准库 + SQLite + 原生 HTML/CSS/JS，
**零第三方依赖**，Docker / Compose 一键启动。

## 功能与规则

- **建稿**：输入草案标题与初始条款；条款带**稳定编号**（编号唯一、删除即作废、不复用、不转挂）。
- **编辑提交**：编辑者选择自己的**所见版本（基线）**，对条款提交
  - **替换**：提交条款新正文；
  - **删除**：条款变为墓碑，编号作废；其上原有**意见保留原引用并标记失效**，绝不转挂邻条；
  - **意见**：附着于稳定编号，随条款展示。
- **并行改动合并（乐观并发）**
  - 同一基线上的并行改动若涉及**不同条款 → 自动合并、直接生效**；
  - 若**触及同一条款 → 双方内容都保留并进入“待决”**，后写**不会**覆盖前写；
  - 待决由编辑者在界面选择 **保留当前（keep）/ 采用提案（take）/ 填写合并文本（custom）** 结案；
    合并文本可以以原编号恢复被并行删除的条款。
- **版本与来源**：每次生效修订产生新草案版本（v1、v2…），界面提供
  当前草稿、逐条款**版本差异（unified diff）**、每次修订的**来源提案与编辑者**流水。
- **发布**：**待决未清不能发布**；发布生成**不可变快照**（仅含当时生效条款），
  后续编辑只影响草稿，已发布快照永不改变。
- **严格校验**：无效条款编号、不存在的草案/基线版本一律返回错误且**不改变草稿**。

## 一键启动（Docker Compose）

```bash
docker compose up -d --build
```

启动即自动完成：

1. 构建镜像（基于 `python:3.11-slim`，不安装第三方包）；
2. 创建持久化命名卷并在首次启动时幂等建表（数据初始化）；
3. 监听服务并开启健康检查。

访问地址：

- **界面**：<http://localhost:8000>
- **API 根**：<http://localhost:8000/api/drafts>

停止 / 清理：

```bash
docker compose down          # 停止并删除容器（数据卷保留）
docker compose down -v       # 同时删除数据库卷（清空全部数据）
```

> 端口 8000 被占用时，编辑 `docker-compose.yml` 的端口映射，例如 `"8080:8000"`，
> 然后访问 <http://localhost:8080>。

## 本地直接运行（无需 Docker，无需 pip）

要求 Python ≥ 3.9（仅用标准库）：

```bash
export CLAUSE_DB_PATH=./data/clauses.db
python3 -m app.server
# 打开 http://localhost:8000
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CLAUSE_HOST` | `0.0.0.0` | 监听地址 |
| `CLAUSE_PORT` | `8000` | 监听端口 |
| `CLAUSE_DB_PATH` | `/data/clauses.db` | SQLite 数据库文件路径，目录自动创建 |

## HTTP API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/drafts` | 创建草案（`title`、`author`、`clauses:[{stable_no, body}]`） |
| GET | `/api/drafts` | 草案列表 |
| GET | `/api/drafts/{id}` | 当前草稿全文（条款、意见、待决、历史流水） |
| POST | `/api/drafts/{id}/proposals` | 提交 `replace` / `delete` / `comment`（带 `baseline_version`、`stable_no`） |
| POST | `/api/drafts/{id}/proposals/{pid}/resolve` | 待决结案（`resolution=keep/take/custom`） |
| POST | `/api/drafts/{id}/publish` | 发布不可变快照（有待决返回 409） |
| GET | `/api/drafts/{id}/diff?from=0&to=2` | 两个版本的条款差异 |
| GET | `/api/drafts/{id}/revisions` | 逐次修订及来源 |
| GET | `/api/drafts/{id}/snapshots` / `…/snapshots/{sid}` | 快照列表 / 快照内容 |

错误统一形如：`{"error": "invalid_baseline", "message": "不存在的基线版本: 9（当前为 v2）"}`，
校验失败（422/404/409）均为只读返回，不会改动草稿。

## 目录结构

```
.
├── Dockerfile
├── docker-compose.yml
├── README.md
└── app
    ├── __init__.py
    ├── db.py          # SQLite 结构与连接（幂等初始化）
    ├── service.py     # 业务规则：提案、并发待决、发布快照、差异
    ├── server.py      # 标准库 HTTP 服务 + JSON API + 静态文件
    ├── entrypoint.sh
    └── static/        # 简洁前端（原生 HTML/CSS/JS，无构建步骤）
```

## 数据与并发说明

- SQLite WAL 模式，库文件在容器内 `/data/clauses.db`，由命名卷持久化；
- 服务为多线程 HTTP，所有写事务由进程内锁串行化，保证“后写不覆盖前写”；
- 面向小组内网使用；如需公网暴露请自行置于反向代理与鉴权之后。
