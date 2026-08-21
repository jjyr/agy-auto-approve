# agy-auto-approve

为 [Antigravity CLI](https://antigravity.google) (`agy`) 增加智能 **Auto-Approve**（自动审批）能力。

---

## 插件功能

在 Agent 调用工具前（`PreToolUse` Hook）进行自动化安全把关：

1. **只读工具极速放行**：`view_file`、`grep_search`、`find_by_name`、`list_dir` 等无需人工确认直接放行。
2. **危险命令硬拦截**：对 `rm -rf /`、删 `.git`、磁盘格式化等破坏性指令秒级拦截（`deny`）。
3. **安全操作自动授权**：对开发环境常用命令及安全修改操作，自动评估并生成精确的 `permissionOverrides`（支持 `;`、`&&` 等复合 Shell 命令的多重授权），跳过交互式确认弹窗。
4. **实时审计日志**：终端显示审批结果，并在 `~/.gemini/antigravity-cli/auto-approve.log` 记录详细审计日志。

---

## 安装与使用

### 安装
```bash
# 本地安装
agy plugin install /path/to/agy-auto-approve && \
python3 /path/to/agy-auto-approve/scripts/register_hook.py

# 或从 GitHub 安装
git clone git@github.com:jjyr/agy-auto-approve.git /tmp/agy-auto-approve && \
agy plugin install /tmp/agy-auto-approve && \
python3 /tmp/agy-auto-approve/scripts/register_hook.py && \
rm -rf /tmp/agy-auto-approve
```

### 常用命令
- **查看已安装插件**：`agy plugin list`
- **实时查看自动审批日志**：`tail -f ~/.gemini/antigravity-cli/auto-approve.log`
- **卸载插件**：`agy plugin uninstall agy-auto-approve`

---

## Prompt 位置与自定义配置

### 默认 Prompt 位置
默认评估 Prompt 位于：  
👉 **[`scripts/eval_guard.py`](scripts/eval_guard.py)**（变量：`DEFAULT_SYSTEM_PROMPT`）

### 如何自定义 Prompt
无需修改源码，支持通过以下方式覆盖（按优先级从高到低）：

1. **环境变量**（最高优先级）：
   ```bash
   export AGY_AUTO_APPROVE_PROMPT="你的自定义安全审批提示词..."
   ```
2. **项目级文件**（仅当前项目生效）：
   在项目根目录下创建 `.agents/agy-auto-approve-prompt.txt`
3. **全局文件**（所有项目生效）：
   创建 `~/.gemini/config/agy-auto-approve-prompt.txt`
4. **修改源码**：
   直接修改 `scripts/eval_guard.py` 中的 `DEFAULT_SYSTEM_PROMPT`。

---

## 配置与 API Key 设置（可选）

如需启用 Gemini 云端语义评估，设置 API Key 即可（未配置时自动降级为本地规则评估）：
```bash
export GEMINI_API_KEY="your-api-key"
# 或写入 ~/.gemini/.env
```

---

## License

MIT
