use std::{env, fs, path::PathBuf};

pub fn home() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .expect("HOME must be set")
}
/// Preserve the host-injected PATH and append the CLI shim directory as a fallback.
/// Only applied to reviewer child processes, never to the daemon's global environment.
pub fn agentapi_path() -> anyhow::Result<std::ffi::OsString> {
    let mut paths: Vec<PathBuf> = env::var_os("PATH")
        .map(|path| env::split_paths(&path).collect())
        .unwrap_or_default();
    let fallback = home().join(".gemini/antigravity-cli/bin");
    if !paths.contains(&fallback) {
        paths.push(fallback);
    }
    Ok(env::join_paths(paths)?)
}
pub fn log_dir() -> PathBuf {
    env::var_os("AGY_AUTO_APPROVE_LOG_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".gemini/agy-auto-approve"))
}
pub fn state_dir() -> PathBuf {
    env::var_os("AGY_APPROVER_STATE_DIR")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            if env::var_os("AGY_AUTO_APPROVE_LOG_DIR").is_some_and(|value| !value.is_empty()) {
                log_dir().join("state")
            } else {
                home().join(".gemini/antigravity-cli/state")
            }
        })
}
pub fn socket_path() -> PathBuf {
    env::var_os("AGY_APPROVER_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".gemini/antigravity-cli/approver.sock"))
}
pub fn setting(name: &str, default: &str) -> String {
    if let Ok(value) = env::var(format!("AGY_AUTO_APPROVE_{}", name.to_uppercase()))
        && !value.is_empty()
    {
        return value;
    }
    for path in [
        PathBuf::from(format!(".agents/agy-auto-approve-{name}.txt")),
        home().join(format!(".gemini/config/agy-auto-approve-{name}.txt")),
    ] {
        if let Ok(value) = fs::read_to_string(path) {
            if name == "prompt" {
                return value;
            }
            if !value.trim().is_empty() {
                return value.trim().into();
            }
        }
    }
    default.into()
}
pub fn prompt() -> String {
    setting("prompt", include_str!("prompt.txt"))
}
pub fn model_and_effort() -> (String, String) {
    (
        setting("model", "gemini-3.7-flash"),
        setting("effort", "medium"),
    )
}
