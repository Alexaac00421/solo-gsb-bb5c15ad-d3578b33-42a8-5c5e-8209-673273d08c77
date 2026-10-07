# 条款草案协作系统

面向小型标准工作组的本地可运行 Web 应用：创建带稳定编号条款的草案，编辑者基于所见版本提交条款替换 / 删除 / 意见，系统自动合并并行改动、管理冲突待决、生成不可变发布快照。

- 后端：Python 3 + Flask + SQLite（无外部服务依赖）
- 前端：原生 HTML/CSS/JS 单页，无需构建
- 交付：Dockerfile + Compose 一键启动，依赖与数据初始化全部自动完成

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后访问：**http://localhost:8000**

首次启动自动完成：安装依赖（镜像构建时）→ 创建 SQLite 库表 → 写入一个演示草案（含示例条款与意见）。数据持久化在名为 `clause_data` 的卷中。

## 本地运行（不用 Docker）

```bash
pip install -r requirements.txt
python -m app.server          # 开发方式
# 或：gunicorn --workers 2 --bind 0.0.0.0:8000 app.server:app
```

同样访问 http://localhost:8000 。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_PORT` | `8000` | 服务监听端口（Compose 映射为 `8000:8000`） |
| `APP_DB` | 容器内 `/data/app.db`；本地 `./data/app.db` | SQLite 数据库文件路径 |
| `SEED_DEMO` | `1` | 数据库为空时写入演示草案；设为 `0` 关闭 |

## 使用流程

1. **新建草案**：左侧「＋ 新建草案」，输入标题和若干带稳定编号（如 `1`、`2.1`、`3.1.4`）的条款，生成 v1。
2. **提交修订**：在「当前草稿」页填写基线版本（默认为当前版本）、操作（替换/删除）、条款编号与新文本。
3. **并行合并规则**：
   - 基线即当前版本 → 直接生效；
   - 基线之后该条款未被他人改动（并行触及不同条款）→ 自动合并；
   - 同一基线上他人已改同一条款 → 进入「待决冲突」，**不会后写覆盖前写**。
4. **冲突结案**：在「待决冲突」页对照基线 / 当前 / 提案三方内容，选择「保留当前」「采用提案」或填写合并文本后结案。
5. **意见**：在「意见」页按稳定编号提交；条款被删除后，其意见保留原编号并标记「已失效」，不会转挂邻条。
6. **版本差异与来源**：「版本与差异」页列出每个版本的作者、说明与来源（初始创建 / 提案 #N / 冲突结案 #N），任选两版本查看条款级差异。
7. **发布**：待决冲突清零后点击「发布快照」，生成不可变快照；后续编辑只影响草稿，不影响已发布快照。

无效条款编号、不存在的基线版本等请求会返回错误且**不改变草稿**。

## HTTP API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/drafts` | 草案列表 / 创建草案 |
| GET | `/api/drafts/<id>` | 当前草稿（含当前版本号与待决数） |
| GET | `/api/drafts/<id>/versions[/<n>]` | 版本历史（含来源）/ 某版本内容 |
| GET | `/api/drafts/<id>/diff?from=A&to=B` | 两版本条款级差异 |
| GET/POST | `/api/drafts/<id>/proposals` | 提案列表 / 提交修订（replace/delete） |
| POST | `/api/drafts/<id>/proposals/<pid>/resolve` | 冲突结案（ours/theirs/merged） |
| GET/POST | `/api/drafts/<id>/comments` | 意见列表 / 提交意见 |
| POST | `/api/drafts/<id>/publish` | 发布不可变快照（有待决则 409） |
| GET | `/api/drafts/<id>/publications[/<pid>]` | 快照列表 / 快照内容 |

## 测试

```bash
python3 tests/smoke_test.py   # 43 项端到端检查：合并、冲突、结案、意见失效、发布不可变、错误处理
```

## 目录结构

```
app/
  server.py      Flask 路由与启动（含建库、演示数据初始化）
  logic.py       核心业务：三方合并、冲突待决、意见失效、发布快照
  db.py          SQLite 连接与建表
  static/        单页前端（index.html / app.js / style.css）
tests/smoke_test.py
Dockerfile  docker-compose.yml  requirements.txt
```
