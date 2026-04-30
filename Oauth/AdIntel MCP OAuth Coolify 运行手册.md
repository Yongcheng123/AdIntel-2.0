# AdIntel MCP OAuth + Coolify 运行手册

本文档记录当前 AdIntel MCP 在 Coolify 上启用 Google OAuth 的真实配置、验证方式和以后维护时的操作步骤。

不要把真实的 `COOLIFY_TOKEN`、`GOOGLE_CLIENT_SECRET`、数据库连接串或 MCP API Key 写进仓库。需要用到密钥时，从本机环境变量或 Coolify Secret/Environment Variables 中读取。

---

## 1. 当前线上信息

| 项目 | 值 |
|---|---|
| Coolify URL | `https://coolify.tonob.net` |
| GitHub Repo | `https://github.com/Yongcheng123/AdIntel-2.0.git` |
| Branch | `main` |
| App Name | `adintel-mcp` |
| App UUID | `ows0k08gkgg04co8ck084004` |
| Project UUID | `ukkkcowgwssoswg88kc08ocs` |
| Environment UUID | `wccoogccw040c4woco0w8oc0` |
| Server UUID | `lks0ksccsc808c48kcosgcww` |
| Destination UUID | `s40ogcw444kkg0o0wg0oso40` |
| Public Domain | `https://adintel-mcp.3.15.29.33.sslip.io` |
| Container Port | `7860` |
| Health Endpoint | `https://adintel-mcp.3.15.29.33.sslip.io/health` |
| MCP Endpoint | `https://adintel-mcp.3.15.29.33.sslip.io/mcp` |
| Google Redirect URI | `https://adintel-mcp.3.15.29.33.sslip.io/auth/google/callback` |
| Allowed Google Domain | `feedmob.com` |

---

## 2. 代码结构

当前项目不是 Node/TypeScript MCP，而是 Python MCP。

关键文件：

- `src/adintel/mcp/auth.py`
  - Google OAuth provider。
  - 负责跳转 Google 登录、处理 callback、验证 `hd` 和邮箱域名、签发 MCP Bearer token。
- `src/adintel/mcp/server.py`
  - 创建 `FastMCP` server。
  - 当 `BASE_URL`、`GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` 都存在时启用 OAuth。
  - OAuth 模式下 MCP endpoint 是 `/mcp`。
  - 提供公开 `/health`。
- `hf_space.py`
  - Coolify 使用的 ASGI entrypoint。
  - 旧的 `MCP_API_KEY` gate 会在 OAuth 模式下自动绕过。
  - `/health` 永远公开，方便 Coolify 健康检查。
- `Dockerfile`
  - Python 3.12 slim。
  - 安装 `curl`，让 Coolify health check 更稳。
  - 暴露 `7860`。

---

## 3. Coolify 环境变量

必需：

```text
ADINTEL_DATABASE_URL=<postgres-url>
BASE_URL=https://adintel-mcp.3.15.29.33.sslip.io
GOOGLE_CLIENT_ID=<google-oauth-client-id>
GOOGLE_CLIENT_SECRET=<google-oauth-client-secret>
ALLOWED_DOMAIN=feedmob.com
```

可选：

```text
OAUTH_ACCESS_TOKEN_TTL_SECONDS=3600
OAUTH_REFRESH_TOKEN_TTL_SECONDS=2592000
```

旧配置：

```text
MCP_API_KEY=<legacy-api-key>
```

说明：

- OAuth 环境变量存在时，线上 `/health` 会返回 `"auth":"oauth"`。
- OAuth 环境变量不存在时，会回退到旧的 API key 模式，线上 `/health` 会返回 `"auth":"api_key"`。
- 不建议设置 `ADINTEL_MCP_PATH`，让代码自动决定路径即可。OAuth 模式自动使用 `/mcp`。

---

## 4. Google Cloud Console 配置

Google Cloud 项目：

```text
Project ID: graceful-ratio-290821
```

OAuth Consent Screen：

- Audience: `External`
- App name: `AdIntel MCP`
- Scopes: `openid`, `email`
- Domain restriction 不靠 Google Console 完成，而是在服务端用 `ALLOWED_DOMAIN=feedmob.com` 检查 `hd` 和 email suffix。

OAuth Client：

- Type: `Web application`
- Authorized redirect URI:

```text
https://adintel-mcp.3.15.29.33.sslip.io/auth/google/callback
```

拿到新的 `Client ID` / `Client Secret` 后，不要写进文档或 Git。只放到 Coolify 环境变量。

---

## 5. 常用 Coolify API 命令

先在本机 shell 设置 token：

```bash
export COOLIFY_URL="https://coolify.tonob.net"
export COOLIFY_TOKEN="<your-coolify-api-token>"
export APP_UUID="ows0k08gkgg04co8ck084004"
```

查看项目：

```bash
curl -fsS -H "Authorization: Bearer $COOLIFY_TOKEN" \
  "$COOLIFY_URL/api/v1/projects" | python3 -m json.tool
```

查看 app 状态：

```bash
curl -fsS -H "Authorization: Bearer $COOLIFY_TOKEN" \
  "$COOLIFY_URL/api/v1/applications/$APP_UUID" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d.get("last_online_at"))'
```

查看 app 环境变量 key：

```bash
curl -fsS -H "Authorization: Bearer $COOLIFY_TOKEN" \
  "$COOLIFY_URL/api/v1/applications/$APP_UUID/envs" \
  | python3 -c 'import json,sys; print("\n".join(sorted(e["key"] for e in json.load(sys.stdin))))'
```

添加或更新环境变量：

```bash
curl -fsS -X POST "$COOLIFY_URL/api/v1/applications/$APP_UUID/envs" \
  -H "Authorization: Bearer $COOLIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"BASE_URL","value":"https://adintel-mcp.3.15.29.33.sslip.io"}'
```

启用 health check：

```bash
curl -fsS -X PATCH "$COOLIFY_URL/api/v1/applications/$APP_UUID" \
  -H "Authorization: Bearer $COOLIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"health_check_enabled":true,"health_check_path":"/health","health_check_port":"7860"}'
```

触发重新部署：

```bash
curl -fsS -X POST "$COOLIFY_URL/api/v1/applications/$APP_UUID/start?force=true" \
  -H "Authorization: Bearer $COOLIFY_TOKEN"
```

---

## 6. 部署后验证

检查 health：

```bash
curl -fsS https://adintel-mcp.3.15.29.33.sslip.io/health
```

期望 OAuth 模式输出：

```json
{"status":"ok","auth":"oauth"}
```

检查 OAuth Authorization Server metadata：

```bash
curl -fsS \
  https://adintel-mcp.3.15.29.33.sslip.io/.well-known/oauth-authorization-server \
  | python3 -m json.tool
```

关键字段应包含：

```json
{
  "issuer": "https://adintel-mcp.3.15.29.33.sslip.io/",
  "authorization_endpoint": "https://adintel-mcp.3.15.29.33.sslip.io/authorize",
  "token_endpoint": "https://adintel-mcp.3.15.29.33.sslip.io/token",
  "registration_endpoint": "https://adintel-mcp.3.15.29.33.sslip.io/register",
  "scopes_supported": ["mcp"]
}
```

检查 MCP endpoint 是否被保护：

```bash
curl -i -sS https://adintel-mcp.3.15.29.33.sslip.io/mcp | sed -n '1,40p'
```

期望：

- HTTP `401`
- `www-authenticate` header 中包含 Bearer challenge
- response body 类似：

```json
{"error":"invalid_token","error_description":"Authentication required"}
```

检查 Protected Resource metadata：

```bash
curl -fsS \
  https://adintel-mcp.3.15.29.33.sslip.io/.well-known/oauth-protected-resource/mcp \
  | python3 -m json.tool
```

期望 resource 是：

```text
https://adintel-mcp.3.15.29.33.sslip.io/mcp
```

---

## 7. MCP Client 使用方式

OAuth-capable MCP client 应配置：

```text
https://adintel-mcp.3.15.29.33.sslip.io/mcp
```

客户端会通过 MCP OAuth metadata 发现：

- `/register`
- `/authorize`
- `/token`
- `/revoke`
- protected resource metadata

用户登录时必须使用 `@feedmob.com` Google Workspace 账号。服务端会同时检查：

- Google `hd` claim 是否等于 `feedmob.com`
- email 是否以 `@feedmob.com` 结尾

---

## 8. Secret 轮换流程

如果 Coolify token 或 Google client secret 泄露，按下面流程轮换。

Google client secret：

1. Google Cloud Console → APIs & Services → Credentials。
2. 打开当前 OAuth Web Client。
3. 创建/重置 Client Secret。
4. 在 Coolify 更新 `GOOGLE_CLIENT_SECRET`。
5. 触发 Coolify redeploy。
6. 重新跑第 6 节验证。

Coolify API token：

1. Coolify dashboard 中创建新的 API token。
2. 本机更新 `COOLIFY_TOKEN`。
3. 确认新 token 可用：

```bash
curl -fsS -H "Authorization: Bearer $COOLIFY_TOKEN" \
  "$COOLIFY_URL/api/v1/projects" | python3 -m json.tool
```

4. 删除旧 token。

---

## 9. 常见问题

### `/health` 返回 `auth:"api_key"`

说明 OAuth 没有启用。检查 Coolify runtime env 是否同时存在：

- `BASE_URL`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

添加后必须 redeploy。

### `/mcp` 仍然在根路径 `/`

说明 OAuth 没有启用，或者设置了错误的 `ADINTEL_MCP_PATH`。建议删除 `ADINTEL_MCP_PATH`，让代码在 OAuth 模式下自动使用 `/mcp`。

### Google 登录后提示 domain forbidden

检查：

- 登录账号是否为 `@feedmob.com`
- Google Workspace 是否返回 `hd=feedmob.com`
- Coolify 的 `ALLOWED_DOMAIN` 是否为 `feedmob.com`

### OAuth metadata 里出现 localhost

检查 Coolify 的：

```text
BASE_URL=https://adintel-mcp.3.15.29.33.sslip.io
```

不要带路径，不要用 localhost。

### Coolify 显示 unhealthy，但 `/health` 是 200

先等 1-2 分钟；Coolify 状态有时会短暂抖动。若持续 unhealthy，检查 app 设置：

- Health check enabled: true
- Health check path: `/health`
- Health check port: `7860`
- Health check scheme: `http`

---

## 10. 本地测试命令

运行测试：

```bash
uv run pytest
```

只跑 OAuth/MCP 相关测试：

```bash
uv run pytest tests/test_mcp_auth.py tests/test_mcp_server.py::test_mcp_server_registers_expected_tools
```

检查新代码 lint：

```bash
uv run ruff check hf_space.py src/adintel/mcp/auth.py tests/test_mcp_auth.py
```

本地模拟 OAuth mode 路由表：

```bash
BASE_URL=https://adintel-mcp.3.15.29.33.sslip.io \
GOOGLE_CLIENT_ID=dummy.apps.googleusercontent.com \
GOOGLE_CLIENT_SECRET=dummy \
ALLOWED_DOMAIN=feedmob.com \
uv run python - <<'PY'
from adintel.mcp.server import create_mcp_server

app = create_mcp_server().streamable_http_app()
for route in app.routes:
    print(getattr(route, "path", None))
PY
```

期望包含：

```text
/.well-known/oauth-authorization-server
/authorize
/token
/register
/revoke
/mcp
/.well-known/oauth-protected-resource/mcp
/health
/auth/google/callback
```

