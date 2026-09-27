# SteamDT 自动扫盘：裸K每日观察（不自动交易）

**不需要每天手动更新CSV。** GitHub Actions 每天使用仓库 Secret 中的 SteamDT API Key 拉取 `universe.csv` 中1,571个饰品的日K，扫描三种候选买点，提交 `reports/latest.md` 和 `reports/latest_candidates.csv`。原始API数据仅在本次 Actions 运行环境中临时保存，不上传至仓库。

## 一次性设置
1. 将这个项目根目录的内容放入你自己的 GitHub 仓库（包括隐藏的 `.github/workflows/daily.yml`）。**不需要上传原始历史CSV。**
2. 仓库 Settings → Secrets and variables → Actions → New repository secret，名称 `STEAMDT_API_KEY`，值为你自己的API Key。**不要把密钥发给别人或写入仓库。**
3. Settings → Actions → General → Workflow permissions，选择 Read and write permissions。若不想自动提交报告，可移除 workflow 的 Publish report 步骤，直接查看 Actions Summary/Artifact。
4. Actions 页面手动执行一次 `SteamDT 裸K每日观察`；之后每天自动运行。默认 UTC 02:25（日本时间11:25），可调整到日K完成更新之后。GitHub 计划任务可能延迟。

SteamDT 官方文档：K线接口 `POST /open/cs2/item/v1/kline`，请求头 `Authorization: Bearer <API_KEY>`；Body 含 `marketHashName`, `type`, `platform`；响应 `data` 每行按 `[更新时间戳,开盘指数,收盘指数,最高指数,最低指数]` 解析。K线接口额度文档标明每分钟120次；这里每次请求至少间隔0.66秒，并对临时错误重试。全量扫盘约需**至少17分钟**，实际视服务器和重试而定。接口**不提供成交数据**。

**重要：** 文档给出type=1..3，但未明确说明哪个type对应日K。本项目默认 `STEAMDT_KLINE_TYPE=1`，在首轮测试中请核实返回日期确实逐日；如果不是，设置仓库 Actions Variable `STEAMDT_KLINE_TYPE` 为正确的日K值再运行。解析器拒绝同一天多根K线。API返回数据的 `platform=ALL` 与你原始训练CSV的价格口径也须核实；若不一致，**不要把模型分数解释为已验证预测**。当API返回的历史少于60根、覆盖不足98%、同日重复或最新日覆盖不足80%时，流程失败而非发布不完整候选。

已随项目包含截至2026-09-22训练的7日研究模型。模型不自动重训；第2/3策略不依赖模型。使用模型预测依赖SteamDT返回的指数口径与原始训练文件一致。报告若距当前日本日期超过两天则标为过期。报告只是候选观察，**非买入建议**，无价差/手续费模拟。

运行前仅需配置密钥，**不要在聊天中提供Key**。本包未持有真实密钥，因此无法替你做实时API联调；首次运行后如响应格式与文档不同，可根据 Actions 错误日志调整解析器。
