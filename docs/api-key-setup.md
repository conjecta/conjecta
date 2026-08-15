# API Key Setup / API 密钥配置

Conjecta's hosted Web app accepts an OpenAI-compatible API endpoint supplied by each signed-in user. The user provides a Base URL and API Key; Conjecta always requests `gpt-5.6-sol` and does not offer a provider or model selector for this flow.

Conjecta 托管版 Web 应用允许登录用户绑定自己的 OpenAI 兼容接口。用户只需提供 Base URL 和 API Key；此流程固定请求 `gpt-5.6-sol`，不提供平台或模型选择。

Last verified: 2026-08-12

## Web UI / 网页端

1. Open Conjecta and sign in.
2. Open **用量与 API Key** from the user menu.
3. Enter the provider's OpenAI-compatible Base URL, for example `https://provider.example/v1`.
4. Enter the matching API Key and click **保存**.
5. Submit a problem. Requests made with this endpoint use the user's provider quota instead of Conjecta's free quota.

中文步骤：

1. 打开 Conjecta 并登录。
2. 在用户菜单中打开 **用量与 API Key**。
3. 填写服务商提供的 OpenAI 兼容 Base URL，例如 `https://provider.example/v1`。
4. 填写对应 API Key，点击 **保存**。
5. 提交数学问题。绑定接口后的请求使用用户自己的服务商额度，不占用 Conjecta 免费额度。

The Base URL must be a public HTTPS address. URLs containing credentials, query strings, fragments, or hosts resolving to private, loopback, link-local, multicast, or reserved addresses are rejected. A trailing slash is removed when the configuration is saved.

Base URL 必须是公网 HTTPS 地址。系统会拒绝包含内嵌账号密码、查询参数、片段，以及解析到私网、回环、链路本地、多播或保留地址的 URL；保存时会移除末尾斜杠。

## Storage And Migration / 存储与迁移

- The Base URL and API Key are encrypted together with AES-256-GCM and stored in the signed-in user's Supabase `conjecta_users.api_keys_encrypted` field.
- The API Key is never returned by the read API. The UI receives only the Base URL, fixed model, update time, and whether the configuration must be rebound.
- Legacy provider-based records remain decryptable, but cannot be used for a solve. Their owners must enter a Base URL and API Key again.
- Deleting the binding clears both the encrypted value and its update timestamp.

- Base URL 与 API Key 使用 AES-256-GCM 一起加密，并保存到当前登录用户的 Supabase `conjecta_users.api_keys_encrypted` 字段。
- 查询接口不会返回 API Key。网页端只能读取 Base URL、固定模型、更新时间和是否需要重新绑定。
- 旧版按平台保存的记录仍可解密，但不能用于求解，用户必须重新填写 Base URL 和 API Key。
- 删除绑定会清除加密值及更新时间。

The deployment must set `CONJECTA_API_KEY_ENCRYPTION_KEY` to a URL-safe base64-encoded 32-byte key. Do not rotate it without a data migration, because existing user bindings would become unreadable.

部署环境必须把 `CONJECTA_API_KEY_ENCRYPTION_KEY` 设置为 32 字节密钥的 URL-safe Base64 编码。没有数据迁移时不要直接轮换该密钥，否则已有绑定将无法解密。

## CLI And Server Configuration / CLI 与服务端配置

The user binding above is separate from the operator-managed backend configuration used by the CLI and by Conjecta's platform-funded free quota. Server operators should continue configuring those providers through `config.toml` and environment variables such as `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or `SHENGSUANYUN_API_KEY` as appropriate.

上述用户绑定与 CLI、平台免费额度使用的服务端配置相互独立。服务端运营者仍应按 `config.toml` 中的 provider 配置对应的环境变量，例如 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或 `SHENGSUANYUN_API_KEY`。

## Security / 安全

- Never commit API keys or paste them into chat, issues, screenshots, or logs.
- Use a dedicated, limited provider key so it can be revoked independently.
- Conjecta validates the Base URL when saving and again before each solve. Production network egress controls remain the final defense against DNS rebinding.
- If a key has been exposed, revoke it at the provider immediately and create a replacement.

## Troubleshooting / 故障排查

| Symptom / 现象 | Action / 处理 |
|---|---|
| Base URL is rejected | Use the provider's public HTTPS OpenAI-compatible API root without credentials, query parameters, or fragments. |
| Configuration requires rebinding / 提示重新绑定 | Open **用量与 API Key** and enter both the Base URL and API Key again. |
| `401` from the provider | Check that the key belongs to this endpoint and is active. |
| Model not found / 模型不存在 | Confirm that the endpoint exposes the exact model ID `gpt-5.6-sol`. |
| `429` or quota error | Check the provider account's balance, rate limits, and usage limits. |
