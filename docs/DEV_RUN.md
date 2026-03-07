# 本地联调：前后端运行说明

按以下步骤可运行整个项目的最新代码，用于前后端联调。

## 环境要求

- Python 3.12（项目使用 `py312` 虚拟环境）
- Node.js（zvt_ui 前端）
- 后端数据与配置依赖 `zvt-home`（如已配置）

## 1. 后端（zvt）

在**项目根目录**执行：

```bash
cd /path/to/zvt   # 进入 zvt 仓库根目录
PYTHONPATH=src ./py312/bin/python -m uvicorn zvt.zvt_server:app --host 0.0.0.0 --port 8090 --reload
```

- 后端地址：http://127.0.0.1:8090  
- API 文档：http://127.0.0.1:8090/docs  
- `--reload` 会监听代码变更并自动重启  

> 后端入口为 `zvt.zvt_server:app`，已包含 work、trading、misc 等路由。

## 2. 前端（zvt_ui）

在**新终端**中执行：

```bash
cd /path/to/zvt/zvt_ui

# 必须提高文件描述符限制，否则 EMFILE 会导致热更新失效
ulimit -n 10240
./node_modules/.bin/next dev
```

或使用项目内脚本（脚本内已包含 ulimit）：

```bash
cd zvt_ui && ./dev.sh
```

- 前端地址：http://localhost:3000  
- 交易页：http://localhost:3000/trade  
- 首页会重定向到 `/trade`  

## 3. 前端环境变量

确保 `zvt_ui/.env` 指向本地后端：

```env
NEXT_PUBLIC_SERVER=http://127.0.0.1:8090
```

修改 `.env` 后需重启前端（`npm run dev` / `./dev.sh`）。

## 4. 启动顺序与检查

1. 先启动**后端**，确认终端出现 `Application startup complete`。  
2. 再启动**前端**，确认出现 `Ready` 且无大量 `EMFILE` 报错。  
3. 浏览器打开 http://localhost:3000 ，应进入交易页并正常请求 8090 接口。  

## 5. 常见问题

| 现象 | 处理 |
|------|------|
| 前端无热更新 | 用 `ulimit -n 10240` 后重新执行 `next dev` 或使用 `./dev.sh` |
| 端口占用 | 先停掉占用 8090/3000 的进程再启动 |
| 前端 404 / 白屏 | 确认未使用 `output: 'export'` 的静态构建；开发时用 `next dev` |
| 接口请求失败 | 检查 `.env` 中 `NEXT_PUBLIC_SERVER` 是否为 `http://127.0.0.1:8090`，且后端已启动 |

## 6. 子模块与分支

- zvt_ui 为 git submodule，当前跟踪 **dev** 分支。  
- 更新子模块：`git submodule update --init --remote zvt_ui`  
- 克隆仓库时带子模块：`git clone --recurse-submodules https://github.com/zvtvz/zvt.git`  
