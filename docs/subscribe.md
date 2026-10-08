# 赛博鸡蛋 · 订阅说明

> ai-egg-monitor 只做"提醒与资讯"。订阅采用**拉取式**，不做 WebSub / PubSubHubbub，不做推送。
> 阅读器按照自身节奏（如 30 分钟、1 小时）来拉取本仓库 `/docs` 下的订阅文件即可。

本仓库 Pages 服务的根目录为 `/docs`，因此下列 URL 路径即订阅地址：

```
https://ikeng.github.io/model-egg-monitor/<文件名>
```

## 1. 主推 Atom 1.0

| 用途 | 链接 |
|------|------|
| 主 feed | `atom.xml` |
| 厂商 feed | `atom-<vendor>.xml`（vendor 名小写，空格转 `-`，中文原样保留） |

Atom 是 IETF 标准（RFC 4287），主流阅读器全支持，**这是本项目首推的订阅格式**。

示例：

```
https://ikeng.github.io/model-egg-monitor/atom.xml
https://ikeng.github.io/model-egg-monitor/atom-通义千问.xml
```

> 厂商 feed 仅在该厂商有命中记录时才会生成，文件名保留中文。
> 部分阅读器不接受 URL 里的非 ASCII 字符，可改用百分号编码形式：
> `https://ikeng.github.io/model-egg-monitor/atom-%E9%80%9A%E4%B9%89%E5%8D%83%E9%97%AE.xml`

## 2. JSON Feed 1.1（开发者推荐）

| 用途 | 链接 |
|------|------|
| 主 feed | `feed.json` |
| 厂商 feed | `feed-<vendor>.json` |

适合自建客户端、脚本消费、数据可视化，结构清晰、字段语义明确。

```
https://ikeng.github.io/model-egg-monitor/feed.json
https://ikeng.github.io/model-egg-monitor/feed-%E9%80%9A%E4%B9%89%E5%8D%83%E9%97%AE.json
```

命令行直接消费：

```bash
curl -s https://ikeng.github.io/model-egg-monitor/feed.json | jq -r '.items[] | "\(.title)  \(.url)"'
```

## 3. RSS 2.0（仅作兼容兜底）

| 用途 | 链接 |
|------|------|
| 主 feed | `rss.xml` |

> **注意**：RSS 2.0 仅用于兼容老旧阅读器，主推 Atom / JSON Feed。本项目不生成按厂商拆分的 RSS。

```
https://ikeng.github.io/model-egg-monitor/rss.xml
```

## 4. 三种格式适用场景

| 格式 | 适用场景 | 备注 |
|------|---------|------|
| Atom 1.0 | 主推。通用阅读器、IETF 标准、字段最完整 | 优先使用 |
| JSON Feed 1.1 | 开发者、自建客户端、脚本消费 | 字段语义清晰 |
| RSS 2.0 | 兼容老旧阅读器 | 仅兜底 |

## 5. 常见阅读器

- Feedly：https://feedly.com
- Inoreader：https://inoreader.com
- NetNewsWire (macOS / iOS)：https://netnewswire.com
- FreshRSS (自建)：https://freshrss.org
- Reeder (Apple 平台)
- Miniflux (自建轻量)：https://miniflux.app

## 6. 公告页面

- 最新公告：https://ikeng.github.io/model-egg-monitor/latest.md
- 历史归档：https://ikeng.github.io/model-egg-monitor/history/YYYY-MM-DD-HH.md

## 7. 风险提示

- 本项目仅做信息提醒，**不自动领取**，**不登录**，**不绕过验证码或风控**，**不共享账号**。
- 涉及账号 / 充值的福利请阅读厂商条款后再决定是否参与。
- "需登录确认"项仅表示该活动规则页面位于登录墙后，我们不会尝试登录，保留链接供人工查看。
