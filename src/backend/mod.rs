//! Session-oriented backend interface, independent of executable and response format.
mod agentapi;
mod agy;
mod process;

use crate::config::{Mode, ReviewerConfig};
pub use agentapi::AgentApiBackend;
pub use agy::AgyBackend;
use anyhow::{Context, Result};
use serde_json::Value;
use std::{future::Future, path::PathBuf, pin::Pin};

// Boxed Send futures keep the async interface usable through a trait object.
pub type BackendFuture<'a> = Pin<Box<dyn Future<Output = Result<String>> + Send + 'a>>;

pub trait Backend: Send + Sync {
    fn create_session<'a>(&'a self, config: &'a ReviewerConfig, id: &'a str) -> BackendFuture<'a>;
    fn send_message<'a>(&'a self, cid: &'a str, payload: &'a str, id: &'a str)
    -> BackendFuture<'a>;
}

pub fn for_mode(mode: Mode, workspace: PathBuf) -> Box<dyn Backend> {
    match mode {
        Mode::Sidecar => Box::new(AgentApiBackend),
        Mode::Cli => Box::new(AgyBackend::new(workspace)),
    }
}

fn conversation_id(v: &Value) -> Result<String> {
    v["conversationId"]
        .as_str()
        .or(v["conversation_id"].as_str())
        .filter(|s| !s.trim().is_empty())
        .map(str::to_owned)
        .context("No conversationId in backend output")
}
