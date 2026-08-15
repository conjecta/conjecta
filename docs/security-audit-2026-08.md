# Conjecta 开源代码审计与渗透测试报告（内部预审）

| 项 | 内容 |
|---|---|
| 报告日期 | 2026-08-11 |
| 审计对象 | Conjecta Math Agent 开源版本（仓库根目录） |
| 审计类型 | 白盒代码审计 + 本地安全验证（非阿里云签章报告） |
| 审计人员 | Conjecta 工程安全预审（自动化扫描 + 人工复核） |
| 版本基线 | `main` @ 审计当日工作树（含本报告同期修复） |

> **重要声明**：本报告为开源前内部预审，**不能替代**阿里云安全管家 / 代码审计 / 渗透测试或先知平台众测出具的带签章报告。结构对齐常见云厂商审计交付物，便于送测时直接对照。阿里云送测清单见文末附录 A。

---

## 1. 概述

### 1.1 业务与威胁模型

Conjecta 是面向数学求解的 Agent 服务：HTTP API + SPA、手机号 OTP 登录、LLM 工具调用、Lean 4 形式化验证、受限 Python/绘图沙箱、可选 MCP 工具。自托管场景下攻击者可能是：

1. 未授权互联网访客；
2. 已登录租户（横向 IDOR / 滥用算力）；
3. 通过 LLM 生成物间接投递的 payload（Lean / 沙箱 / markdown）。

### 1.2 审计结论（摘要）

| 级别 | 发现数（修复前） | 本轮已修复 | 残留待办 |
|---|---|---|---|
| Critical | 2 | 2 | 0 |
| High | 6 | 4 | 2（沙箱非容器隔离、MCP/沙箱 HTTP 纵深） |
| Medium | 8 | 2 | 6 |
| Low / Info | 若干 | 部分 | 见明细 |

**开源发布建议**：在完成附录 B 残留项的运营加固说明后可公开源码；对不可信多租户公网部署，必须额外提供进程隔离（cgroup/gVisor）并禁用或严格管控 MCP。

---

## 2. 审计范围

| 范围 | 覆盖 |
|---|---|
| 认证与会话 | JWT/Cookie、SMS OTP、登出、管理员边界 |
| 传输与代理 | 可信代理 CIDR、`X-Real-IP` / `X-Forwarded-Proto` |
| API / 租户隔离 | `/api/solve*`、knowledge、materials、share、Lean jobs、附图 |
| 输入与 SSRF | 上传限制、`fetch_url` / `net_safety` |
| 代码执行面 | Lean 静态门禁、REPL/cgroup、python/plot 沙箱、subprocess |
| 依赖与密钥 | bandit、pip-audit、npm audit、密钥模式扫描 |
| 前端 XSS | `MathText` / KaTeX HTML sink |

**不在范围**：第三方 LLM 供应商侧安全；物理机 / K8s 基线（仅给出自托管建议）；真实公网对生产站的黑盒压测。

---

## 3. 方法

1. **SAST**：Bandit（`math_agent/`）
2. **依赖 CVE**：`pip-audit`（Python）、`npm audit`（前端）
3. **密钥扫描**：工作树正则（无真实密钥命中；测试用假 key 除外）
4. **白盒走读**：`math_agent/web/`、`math_agent/lean/`、`math_agent/tools/*_sandbox.py`、`math_agent/net_safety.py`
5. **验证**：对 Critical Lean 绕过做 `scan_source` 实证；修复后回归测试

---

## 4. 工具扫描结果

### 4.1 Bandit

共 5 条（无 Critical 逻辑洞，多为误报/低危）：

| 严重级 | ID | 位置 | 判定 |
|---|---|---|---|
| HIGH | B324 | `knowledge_graph.py:79` SHA1 | **误报**：仅作非加密 source id 哈希；可标 `usedforsecurity=False` |
| MEDIUM | B314 | `arxiv.py` / `source_fetch.py` ElementTree | 信任 arXiv XML；可换 defusedxml（加固） |
| MEDIUM | B108 | sandbox `/tmp` | 预期：一次性子进程临时目录 |

### 4.2 pip-audit（修复前 → 后）

| 包 | 修复前 | 处理 |
|---|---|---|
| `aiohttp` 3.14.1 | 3 CVE（含 request smuggling） | **已升级** → 3.14.3 |
| `h2` 4.3.0 | Host header 重复 | **已升级** → 4.4.1 |
| `pypdf` 6.14.2 | DoS | **已升级** → 6.15.0 |
| `cryptography` 46.0.7 | 多条（需升到 48–50） | **残留**：大版本升级另排窗口 |

### 4.3 npm audit

前端 `math_agent/web/frontend`：**0** vulnerabilities。

### 4.4 密钥扫描

未发现生产密钥 / 私钥 / 真实 JWT。测试夹具中的 `sk-ant-test`、`example.supabase.co` 为假数据。

---

## 5. 发现明细

严重级定义：Critical = 可导致主机 RCE / 未授权代码执行；High = 跨租户数据或算力滥用；Medium = 需条件的加固项；Low/Info = 纵深或文档问题。

### 5.1 Critical（已修复）

#### C-01 Lean 静态门禁被 `elab_rules` + `open IO` 绕过

| 项 | 内容 |
|---|---|
| 位置 | `math_agent/lean/verifier.py`（原 `UNSAFE_SOURCE_PATTERNS`） |
| 描述 | `elab`/`macro` 词边界漏匹配 `elab_rules`/`macro_rules`；`IO` 仅匹配 `IO\.`，`open IO` + `FS.writeFile` 可通过静态门禁，elaborator 侧可写主机文件。 |
| 复现 | `LeanVerifier().scan_source(<payload>)` 修复前 `static_ok=True`。 |
| 修复 | 匹配 `elab(_rules)?` / `macro(_rules)?`；拦截裸 `IO`/`BaseIO`/`EIO`/`FS`/`System`。回归：`tests/test_lean_verifier_security.py`。 |
| 状态 | **已修复并验证** |

#### C-02 `/api/lean/jobs` 无用户鉴权（与 C-01 叠加）

| 项 | 内容 |
|---|---|
| 位置 | `math_agent/web/solve_routes.py` |
| 描述 | 仅依赖 app-token / 本地中间件，未 `require_auth_user`；可滥用 Lean 编译算力并尝试门禁绕过。 |
| 修复 | `POST/GET /api/lean/jobs*` 增加 `require_http_app_access` + `require_auth_user`。 |
| 状态 | **已修复** |

### 5.2 High（已修复 / 残留）

#### H-01 附图跨租户可读（IDOR）— 已修复

| 项 | 内容 |
|---|---|
| 位置 | `GET /api/solve/figures/{session_id}/{filename}` |
| 描述 | 任意登录用户可读全局 `artifact_root/<session>/figures/`。 |
| 修复 | 校验当前用户对该 `session_id` 有 checkpoint 或 active solve。 |
| 状态 | **已修复**（`tests/test_solve_figures_route.py`） |

#### H-02 LLM 辅助路由仅靠 app token — 已修复

| 项 | 内容 |
|---|---|
| 位置 | `/api/next-steps`、`/explore-knowledge`、`/evaluate-satisfaction/stream`、`/review-materials` |
| 描述 | 无 `require_auth_user` 时，共享 `CONJECTA_AUTH_TOKEN` 即可烧平台 LLM。 |
| 修复 | 上述路由强制 `require_auth_user`。 |
| 状态 | **已修复** |

#### H-03 默认管理员手机号硬编码 — 已修复

| 项 | 内容 |
|---|---|
| 位置 | `math_agent/web/operations.py` `DEFAULT_ADMIN_PHONE` |
| 描述 | 未设 `CONJECTA_ADMIN_PHONES` 时默认 `17855537173` 为管理员。 |
| 修复 | 默认改为空；生产必须显式配置。文档已同步。 |
| 状态 | **已修复** |

#### H-04 依赖 CVE（aiohttp/h2/pypdf）— 已修复

见 §4.2。`cryptography` 大版本升级见残留。

#### H-05 Python/Plot 沙箱非容器隔离 — 残留

进程级 AST + RLIMIT，与服务同 UID。设计如此；公网多租户需 nsjail/gVisor。**报告为已知架构限制。**

#### H-06 MCP / 沙箱出站 HTTP 纵深不足 — 残留

MCP 工具绕过 AST/Lean 门禁；compute 沙箱 `urllib` 无 IP pin（相对 `net_safety.fetch_public_url`）。建议：默认关 MCP；沙箱去掉 `urllib` 或复用 pin。

### 5.3 Medium（摘录）

| ID | 描述 | 状态 |
|---|---|---|
| M-01 | Logout 不吊销 JWT（7 天 TTL 内 Bearer 仍可用） | 残留；建议短 TTL + jti 黑名单 |
| M-02 | Logout `delete_cookie` 未镜像 Secure/SameSite | **已修复** |
| M-03 | 无 captcha；SMS 可被分布式 IP 骚扰 | 残留（阿里云 Dypns + 业务层配额） |
| M-04 | 进程内限流、多 worker 不共享 | 残留 |
| M-05 | REPL 无 `systemd-run` 时 MemoryMax 静默失效 | 残留 |
| M-06 | sympy `parse_expr(evaluate=True)` DoS 面 | 残留（有长度/词表约束） |
| M-07 | KaTeX → `dangerouslySetInnerHTML` | 残留（`trust=false`） |
| M-08 | 登录响应仍返回 `access_token`（与 cookie-only 文档略不一致） | 残留 |

### 5.4 扎实控制（可写「已缓解」）

- Cookie：`HttpOnly` + `SameSite=Lax` + 条件 `Secure`
- 可信代理：仅 peer ∈ `CONJECTA_TRUSTED_PROXY_CIDRS` 信任 Real-IP；**不**用客户端 XFF 链做鉴权
- `fetch_url` SSRF：私网/链路本地拦截 + HTTP DNS pin
- 上传：体积/数量/MIME+魔数、路径 `_safe_component`
- Solve 主路径租户隔离（store / active_solves / HITL）
- Lean 密钥剥离子进程环境；生产路径无 `shell=True`
- 前端 npm 依赖无已知 CVE；bundle 无硬编码密钥

---

## 6. 渗透测试用例（本地验证）

| 编号 | 用例 | 结果 |
|---|---|---|
| PT-01 | Lean `elab_rules`+`open IO` 静态门禁 | 修复前绕过；修复后拦截 |
| PT-02 | 用户 B 读取用户 A figure URL | 修复后 404 |
| PT-03 | 无 JWT 调 `/api/lean/jobs`（phone auth 开启时） | 修复后 401 |
| PT-04 | 伪造 `X-Real-IP` 自非信任 peer | 不提升权限（既有测试覆盖） |
| PT-05 | `fetch_url` → `http://127.0.0.1` | `UnsafeFetchURL` |
| PT-06 | 路径穿越 `/api/solve/figures/../` | 404 |

---

## 7. 修复清单（本轮）

| 变更 | 文件 |
|---|---|
| Lean 门禁加固 + 回归测试 | `math_agent/lean/verifier.py`, `tests/test_lean_verifier_security.py` |
| Lean jobs / 附图鉴权 | `math_agent/web/solve_routes.py`, `tests/test_solve_figures_route.py` |
| LLM 辅助路由鉴权 | `math_agent/web/knowledge_routes.py` |
| 取消默认 admin 手机 | `math_agent/web/operations.py`, `tests/test_operations.py`, `docs/operations-dashboard.md` |
| Logout cookie 属性对齐 | `math_agent/web/phone_auth.py` |
| 依赖升级 | `uv.lock`（aiohttp / h2 / pypdf） |

---

## 8. 结论

开源版本在**认证代理边界、SSRF、上传、主求解租户隔离**方面基础较好。审计发现的 **Critical Lean elaborator 绕过**与 **无用户鉴权的 Lean/LLM 辅助面**已在本轮关闭。残留风险集中在：**非容器沙箱、MCP 信任边界、JWT 吊销、cryptography 大版本、运营向 SMS/限流**。

对「阿里云出具正式报告」：请使用附录 A 送测；本文件可作为《自查报告》附件提交，缩短对方复测周期。

---

## 附录 A — 阿里云送测准备清单

可购买路径（任选）：

1. **阿里云安全管家** → 代码审计 / 渗透测试服务；或  
2. **先知（Xianzhi）平台**众测（需公网测试环境与授权书）。

### 需提供给厂商

| 材料 | 说明 |
|---|---|
| 源码包 / Git 只读授权 | 本仓库 tag 或 zip（含本报告对应 commit） |
| 测试环境 URL | 独立 staging；禁止直接打生产 |
| 测试账号 | 普通用户 ≥2（测 IDOR）、管理员 1（测越权） |
| 范围声明 | 含：`/api/*`、SPA、Lean jobs、上传、SMS 登录；排除第三方 LLM 供应商 |
| 时间窗口与应急联系人 | 书面授权书（众测必备） |
| 自查报告 | 本文档 PDF/Markdown |
| 配置说明 | `CONJECTA_JWT_SECRET`、`CONJECTA_TRUSTED_PROXY_CIDRS`、`CONJECTA_ADMIN_PHONES`、`CONJECTA_SMS_BYPASS_PHONES`（送测环境必须为空）、`--no-proxy-headers` |

### 送测前自检（勾选）

- [ ] `CONJECTA_SMS_BYPASS_PHONES` 为空  
- [ ] `CONJECTA_ADMIN_PHONES` 仅测试管理员号  
- [ ] `CONJECTA_ALLOW_UNAUTHENTICATED` 未开启  
- [ ] Nginx：`X-Real-IP`/`X-Forwarded-For`/`X-Forwarded-Proto` 覆盖写入；Uvicorn `--no-proxy-headers`  
- [ ] MCP 默认关闭或仅内网  
- [ ] 本报告 Critical/High「已修复」项已合入送测分支  
- [ ] 准备 WAF/限流说明（若已有）

### 预期厂商关注点（可主动说明）

1. Lean 代码执行面与静态门禁（已修 C-01）  
2. 多租户 IDOR（附图已修）  
3. SSRF（`fetch_url` / 沙箱 HTTP）  
4. OTP 防刷与短信轰炸  
5. JWT 吊销与会话固定  

---

## 附录 B — 残留加固 backlog（建议优先级）

1. P0：公网部署为 Lean/REPL/compute 加 cgroup 或 gVisor；无 `systemd-run` 则拒绝启用 REPL  
2. P0：生产禁用或 allowlist MCP；stdio MCP 独立用户  
3. P1：JWT 短 TTL + refresh / logout 吊销表  
4. P1：`cryptography` 升到修复版本（评估 breaking changes）  
5. P1：compute 沙箱移除 `urllib` 或 DNS pin  
6. P2：SMS captcha；Redis 全局限流  
7. P2：登录响应停止返回 `access_token`（纯 cookie）  
8. P2：`defusedxml` 替换 arXiv XML 解析；SHA1 标注 `usedforsecurity=False`  
9. P3：提供参考 systemd unit（`MemoryMax`、`PrivateTmp`、`ProtectSystem=strict`、`NoNewPrivileges`）

---

## 附录 C — 修订记录

| 日期 | 说明 |
|---|---|
| 2026-08-11 | 初版：三路审计汇总；修复 C-01/C-02/H-01/H-02/H-03 及部分依赖 CVE |
