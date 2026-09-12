use crate::{audit, config, parser};
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{path::PathBuf, time::Duration};
use tokio::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Assessment {
    pub outcome: String,
    pub risk_level: String,
    pub user_authorization: String,
    pub rationale: String,
}
impl Assessment {
    pub fn deny(reason: impl std::fmt::Display) -> Self {
        Self {
            outcome: "deny".into(),
            risk_level: "high".into(),
            user_authorization: "unknown".into(),
            rationale: format!("Fail-closed: {reason}"),
        }
    }
}
pub fn parse(raw: &str) -> Assessment {
    if raw.trim().is_empty() {
        return Assessment::deny("LLM review completed without an assessment payload");
    }
    let mut data = serde_json::from_str::<Value>(raw.trim()).ok();
    if data.is_none() {
        let fence = regex::Regex::new(r"(?s)```(?:json)?\s*(.*?)\s*```").unwrap();
        if let Some(c) = fence.captures(raw) {
            data = serde_json::from_str(c[1].trim()).ok();
        }
    }
    if data.is_none()
        && let (Some(a), Some(b)) = (raw.find('{'), raw.rfind('}'))
        && a < b
    {
        data = serde_json::from_str(&raw[a..=b]).ok();
    }
    let Some(v) = data.filter(Value::is_object) else {
        return Assessment::deny("Assessment payload was not valid JSON");
    };
    // An invalid, nonempty outcome must not fall back to a more permissive alias.
    let outcome_value = v
        .get("outcome")
        .filter(|value| match value {
            Value::Null => false,
            Value::Bool(b) => *b,
            Value::Number(n) => n.as_f64() != Some(0.0),
            Value::String(s) => !s.is_empty(),
            Value::Array(a) => !a.is_empty(),
            Value::Object(o) => !o.is_empty(),
        })
        .or_else(|| v.get("decision"));
    let outcome = outcome_value
        .and_then(Value::as_str)
        .unwrap_or("deny")
        .trim()
        .to_lowercase();
    let outcome = if matches!(outcome.as_str(), "allow" | "deny" | "ask" | "force_ask") {
        outcome
    } else {
        "deny".into()
    };
    let allow = outcome == "allow";
    Assessment {
        outcome,
        risk_level: v["risk_level"]
            .as_str()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or(if allow { "low" } else { "high" })
            .trim()
            .into(),
        user_authorization: v["user_authorization"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or("unknown")
            .trim()
            .into(),
        rationale: v["rationale"]
            .as_str()
            .filter(|s| !s.trim().is_empty())
            .or(v["reason"].as_str())
            .filter(|s| !s.trim().is_empty())
            .unwrap_or(if allow {
                "Auto-review returned a low-risk allow decision."
            } else {
                "Auto-review returned a deny decision without a rationale."
            })
            .trim()
            .into(),
    }
}
pub fn script_content(cmd: &str, workspaces: &[Value]) -> String {
    for command in parser::commands(cmd) {
        for token in command.split_whitespace() {
            // Inspect script candidates with the established 4000-character limit.
            if ![
                ".sh", ".py", ".js", ".ts", ".bash", ".zsh", ".rb", ".mjs", ".cjs",
            ]
            .iter()
            .any(|ext| token.ends_with(ext))
            {
                continue;
            }
            let candidate = token.trim_matches(['\'', '"']);
            for ws in workspaces.iter().filter_map(Value::as_str) {
                if let Ok(bytes) = std::fs::read(PathBuf::from(ws).join(candidate)) {
                    let content: String =
                        String::from_utf8_lossy(&bytes).chars().take(4000).collect();
                    return format!(
                        "\n[Extracted Content of Script '{candidate}']:\n```\n{content}\n```\n"
                    );
                }
            }
        }
    }
    String::new()
}
pub struct Bridge {
    pub conversation_id: Option<String>,
    path: PathBuf,
}
impl Default for Bridge {
    fn default() -> Self {
        let path = config::state_dir().join("reviewer_session.json");
        let data: Value = std::fs::read(&path)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or(Value::Null);
        let conversation_id = data["conversationId"]
            .as_str()
            .or(data["conversation_id"].as_str())
            .filter(|s| !s.is_empty())
            .map(str::to_owned);
        Self {
            conversation_id,
            path,
        }
    }
}
impl Bridge {
    async fn call(args: &[&str], id: &str) -> Result<String> {
        let started = std::time::Instant::now();
        audit::record(
            id,
            "agentapi_request",
            json!({"command":"agentapi", "args":args}),
        );
        let result = tokio::time::timeout(
            Duration::from_secs(20),
            Command::new("agentapi")
                .args(args)
                .kill_on_drop(true)
                .output(),
        )
        .await;
        let output = match result {
            Ok(Ok(output)) => output,
            error => {
                let message = match error {
                    Ok(Err(e)) => e.to_string(),
                    Err(_) => "agentapi timed out".into(),
                    _ => unreachable!(),
                };
                audit::record(
                    id,
                    "agentapi_error",
                    json!({"error":message,"duration_ms":started.elapsed().as_millis()}),
                );
                bail!("{message}");
            }
        };
        audit::record(
            id,
            "agentapi_response",
            json!({"stdout":String::from_utf8_lossy(&output.stdout),
            "stderr":String::from_utf8_lossy(&output.stderr), "exit_code":output.status.code(),
            "duration_ms":started.elapsed().as_millis()}),
        );
        if !output.status.success() {
            bail!(
                "agentapi failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().into())
    }
    async fn session(&mut self, id: &str) -> Result<String> {
        if let Some(cid) = &self.conversation_id {
            audit::record(
                id,
                "reviewer_session",
                json!({"conversation_id":cid,"reused":true}),
            );
            return Ok(cid.clone());
        }
        let raw = Self::call(
            &[
                "new-conversation",
                "--title=Guardian Approver Session",
                &config::prompt(),
            ],
            id,
        )
        .await?;
        let v: Value = serde_json::from_str(&raw)?;
        let cid = v["conversationId"]
            .as_str()
            .or(v["conversation_id"].as_str())
            .filter(|s| !s.is_empty())
            .context("No conversationId in agentapi output")?
            .to_string();
        self.conversation_id = Some(cid.clone());
        audit::record(
            id,
            "reviewer_session",
            json!({"conversation_id":cid,"reused":false}),
        );
        std::fs::create_dir_all(self.path.parent().unwrap())?;
        std::fs::write(
            &self.path,
            serde_json::to_vec(&json!({"conversationId": cid}))?,
        )?;
        Ok(cid)
    }
    async fn send(&mut self, payload: &str, id: &str) -> Result<String> {
        let cid = self.session(id).await?;
        let raw = Self::call(&["send-message", &cid, payload], id).await?;
        if let Ok(v) = serde_json::from_str::<Value>(&raw)
            && let Some(response) = v.get("response")
        {
            // Unexpected non-string response envelopes must fail closed.
            return Ok(response.as_str().unwrap_or("").to_owned());
        }
        Ok(raw)
    }
    pub async fn evaluate(&mut self, req: &Value) -> Assessment {
        let generated_id = audit::request_id();
        let id = req["request_id"].as_str().unwrap_or(&generated_id);
        let ws = req["workspacePaths"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let paths = ws
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(", ");
        let mut action = json!({"environment": {"workspace_paths": if paths.is_empty() {"Current Workspace"} else {&paths}},
            "tool": req["toolCall"]["name"], "args": req["toolCall"]["args"]});
        if req["toolCall"]["name"] == "run_command" {
            let script = script_content(
                req["toolCall"]["args"]["CommandLine"]
                    .as_str()
                    .unwrap_or(""),
                &ws,
            );
            if !script.is_empty() {
                action["inspected_script"] = script.into();
            }
        }
        let cached = self.conversation_id.is_some();
        let payload = action.to_string();
        audit::record(id, "reviewer_input", json!({"action":action}));
        let mut result = self.send(&payload, id).await;
        if result.is_err() && cached {
            audit::record(
                id,
                "reviewer_retry",
                json!({"reason":"Cached session failed; recreating conversation"}),
            );
            self.conversation_id = None;
            let _ = std::fs::remove_file(&self.path);
            result = self.send(&payload, id).await;
        }
        let assessment = match result {
            Ok(raw) => parse(&raw),
            Err(err) => Assessment::deny(format!("Approver review failed: {err}")),
        };
        audit::record(id, "reviewer_result", json!({"assessment":assessment}));
        assessment
    }
}
