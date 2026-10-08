# 赛博鸡蛋 · ai-egg-monitor

监控大模型厂商公开活动，发现"赛博鸡蛋"——**免费额度、赠金、算力券、会员、折扣、内测、邀请奖励** 等。

每天北京时间 10:00 与 18:00 各跑一次，生成 Markdown 公告 + Atom / JSON Feed / RSS 订阅源，提交到本仓库并通过 GitHub Pages 对外发布。

> 本项目**只做提醒和资讯**：不自动领取，不登录，不绕过验证码，不存账号密码。
> 仓库公开，**禁止提交任何 secret / cookie / token / 账号密码**。

## 一、项目结构

```
ai-egg-monitor/
├─ .github/workflows/monitor.yml    # GitHub Actions 定时任务
├─ config/sources.yaml              # 全局配置 + 厂商列表（唯一需要改的地方）
├─ scripts/main.py                 # 抓取 + 过滤 + 去重 + 生成订阅
├─ scripts/state/egg_state.json     # 状态文件（去重用，提交到仓库）
├─ docs/
│  ├─ latest.md                    # 最新公告
│  ├─ history/YYYY-MM-DD-HH.md     # 历史归档
│  ├─ atom.xml                     # Atom 1.0 主 feed（主推）
│  ├─ feed.json                    # JSON Feed 1.1
│  ├─ rss.xml                      # RSS 2.0 兼容兜底
│  ├─ atom-<vendor>.xml            # 按厂商拆分 Atom
│  ├─ feed-<vendor>.json           # 按厂商拆分 JSON Feed
│  └─ subscribe.md                 # 订阅说明页
├─ requirements.txt
├─ README.md
└─ LICENSE
```

## 二、本地运行

```bash
pip install -r requirements.txt
python scripts/main.py
```

运行完成后会自动生成 / 更新 `docs/` 下的所有订阅文件与 `latest.md`，并把当次结果归档到 `docs/history/YYYY-MM-DD-HH.md`，状态写入 `scripts/state/egg_state.json`。

## 三、新增 / 修改厂商

只需编辑 `config/sources.yaml`，无需改代码：

```yaml
vendors:
  - name: 智谱 GLM
    enabled: true
    sources:
      - type: html
        url: https://open.bigmodel.cn/pricing
      - type: rss
        url: https://open.bigmodel.cn/news/rss
        keywords: [免费, 额度, 内测]   # 可选：覆盖默认关键词
```

支持两种 source 类型：

| type | 实现 | 说明 |
|------|------|------|
| `html` | requests + BeautifulSoup | 抓取页面标题和正文，关键词命中即视为"赛博鸡蛋"候选 |
| `rss`  | feedparser | 解析 RSS / Atom，每条 item 同样做关键词过滤 |

## 四、部署到 GitHub 公开仓库

1. `git init && git add . && git commit -m "init ai-egg-monitor"` 推送到 GitHub。
2. 仓库 **Settings → Actions → General → Workflow permissions** 选择 **Read and write**（确保 Actions 能 push 回仓库）。
3. 仓库 **Settings → Pages → Build and deployment**：
   - Source：**Deploy from a branch**
   - Branch：`main`，目录：`/docs`
4. 等待 Pages 部署，访问 `https://<user>.github.io/ai-egg-monitor/atom.xml` 应能看到 Atom feed。
5. （可选）添加 Secret `FEISHU_WEBHOOK` 用于推送飞书消息。

## 五、订阅地址

把 `<user>` 替换为 GitHub 用户名即可。

| 用途 | 链接 |
|------|------|
| **Atom 主 feed（主推）** | `https://<user>.github.io/ai-egg-monitor/atom.xml` |
| JSON Feed | `https://<user>.github.io/ai-egg-monitor/feed.json` |
| RSS 兼容兜底 | `https://<user>.github.io/ai-egg-monitor/rss.xml` |
| 按厂商 Atom | `https://<user>.github.io/ai-egg-monitor/atom-<vendor>.xml` |
| 按厂商 JSON Feed | `https://<user>.github.io/ai-egg-monitor/feed-<vendor>.json` |
| 订阅说明页 | `https://<user>.github.io/ai-egg-monitor/subscribe` |

- `<vendor>` 规则：厂商名小写、空格转 `-`。例如 `DeepSeek` → `atom-deepseek.xml`，`通义千问` → `atom-通义千问.xml`。
- **拉取式订阅**：阅读器按自身节奏拉取，不做 WebSub / 推送。

## 六、三种订阅格式适用场景

| 格式 | 适用场景 | 备注 |
|------|---------|------|
| **Atom 1.0** | 主推。IETF 标准（RFC 4287），主流阅读器全支持 | 优先使用 |
| JSON Feed 1.1 | 开发者、自建客户端、脚本消费 | 字段语义清晰 |
| RSS 2.0 | 兼容老旧阅读器 | 仅兜底，主推 Atom |

详见 `docs/subscribe.md`。

## 七、可选 Secret：FEISHU_WEBHOOK

如需把每次公告推送到飞书群机器人，在仓库 **Settings → Secrets and variables → Actions** 添加：

- Name：`FEISHU_WEBHOOK`
- Value：飞书自定义机器人 Webhook URL

`scripts/main.py` 检测到该环境变量时，会把 `docs/latest.md` 内容以 text 类型 POST 到飞书。没有则跳过。Webhook URL 不会出现在代码或日志中。

## 八、风险提示

- 本项目仅做信息提醒，**不自动领取**，**不登录**，**不绕过验证码 / 风控**，**不共享账号**。
- 涉及账号或充值的福利，请阅读厂商条款后再决定是否参与。
- "需登录确认"项仅表示活动规则位于登录墙后，我们不会尝试登录，保留链接供人工查看。

## 九、License

MIT，见 `LICENSE`。
