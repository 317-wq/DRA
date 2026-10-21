# MVP-1.9 · BYOK 首次配置

> 基线：PROJECT.md v1.18
> 前置版本：MVP-1.8.1
> 日期：2026-10-21
> 状态：已完成（126 个离线单测；设置页与首次提示已截图验证）

## 1. 本版范围

用户下载后需要自己准备两个 Key。本版把「配起来」这件事从命令行搬进界面。

**包含什么**：

1. **服务商预设（9 个）**：通义/DashScope、OpenAI、DeepSeek、Kimi、智谱、OpenRouter、SiliconFlow、Ollama、自定义。
2. **模型发现**：请求服务商 `/models` 动态拉取；失败时保留预设模型并允许手填。
3. **测试连接**：发一条最小 chat 请求，返回模型、延迟与回复。
4. **设置页**：服务商、Base URL、API Key、模型、Tavily Key、质量阈值；保存后热加载。
5. **首次配置提示**：缺少模型 Key 或搜索 Key 时，顶部显示配置提示与「去配置」。
6. **空 Key 保护**：设置页留空不会清掉已保存的 Key。
7. **深链接**：`?view=settings` 直接打开设置。

**不包含什么**：

- 登录 / 共享额度（SaaS 代理）
- 系统钥匙串（当前仍是本机 `config.toml`，权限 0600）
- 多搜索源（目前 Tavily）
- 多模型角色分别配置（plan/analyze/write 仍走同一模型）

## 2. 接口

- `GET /api/providers`：预设列表（id、name、base_url、models、note）
- `POST /api/providers/models`：`{base_url, api_key?}` → 模型列表
- `POST /api/providers/test`：`{base_url, api_key?, model}` → `{ok, model, latency_ms, reply}`
- `GET /api/settings`：新增 `api_key_set` / `tavily_key_set`
- `PUT /api/settings`：忽略空字符串，避免清空已有 Key

## 3. 验收

- [x] 设置页能选服务商并自动填 Base URL
- [x] 能拉取模型列表（失败有可读错误）
- [x] 能测试连接并显示延迟
- [x] 保存后立即生效，不需要重启
- [x] 缺 Key 时首页显示配置提示
- [x] 空 Key 保存不会清空已有配置
- [x] 126 个离线单测通过

## 4. 风险与待办

- **系统钥匙串**：Key 仍以明文存 `config.toml`（0600）；下一步接 Windows Credential Manager / macOS Keychain。
- **Anthropic 原生 API** 不是 OpenAI 兼容格式，需要单独适配或走 OpenRouter。
- **模型名会过时**：预设只是示例，以动态拉取为准。
- **桌面 Tk 向导**：便携版首次启动仍是简化表单，没有服务商下拉与模型拉取；网页设置页功能更全。
- **SSRF**：本地用户可填任意 Base URL（Ollama 需要 localhost），暂不做白名单。
