use std::{env, fs, path::PathBuf};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, clap::ValueEnum, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Cli,
    Sidecar,
}
impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Cli => "cli",
            Self::Sidecar => "sidecar",
        }
    }
    pub fn for_hook() -> Self {
        if env::var_os("ANTIGRAVITY_LS_ADDRESS").is_some_and(|v| !v.is_empty()) {
            Self::Sidecar
        } else {
            Self::Cli
        }
    }
}
// Selected once at the process boundary; never mutate the daemon environment.
static MODE: std::sync::OnceLock<Mode> = std::sync::OnceLock::new();
pub fn set_mode(mode: Mode) {
    MODE.set(mode).expect("mode already selected");
}
pub fn mode() -> Mode {
    *MODE.get_or_init(Mode::default)
}

pub fn home() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .expect("HOME must be set")
}
/// Preserve the host-injected PATH and append the CLI shim directory as a fallback.
/// Only applied to reviewer child processes, never to the daemon's global environment.
pub fn backend_path() -> anyhow::Result<std::ffi::OsString> {
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
    state_dir_for(mode())
}
pub fn state_dir_for(mode: Mode) -> PathBuf {
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
        .join(mode.as_str())
}
pub fn socket_path() -> PathBuf {
    socket_path_for(mode())
}
pub fn socket_path_for(mode: Mode) -> PathBuf {
    let base = env::var_os("AGY_APPROVER_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".gemini/antigravity-cli/approver.sock"));
    let stem = base.file_stem().unwrap_or_default().to_string_lossy();
    base.with_file_name(format!("{stem}-{}.sock", mode.as_str()))
}
pub fn config_path() -> PathBuf {
    home().join(".gemini/config/agy-auto-approve.toml")
}

#[derive(Default, serde::Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
struct FileConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    prompt: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    cli_model: Option<String>,
}

#[derive(serde::Serialize)]
pub struct ReviewerConfig {
    pub model: Option<String>,
    pub prompt: String,
    pub cli_model: Option<String>,
    pub cli_model_source: String,
    pub model_source: String,
    pub prompt_source: String,
}

fn read_optional(path: &std::path::Path) -> anyhow::Result<Option<String>> {
    use anyhow::Context;
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e).with_context(|| format!("Cannot read {}", path.display())),
    }
}

fn file_config() -> anyhow::Result<FileConfig> {
    use anyhow::Context;
    read_optional(&config_path())?
        .map(|text| {
            toml::from_str(&text)
                .with_context(|| format!("Invalid configuration: {}", config_path().display()))
        })
        .unwrap_or_else(|| Ok(FileConfig::default()))
}

fn resolve(name: &str, value: Option<String>, default: &str) -> anyhow::Result<(String, String)> {
    let variable = format!("AGY_AUTO_APPROVE_{}", name.to_uppercase());
    if let Ok(value) = env::var(&variable)
        && !value.is_empty()
    {
        return Ok((value, variable));
    }
    if let Some(value) = value {
        return Ok((value, config_path().display().to_string()));
    }
    Ok((default.into(), "default".into()))
}

pub fn reviewer_config() -> anyhow::Result<ReviewerConfig> {
    let file = file_config()?;
    let (model, model_source) = resolve("model", file.model, "")?;
    let model = model.trim();
    anyhow::ensure!(
        matches!(model, "" | "flash_lite" | "flash" | "pro"),
        "Invalid model {model:?} from {model_source}; expected flash_lite, flash, pro, or an empty string for the host default"
    );
    let (prompt, prompt_source) = resolve("prompt", file.prompt, include_str!("prompt.txt"))?;
    let (cli_model, cli_model_source) = resolve("cli_model", file.cli_model, "")?;
    Ok(ReviewerConfig {
        cli_model: (!cli_model.trim().is_empty()).then(|| cli_model.trim().to_owned()),
        cli_model_source,
        model: if model.is_empty() {
            None
        } else {
            Some(model.into())
        },
        prompt,
        model_source,
        prompt_source,
    })
}

pub fn show(json: bool) -> anyhow::Result<()> {
    let config = reviewer_config()?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "file": config_path(), "reviewer": config, "mode": mode(),
                "socket": socket_path(), "state_dir": state_dir(), "log_dir": log_dir()
            }))?
        );
    } else {
        println!("Config: {}", config_path().display());
        println!(
            "Model: {} ({})",
            config.model.as_deref().unwrap_or("host default"),
            config.model_source
        );
        println!(
            "CLI model: {} ({})",
            config.cli_model.as_deref().unwrap_or("host default"),
            config.cli_model_source
        );
        println!("Mode: {}", mode().as_str());
        println!("Prompt ({}):\n{}", config.prompt_source, config.prompt);
        println!(
            "Socket: {}\nState: {}\nLogs: {}",
            socket_path().display(),
            state_dir().display(),
            log_dir().display()
        );
        println!(
            "These settings apply to new reviewer conversations. Run `agy-auto-approve daemon restart` to reset the cached conversation. Environment overrides use the daemon's startup environment."
        );
    }
    Ok(())
}

pub fn edit() -> anyhow::Result<()> {
    use anyhow::Context;
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let path = config_path();
    fs::create_dir_all(path.parent().unwrap())?;
    // Start with built-in defaults; environment overrides remain temporary.
    if !path.exists() {
        let template = FileConfig {
            model: Some(String::new()),
            cli_model: Some(String::new()),
            prompt: Some(include_str!("prompt.txt").into()),
        };
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)?;
        writeln!(
            file,
            "# Global reviewer settings. model: sidecar tier; cli_model: agy model ID. Empty uses host default.\n# Environment variables override this file. Apply with: agy-auto-approve daemon restart\n{}",
            toml::to_string_pretty(&template)?
        )?;
    }
    let editor = ["VISUAL", "EDITOR"]
        .into_iter()
        .filter_map(|key| env::var(key).ok())
        .find(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "vi".into());
    let status = std::process::Command::new("/bin/sh")
        .arg("-c")
        .arg(format!("exec {editor} \"$1\""))
        .arg("agy-config-editor")
        .arg(&path)
        .status()
        .context("Cannot launch configuration editor")?;
    anyhow::ensure!(status.success(), "Editor exited with {status}");
    let file = file_config()?;
    if let Some(model) = file.model {
        anyhow::ensure!(
            matches!(model.trim(), "" | "flash_lite" | "flash" | "pro"),
            "Invalid model in {}; expected flash_lite, flash, pro, or empty",
            path.display()
        );
    }
    eprintln!(
        "Saved configuration. Run `agy-auto-approve daemon restart` to use it in a new reviewer conversation. Environment variables override this file."
    );
    Ok(())
}
