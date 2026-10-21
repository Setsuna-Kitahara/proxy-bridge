# Proxy Bridge（代理桥）

[![许可证：MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-41BDF5.svg)](https://www.home-assistant.io)
[![AI 搭建](https://img.shields.io/badge/built%20with-AI-8A2BE2.svg)](#作者与搭建方式)

在 Home Assistant 内部，把**指定的域名**分流到 HTTP / HTTPS / SOCKS5 代理——
不改核心代码，也不做 TLS 中间人解密。

Home Assistant 从不读取 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，HACS 又只会
直连 GitHub 下载。于是：当 `github.com`、`raw.githubusercontent.com` 或
`codeload.github.com` 被阻断时，你**连"装一个能修好它的插件"这件事都做不到**。

Proxy Bridge 就是干这个的。它不是 HACS 专用插件：它只在进程内给
`aiohttp.ClientSession` 打一次补丁，之后 Home Assistant 里所有走 aiohttp 的
请求（Core、HACS、其它自定义集成）都会按你的规则匹配。

- **零第三方依赖**：纯 aiohttp + asyncio。
- **SOCKS5 / SOCKS5H**，支持 RFC 1929 用户名密码认证。
- **HTTP / HTTPS 代理**，支持 Basic 认证、自签证书。
- **图形界面配置**，带连通性测试。
- **内置英语 + 简体中文**，并提供其它语言的提交规范。
- **双包自举**：另有一个名字不同的"自举版"，可在完全没有代理时手工拷入，
  之后再让正式版读取它的配置。
- **诊断、系统健康、`probe` 服务**，方便排查。

## 作者与搭建方式

**本插件由 AI 参与搭建，这一点明确写出、不做隐瞒。** 它由 **Kitahara Setsuna**
与 AI 编码代理 **DeepSeek Harness**（使用 `deepseek-flash` 模型）共同设计并实现：
整体架构、aiohttp 补丁、零依赖 SOCKS5 客户端、配置向导、翻译流水线与测试套件，
都出自这次人机协作。行为由 `tests/` 下的自动化测试固定，并在
Home Assistant 2025.1.4（`pytest-homeassistant-custom-component` 钉住的版本）上
跑通，另外对照 2026.10.2 源码做过逐项核对。

之所以公开声明，是因为“谁写的”不该成为评价标准——请按测试、代码和 issue
记录来判断这个项目。人类和 AI Agent 提交的 bug 报告与补丁同样欢迎。

以 [MIT 许可证](LICENSE) 发布，版权归 Kitahara Setsuna 与 DeepSeek Harness。

## 工作原理

```
aiohttp.ClientSession._request（全局补丁一次）
        │ 域名命中规则、且调用方没有自己传 proxy？
        ▼
http://127.0.0.1:<随机端口>     ← 本地回环桥，每个配置项一个
        │  CONNECT 隧道 / origin-form 转发
        ▼
HTTP · HTTPS · SOCKS5 · SOCKS5H  →  上游代理  →  互联网
```

补丁只**新增** `proxy=` 参数，绝不覆盖调用方显式传入的 `proxy=`，
所以已经在用代理的集成不会被抢走流量。HTTPS 全程 `CONNECT` 隧道透传，
Proxy Bridge 看不到、也不解密任何 TLS 内容。

## 环境要求

- Home Assistant **2024.12** 或更新版本。
- 一个 HA 服务器能连接到的代理服务器。请先确认 HA 服务器能连接到你要设置的代理。
- HACS 是可选的——发布版 zip 自带全部文件。

## 校验下载

发布包会与 `SHA256SUMS` 一起挂在
[releases 页面](https://github.com/KitaharaSetsuna/proxy-bridge/releases)，仓库
`dist/` 里也会生成一份。如果你是通过网盘、镜像、代理缓存或聊天软件拿到文件的，
先校验再往 `/config` 里拷：

```bash
sha256sum -c SHA256SUMS                              # Linux
shasum -a 256 proxy_bridge_bootstrap-1.0.0.zip       # macOS
Get-FileHash .\proxy_bridge_bootstrap-1.0.0.zip -Algorithm SHA256   # PowerShell
```

<!-- sha256:begin -->
| 安装包 | SHA-256 |
| --- | --- |
| `proxy_bridge-1.0.0.zip` | `fc95cc46b6023342e03f3345184f5ccc15e96053ff6021853a19faae73e49b7a` |
| `proxy_bridge_bootstrap-1.0.0.zip` | `ed005d613ba46fafe9ef7b7e9aaa24d88a6f40dd1d55864732b0c00f59b6a85c` |
<!-- sha256:end -->

对不上就说明字节不是这个仓库构建出来的，**不要安装**。项目暂未提供代码签名，
因此这里和 release 页面公布的校验和就是完整性依据。

## 安装

### 从 HACS 安装（GitHub 已可达时）

1. HACS → **自定义仓库** → 添加
   `https://github.com/KitaharaSetsuna/proxy-bridge`，类型选「集成」。
2. 安装 **Proxy Bridge**，重启 Home Assistant。
3. **设置 → 设备与服务 → 添加集成 → Proxy Bridge**。
4. 填写代理信息、勾选域名、点测试、保存。

### GitHub 不可达时（自举）

用第二个包 `proxy_bridge_bootstrap`。它的域名刻意不同：先手工安装，
之后正式版会导入它的设置并提醒你删掉它。详见
[docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)。

## 配置项

| 字段 | 说明 |
| --- | --- |
| 名称 | 条目名，同时用作唯一 ID。 |
| 代理类型 | `http`、`https`、`socks5`（本地解析）、`socks5h`（交给代理解析，推荐）。 |
| 主机 / 端口 | 上游代理地址。除非代理就跑在 HA 容器里，否则请填局域网地址（如 `192.168.1.10`），**不要**填 `127.0.0.1`。 |
| 用户名 / 密码 | 可选。HTTP(S) 走 `Proxy-Authorization: Basic`，SOCKS5 走 RFC 1929。 |
| 验证上游 TLS | 仅在对端是自签证书的 HTTPS 代理时关闭。 |
| 域名 | 预置项 + 任意自定义。支持精确域名、`*.example.com` 通配、glob。 |
| 重试 | 仅对 **GET/HEAD** 的连接错误/超时额外重试（0–10）。 |
| 启用 | 保留条目但不分流。 |
| 测试连接 | 用新配置真实发一次请求；不想联网可取消勾选直接保存。 |

预置域名覆盖 GitHub 下载链路和 HACS 元数据：

```
raw.githubusercontent.com  objects.githubusercontent.com  codeload.github.com
github.com                 api.github.com                 gist.githubusercontent.com
release-assets.githubusercontent.com  data-v2.hacs.xyz
```

HACS 优点在于优先取 release 压缩包（`codeload.github.com`），没有附件时
才逐文件走 `raw.githubusercontent.com`，所以两个都必须分流。

### YAML（可选）

正常用界面即可。若偏好 YAML，写一次，导入流程会建好配置项：

```yaml
proxy_bridge:
  - proxy: "socks5h://user:secret@192.168.1.10:1080"
    name: Home proxy
    hosts: [github.com, raw.githubusercontent.com, "*.githubusercontent.com"]
    retries: 2
    enabled: true
```

旧的 `proxy_shim:` 实验里的 `proxy` / `hosts` / `retries` 可直接搬过来。

### 服务

```yaml
service: proxy_bridge.probe
data:
  proxy: "socks5h://user:secret@192.168.1.10:1080"
  hosts: [raw.githubusercontent.com]
response_variable: probe
```

`probe` 会临时起一座桥，请求 `https://<host>/`，返回 `{ok, error, hosts}`。
适合在保存前验证候选代理，或界面进不去时排查。

## 排错

| 现象 | 排查 |
| --- | --- |
| 测试报 `407` | 代理要认证——填用户名密码。 |
| 测试报证书错误 | HTTPS 代理用了自签证书——关闭「验证上游 TLS」。 |
| 测试通过但 HACS 仍失败 | 被墙的域名不在「域名」里。补上，并观察日志里的 `routing ... through ...`。 |
| 完全没分流 | 条目被禁用；或调用方自己传了 `proxy=`（例如同时装了别的代理插件）。不要和 `hacs-proxy` 同装。 |
| 启动后头几次请求失败 | 本集成加载晚于部分集成；HACS 每 30–60 分钟自刷新，或直接重载本集成。 |

打开调试日志：

```yaml
logger:
  logs:
    custom_components.proxy_bridge: debug
```

诊断（设置 → 设备与服务 → Proxy Bridge → ⋯ → 下载诊断）包含脱敏后的配置、
实时监听地址、路由与计数器。密码不会进入日志或诊断。

## 已有先例

- [`ryanh7/hacs-proxy`](https://github.com/ryanh7/hacs-proxy)（2022，约 123★）：
  直接替换 `hacs.session`、`hacs.github._session` 等 HACS 内部对象，只支持
  单个 HTTP 代理 URL。没有 SOCKS5、没有代理认证、没有域名选择、没有 i18n
  框架、没有自举方案，而且 HACS 一改内部结构就失效。Proxy Bridge 改的是
  共用的 aiohttp 层，不依赖 HACS 内部实现。
- Home Assistant Core：没有全局代理支持（`HTTP_PROXY` 被忽略）；dev 分支的
  `marketplace` 也没有代理选项。
- HACS 2.x：全仓库没有任何代理设置。
- [`dermotduffy/hass-web-proxy-integration`](https://github.com/dermotduffy/hass-web-proxy-integration)
  是**反向** Web 代理（给摄像头/媒体 URL 用），无关。

## 限制

- 只覆盖 **aiohttp** 流量；`requests`/`urllib`/裸 socket 不拦截
  （HA Core 与 HACS 都是 aiohttp）。
- 设计上不做 TLS 中间人，因此不做内容过滤、HTML 改写、请求头注入。
- 本地回环桥监听 `127.0.0.1` 且无鉴权——条目启用期间，同机进程可以拿它当代理用。
- 代理密码由 HA 明文存在 `.storage`，与其它集成的密钥一致。
- 只有 GET/HEAD 会重试；POST/PUT 绝不重放。

## 参与贡献

- 翻译：[docs/TRANSLATING.md](docs/TRANSLATING.md)
- 架构与设计取舍：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 安全模型：[docs/SECURITY.md](docs/SECURITY.md)
- 发布流程：[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check .
python -m pytest -q
python scripts/validate.py
python scripts/build_dist.py
```

## 许可证

MIT © 2026 Kitahara Setsuna 与 DeepSeek Harness，见 [LICENSE](LICENSE)。
