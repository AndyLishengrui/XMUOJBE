# ops/ — 本站的运维脚本与算法教练代码

这些文件原本只存在于服务器磁盘上（`/opt/algo-coach/`、`~/xmuoj/scripts/`），
**没有任何版本控制**，一旦磁盘损坏或误删就全部丢失。2026-10-01 纳入本仓库做异地备份。

## 目录说明

### `ops/algo-coach/`
算法教练（AI 批改 WA 报告）的完整代码，运行在宿主机上：
- `app.py` — Flask API（**只绑 docker0 网关 `172.18.0.1:5000`**，不对公网开放）
- `worker.py` — 队列处理 worker（按题面取题面喂模型；**只在低峰窗口 00:00–08:00 跑**）
- `parallel_worker.py` — 多 worker 启动器（窗口外 `target=0`，一个 worker 都不留）
- `scanner.py` — 每 5 分钟扫出 WA≥3 的学生入队
- `models.py` / `monthly.py` / `verifier.py` / `reset_password.py`
- `start.sh` / `night_boost.sh` / `day_normal.sh` / `ai_daily_score.sh`

⚠️ **不随代码进库的运行时文件**（在本机 `/opt/algo-coach/`）：
`reports.db`（SQLite 报告库）、`*.log`、`.admin_token`（重置密码接口的令牌，600 权限）。

### `ops/scripts/`
- `health_check.sh` — 每小时自愈巡检（容器/nginx/判题机/卡死提交/低 AC 题扫描）
- `diagnose_problem.py` — 排查某道题的测试数据
- `restore_dramatiq.sh` — dramatiq 守护
- `auto_tag.sh` — 自动打标签

## ⚠️ 改这些文件的两个约定

1. **同步到服务器**：改完要 `sudo cp` 回 `/opt/algo-coach/`（或 `~/xmuoj/scripts/`），
   算法教练的进程才用得到；`worker.py` 改了要等下次窗口开启、或重启 launcher 才生效。
2. **别把密钥写回代码**：`app.py` 的 `/reset_password` 曾硬编码兜底 token `xmuoj2026`，
   而该接口能把任意学生密码重置成 123456 —— 端口当时还对全网开放。
   现已改为 **fail-closed**（`COACH_ADMIN_TOKEN` 环境变量 → `/opt/algo-coach/.admin_token`
   → 都没有则一律 403）。
