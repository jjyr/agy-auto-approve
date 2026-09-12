use crate::{audit, config, daemon, parser, reviewer::Assessment};
use anyhow::Result;
use fs2::FileExt;
use regex::Regex;
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::LazyLock,
};

pub fn blacklist(command: &str) -> Option<String> {
    static PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
        r"(?:^|[;&|\n`$()])\s*rm\s+-[rfRF]*\s+/(?:\*|\s*$)",
        r"(?:^|[;&|\n`$()])\s*rm\s+-[rfRF]*\s+~(?:/.*|\s*$)",
        r"(?:^|[;&|\n`$()])\s*rm\s+-[rfRF]*\s+\$HOME(?:/.*|\s*$)",
        r"(?:^|[;&|\n`$()])\s*rm\s+-[rfRF]*\s+/(?:etc|usr|var|bin|System|boot|sbin|Users|home)(?:/|\s+|$)",
        r"(?:^|[;&|\n`$()])\s*rm\s+-[rfRF]*\s+(?:.*/)?\.git(?:/|\s+|$)",
        r"\bmkfs\b", r"\bfdisk\b", r"\bdd\s+if=", r":\(\)\{\s*:\|:&\s*\};:",
        r"(?:^|[;&|\n`$()])\s*chmod\s+-[rwxRWX0-7]*\s+777\s+/",
    ].iter().map(|s| Regex::new(s).unwrap()).collect()
    });
    for cmd in std::iter::once(command.to_string()).chain(parser::commands(command)) {
        for pattern in PATTERNS.iter() {
            if pattern.is_match(&cmd) {
                return Some(format!(
                    "Blocked by hard blacklist: matched pattern '{}'",
                    pattern.as_str()
                ));
            }
        }
    }
    None
}
pub fn read_only(tool: &str) -> bool {
    matches!(
        tool,
        "view_file"
            | "grep_search"
            | "find_by_name"
            | "list_dir"
            | "read_url_content"
            | "search_web"
            | "read_browser_page"
    )
}
pub struct Breaker {
    path: PathBuf,
    _lock: fs::File,
    state: Value,
}
impl Breaker {
    pub fn open(dir: &Path, cid: &str) -> Result<Self> {
        fs::create_dir_all(dir)?;
        let cid: String = cid
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        let path = dir.join(format!("cb_{cid}.json"));
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path.with_extension("lock"))?;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(1);
        loop {
            match lock.try_lock_exclusive() {
                Ok(()) => break,
                Err(e)
                    if e.kind() == std::io::ErrorKind::WouldBlock
                        && std::time::Instant::now() < deadline =>
                {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                Err(e) => return Err(e.into()),
            }
        }
        let state = fs::read(&path)
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({"consecutive_denials":0,"history":[]}));
        Ok(Self {
            path,
            _lock: lock,
            state,
        })
    }
    pub fn tripped(&self) -> Option<String> {
        let n = self.state["consecutive_denials"].as_u64().unwrap_or(0);
        if n >= 3 {
            return Some(format!(
                "Circuit breaker tripped: {n} consecutive denials exceeded threshold (3). Halting loop to prompt user."
            ));
        }
        if let Some(h) = self.state["history"].as_array() {
            let denials = h.iter().rev().take(5).filter(|v| **v == "deny").count();
            if h.len() >= 5 && denials >= 4 && h.last() == Some(&json!("deny")) {
                return Some(format!(
                    "Circuit breaker tripped: {denials}/5 denials in recent window. Halting loop to prompt user."
                ));
            }
        }
        None
    }
    pub fn record(&mut self, decision: &str) -> Result<()> {
        self.state["consecutive_denials"] = if decision == "deny" {
            self.state["consecutive_denials"].as_u64().unwrap_or(0) + 1
        } else {
            0
        }
        .into();
        let mut h = self.state["history"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        h.push(decision.into());
        if h.len() > 5 {
            h.drain(..h.len() - 5);
        }
        self.state["history"] = h.into();
        let tmp = self.path.with_extension("tmp");
        fs::write(&tmp, serde_json::to_vec(&self.state)?)?;
        fs::rename(tmp, &self.path)?;
        Ok(())
    }
}
pub fn result(decision: &str, reason: &str, tool: &str, grants: Option<Vec<String>>) -> Value {
    let tag = match decision {
        "allow" => "ALLOWED",
        "deny" => "DENIED",
        _ => "REVIEW REQUIRED",
    };
    let reason = if reason.trim().starts_with("[agy-auto-approve") {
        reason.trim().into()
    } else {
        format!("[agy-auto-approve: {tag}] {}", reason.trim())
    };
    let dir = config::log_dir();
    if fs::create_dir_all(&dir).is_ok()
        && let Ok(mut f) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("auto-approve.log"))
    {
        let _ = writeln!(
            f,
            "[{}] [{:<5}] tool={} | reason={}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            decision.to_uppercase(),
            tool,
            reason
        );
    }
    if std::env::var("AGY_AUTO_APPROVE_SILENT")
        .unwrap_or_default()
        .is_empty()
    {
        let color = match decision {
            "allow" => 32,
            "deny" => 31,
            _ => 33,
        };
        eprintln!(
            "\x1b[{color}m[agy-auto-approve: {}]\x1b[0m {tool} -> {reason}",
            decision.to_uppercase()
        );
    }
    let mut v = json!({"decision": decision, "reason": reason});
    if let Some(grants) = grants {
        v["permissionOverrides"] = grants.into();
    }
    v
}
pub async fn evaluate(payload: &Value) -> Value {
    let id = audit::request_id();
    let started = std::time::Instant::now();
    audit::record(&id, "hook_input", json!({"input":payload}));
    let mut stage = "reviewer";
    let output = evaluate_inner(payload, &id, &mut stage).await;
    audit::record(
        &id,
        "hook_result",
        json!({"tool":payload["toolCall"]["name"],
        "conversation_id":conversation_id(payload), "output":output, "stage":stage,
        "duration_ms":started.elapsed().as_millis()}),
    );
    output
}
fn conversation_id(payload: &Value) -> &str {
    payload["conversationId"]
        .as_str()
        .filter(|s| !s.is_empty())
        .or(payload["conversation_id"].as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("default")
}
async fn evaluate_inner(payload: &Value, id: &str, stage: &mut &'static str) -> Value {
    let tool = payload["toolCall"]["name"].as_str().unwrap_or("");
    let args = &payload["toolCall"]["args"];
    if read_only(tool) {
        *stage = "whitelist";
        return result(
            "allow",
            "Read-only tool automatically approved.",
            tool,
            Some(vec![]),
        );
    }
    if tool == "run_command"
        && let Some(reason) = blacklist(args["CommandLine"].as_str().unwrap_or(""))
    {
        *stage = "blacklist";
        return result("deny", &reason, tool, None);
    }
    let cid = payload["conversationId"]
        .as_str()
        .filter(|s| !s.is_empty())
        .or(payload["conversation_id"].as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("default");
    let mut breaker = match Breaker::open(&config::state_dir(), cid) {
        Ok(b) => b,
        Err(e) => {
            *stage = "state_error";
            return result(
                "deny",
                &format!("Fail-closed: cannot open circuit breaker state: {e}"),
                tool,
                None,
            );
        }
    };
    if let Some(reason) = breaker.tripped() {
        *stage = "circuit_breaker";
        return result("force_ask", &reason, tool, None);
    }
    let assessment = match daemon::review_traced(payload, id).await {
        Ok(a) => a,
        Err(e) => {
            *stage = "reviewer_error";
            Assessment::deny(format!("Approver daemon/agentapi is unavailable: {e}"))
        }
    };
    audit::record(id, "assessment", json!({"assessment":assessment}));
    if let Err(e) = breaker.record(&assessment.outcome) {
        *stage = "state_error";
        return result(
            "deny",
            &format!("Fail-closed: cannot persist circuit breaker: {e}"),
            tool,
            None,
        );
    }
    let grants = (assessment.outcome == "allow").then(|| parser::overrides(tool, args));
    result(&assessment.outcome, &assessment.rationale, tool, grants)
}
