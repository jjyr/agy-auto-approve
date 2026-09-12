# agy-auto-approve

Antigravity CLI / Desktop 的自动审批 hook 与持久化审批 daemon，使用 Rust 构建为一个可执行文件。运行时不需要 Python、虚拟环境或单独安装 Tree-sitter；AI 审批仍依赖宿主提供的 `agentapi` 命令及其登录状态。

## 安装

支持 macOS / Linux 的 ARM64 和 x86_64。默认安装 GitHub Releases 预编译二进制，无需 Rust、Cargo 或 Python；需要 `curl`、`tar`，以及 `sha256sum` 或 `shasum`。Linux 使用 musl 静态链接版本。

```bash
curl -fsSL https://raw.githubusercontent.com/jjyr/agy-auto-approve/main/scripts/install.sh | sh
```

安装脚本识别系统和 CPU，将最新正式 Release 解析为固定 tag，下载对应压缩包与 `SHA256SUMS`，校验后安装到 `~/.local/bin/agy-auto-approve`，然后注册 CLI hook 和 Desktop sidecar。它使用原子替换更新二进制，不需要 sudo，也不修改 shell 配置。将 `~/.local/bin` 加入 PATH 后即可使用命令。下载、校验或版本检查失败时保留已有安装。

```bash
# 指定版本、仅注册 CLI
curl -fsSL https://raw.githubusercontent.com/jjyr/agy-auto-approve/main/scripts/install.sh | \
  sh -s -- --version v0.3.0 --cli-only

# 使用本地脚本：默认也是下载 Release
sh scripts/install.sh --bin-dir "$HOME/bin" --no-register

# 安装已有二进制，或构建本地源码
./scripts/install.sh --binary /path/to/agy-auto-approve
./scripts/install.sh --source .

# 也支持 Cargo 从当前源码安装
cargo install --path . --locked
agy-auto-approve register
```

crate 和 bin 均命名为 `agy-auto-approve`。当前分发方式是 GitHub Releases，不依赖 crates.io。源码构建需要 Rust 工具链和 C 编译器。首次正式 Release 发布前，下载方式会明确报错，可使用 `--source` 或 `--binary` 安装。

`register` 默认配置 CLI 与 Desktop，保留其他配置项；重复执行可更新已有 hook：写入 `~/.gemini/config/hooks.json`，在已有 CLI settings 中补齐开发命令权限，并部署和启用 Desktop sidecar。不要移动注册后的二进制文件；如移动，重新执行注册。

```bash
agy-auto-approve register --cli-only
agy-auto-approve register --desktop-only
```

也可以在 `cargo install` 后使用 `agy plugin install /path/to/agy-auto-approve` 安装插件元数据，然后执行 `agy-auto-approve register`，让全局配置使用固定的绝对路径。仓库中的插件 manifest 使用 PATH 中的 `agy-auto-approve`。

## Daemon 管理

```bash
agy-auto-approve daemon start       # 后台启动；已启动时返回现有进程状态
agy-auto-approve daemon status      # JSON：PID、版本、socket、运行时长和审批计数
agy-auto-approve daemon stop        # 停止并等待 socket 清理
agy-auto-approve daemon run         # 前台运行，适用于 sidecar / 服务管理器
agy-auto-approve daemon run --idle-timeout 0  # 禁用空闲退出
```

默认 socket 为 `~/.gemini/antigravity-cli/approver.sock`，默认空闲 30 分钟退出。CLI hook 在需要 AI 审批时自动启动 daemon。`status` 不会启动 daemon，未运行时返回退出码 1。`start/status/stop` 向 stdout 输出 JSON，诊断信息写入 stderr。

迁移旧版本时，可直接用 Rust 命令停止仍占用相同 socket 的旧 daemon：

```bash
agy-auto-approve daemon stop
agy-auto-approve daemon start
```

如旧 daemon 由 Desktop 监管，应在重新注册后重启 Desktop，使其加载新 sidecar 命令。

升级二进制后，已运行的 daemon 仍执行旧版本；安装脚本会提示重启。CLI 使用 `daemon stop` / `daemon start`，Desktop 使用宿主重启方式。安装不会清空已有审批历史或 reviewer 会话。

## 审批历史与详细日志

```bash
agy-auto-approve logs                         # 最近 20 次已完成审批，最新在前
agy-auto-approve logs --limit 100
agy-auto-approve logs --decision deny
agy-auto-approve logs --tool run_command --conversation CONVERSATION_ID
agy-auto-approve logs --json                  # JSON 摘要列表
agy-auto-approve logs show APPROVAL_ID         # 一次审批的完整 JSON 事件记录
agy-auto-approve logs -f                         # 显示最近记录并持续关注新审批
agy-auto-approve logs -f --decision deny         # 持续关注拒绝结果
agy-auto-approve logs -f --json                  # JSON Lines 流，便于管道处理
```

`-f` / `--follow` 先按时间正序显示最近 `--limit` 条匹配记录，再持续输出新完成的审批，按 Ctrl-C 退出。无 `-f` 时列表仍为最新在前，`--json` 输出 JSON 数组；跟随模式则每行一个 JSON 对象。跟随会等待日志文件创建，并在检测到文件替换或截断后重新读取；只读取已完整写入的行。`logs show ID` 查看详情，不接受 `-f` 或列表过滤参数。

每次审批分配独立 ID，串联以下记录：

- 原始 hook JSON 输入；非法输入记录原始文本（超过 1 MiB 时只记录已读取部分并标记截断）。
- 实际发送给 reviewer 的工具参数、工作区和检查到的脚本内容。
- `agentapi` 命令参数、原始 stdout / stderr、退出码和耗时。新建会话时记录提示词；复用时记录 reviewer 会话 ID，不重复发送提示词。
- 会话重建重试、调用错误、daemon 超时结果，以及解析后的风险等级、用户授权程度和判断理由。
- 最终 hook 输出（包括 `permissionOverrides`）、决策阶段和总耗时。白名单、黑名单、熔断和异常也会记录，不调用模型的审批没有模型事件。

这里的输出是审批服务和 agentapi 的输出，不是审批之后工具实际执行的 stdout/stderr。判断理由来自 agentapi 返回的 assessment；服务无法记录模型未返回的内部推理。

详细记录默认写入 `~/.gemini/agy-auto-approve/approvals.jsonl`，沿用 `AGY_AUTO_APPROVE_LOG_DIR`。这是插件自己的日志目录，CLI 和 Desktop 共用，不要求安装 Antigravity CLI。文件权限为 `0600`，包含原始参数和模型响应；它按事件追加并对并发写入加锁。`logs` 直接读取文件，不需要 daemon 在线，`AGY_AUTO_APPROVE_SILENT` 不会关闭记录。没有自动轮转或清理。意外终止的审批可能只有部分事件，可通过已记录的 ID 查看；摘要仅列出已完成审批。

旧版位于 `~/.gemini/antigravity-cli/` 的日志保留原位，不会自动搬移或合并。升级后重启 daemon（Desktop 重启宿主），让 hook 和 daemon 都使用新目录。查看旧记录可临时覆盖目录：

```bash
AGY_AUTO_APPROVE_LOG_DIR="$HOME/.gemini/antigravity-cli" agy-auto-approve logs
```

原来的 `auto-approve.log` 摘要日志继续保留；之前未记录的输入和模型响应无法补回，`logs` 仅查询新增的结构化记录。

## 审批行为

审批管线按以下顺序执行：

1. 只读工具白名单直接允许。
2. Bash Tree-sitter AST 拆解命令，匹配原有灾难性命令黑名单。
3. 按对话 ID 读取熔断状态：连续 3 次拒绝，或最近 5 次中 4 次拒绝且最后一次拒绝时，返回 `force_ask`。
4. 通过 daemon 调用 `agentapi new-conversation` / `send-message`，复用持久化会话；缓存会话失效时重建并重试一次。
5. 容错解析 JSON / Markdown 代码块审批结果，更新熔断状态；允许时生成原有格式的 `permissionOverrides`。

保留原有脚本内容检查（最多 4000 字符）、`gh` 命令权限前缀、80 字符命令权限截断、审计日志及 `allow/deny/ask/force_ask` 输出。模型或 daemon 不可用、响应不可解析时拒绝；非法 hook 输入返回 `ask`。运行实现和测试均使用 Rust。

运行可靠性措施包括 socket 独占锁、socket 权限 `0600`、熔断状态加锁及原子写入、IPC 大小限制、模型调用超时、审批期间仍可查询状态。注册器遇到损坏 JSON 会报错并保留原文件。黑名单与权限生成策略按原实现移植，没有扩大规则覆盖范围。

## 配置

沿用原有环境变量及文件路径：

| 配置 | 环境变量 / 默认路径 |
| --- | --- |
| Socket | `AGY_APPROVER_SOCKET` / `~/.gemini/antigravity-cli/approver.sock` |
| 状态目录 | `AGY_APPROVER_STATE_DIR` / `~/.gemini/antigravity-cli/state` |
| 日志目录 | `AGY_AUTO_APPROVE_LOG_DIR` / `~/.gemini/agy-auto-approve` |
| 静默日志 | `AGY_AUTO_APPROVE_SILENT=1`（关闭 stderr 审批提示） |
| 提示词 | `AGY_AUTO_APPROVE_PROMPT` |

设置日志目录且没有显式指定状态目录时，状态存放在日志目录下的 `state/`。提示词优先级为环境变量、daemon 工作目录中的 `.agents/agy-auto-approve-prompt.txt`、`~/.gemini/config/agy-auto-approve-prompt.txt`、[默认提示词](src/prompt.txt)。提示词在创建 reviewer 会话时注入；已有会话继续沿用原提示词。

model/effort 配置解析已保留（默认 `gemini-3.7-flash` / `medium`），但桥接目前不会把它们传给 `agentapi`。实际模型由 `agentapi` 宿主决定。daemon 复用一个审批会话，配置文件相对 daemon 启动目录解析。

```bash
tail -f ~/.gemini/agy-auto-approve/auto-approve.log
printf '%s\n' '{"toolCall":{"name":"view_file","args":{}},"workspacePaths":[]}' | agy-auto-approve hook
```

## 开发与验证

```bash
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
```

Rust 测试覆盖 AST、黑白名单、容错响应、熔断持久化，以及真实二进制的 hook、daemon 自动启动/停止/空闲退出、会话复用/重建和注册流程。集成测试使用临时 HOME 和模拟 `agentapi`，无需登录或 Python。

架构说明：[审批架构参考](docs/auto_approver_architecture.md)、[Sidecar 与 daemon](docs/sidecars.md)。

## 发布版本

普通分支 push 和 PR 运行测试，不发布二进制。提交代码并更新 `Cargo.toml` / `Cargo.lock` 中的包版本后，推送对应正式版本 tag：

```bash
git tag v0.3.0
git push origin v0.3.0
```

[Release workflow](.github/workflows/release.yml) 校验 tag 为 `vX.Y.Z` 且与 Cargo 版本一致，在四种原生 runner 上执行检查、测试和构建：

| 系统 | Rust target | GitHub runner |
| --- | --- | --- |
| Linux x86_64 | `x86_64-unknown-linux-musl` | `ubuntu-24.04` |
| Linux ARM64 | `aarch64-unknown-linux-musl` | `ubuntu-24.04-arm` |
| macOS Intel | `x86_64-apple-darwin` | `macos-15-intel` |
| macOS Apple Silicon | `aarch64-apple-darwin` | `macos-15` |

产物命名为 `agy-auto-approve-vX.Y.Z-<target>.tar.gz`，每个包只包含一个 `agy-auto-approve` 可执行文件。Linux 检查产物没有动态加载器或共享库依赖；macOS 构建目标最低为 macOS 11。

全部构建成功后，发布任务生成 `SHA256SUMS`，上传四个包及校验文件到草稿 Release，再将其公开为最新正式版。失败时不会将未完成的草稿设为最新版本；可以重跑补全草稿，但已公开版本禁止覆盖。Workflow 使用仓库自带 `GITHUB_TOKEN`，只给发布任务 `contents: write` 权限，不需要另配 PAT。这里的校验用于下载完整性验证，二进制不包含 Apple Developer ID 签名或公证。

## License

MIT
