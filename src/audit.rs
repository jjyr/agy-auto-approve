//! Append-only, correlated audit events. Logging never changes an approval decision.
use crate::config;
use anyhow::{Context, Result, bail};
use fs2::FileExt;
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

pub fn request_id() -> String {
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    format!(
        "{:x}-{:x}-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    )
}
pub fn record(id: &str, event: &str, data: Value) {
    if let Err(error) = append(id, event, data) {
        eprintln!("agy-auto-approve: unable to write approval history: {error}");
    }
}
fn append(id: &str, event: &str, data: Value) -> Result<()> {
    let dir = config::log_dir();
    fs::create_dir_all(&dir)?;
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(dir.join("approvals.jsonl"))?;
    file.set_permissions(fs::Permissions::from_mode(0o600))?;
    // One line per event, even when multiple hook processes and the daemon write together.
    file.lock_exclusive()?;
    let entry = json!({"schema_version":1, "id":id, "timestamp":chrono::Utc::now().to_rfc3339(),
        "event":event, "data":data});
    writeln!(file, "{entry}")?;
    Ok(())
}
#[derive(Default)]
pub struct Filter {
    pub limit: usize,
    pub decision: Option<String>,
    pub tool: Option<String>,
    pub conversation: Option<String>,
}
fn events(mut visit: impl FnMut(Value)) -> Result<()> {
    let path = config::log_dir().join("approvals.jsonl");
    let file = match File::open(&path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e).with_context(|| format!("Cannot read {}", path.display())),
    };
    // Snapshot the committed byte length, then release the lock before scanning.
    // Browsing a large history must not hold up active approval writers.
    FileExt::lock_shared(&file)?;
    let length = file.metadata()?.len();
    FileExt::unlock(&file)?;
    let mut reader = BufReader::new(file.take(length));
    let mut line = String::new();
    loop {
        line.clear();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        if let Ok(value) = serde_json::from_str::<Value>(&line) {
            visit(value);
        }
    }
    Ok(())
}
pub fn list(filter: &Filter) -> Result<Vec<Value>> {
    let mut records = VecDeque::new();
    events(|entry| {
        if let Some(record) = summary(&entry, filter) {
            records.push_back(record);
            if records.len() > filter.limit {
                records.pop_front();
            }
        }
    })?;
    Ok(records.into_iter().rev().collect())
}
fn summary(entry: &Value, filter: &Filter) -> Option<Value> {
    if entry["event"] != "hook_result" {
        return None;
    }
    let d = &entry["data"];
    if filter
        .decision
        .as_deref()
        .is_some_and(|s| d["output"]["decision"] != s)
        || filter.tool.as_deref().is_some_and(|s| d["tool"] != s)
        || filter
            .conversation
            .as_deref()
            .is_some_and(|s| d["conversation_id"] != s)
    {
        return None;
    }
    Some(json!({"id":entry["id"], "timestamp":entry["timestamp"],
            "tool":d["tool"], "conversation_id":d["conversation_id"], "decision":d["output"]["decision"],
            "command":d["command"], "cwd":d["cwd"],
            "reason":d["output"]["reason"], "stage":d["stage"], "duration_ms":d["duration_ms"]}))
}
pub fn show(id: &str) -> Result<Value> {
    let mut records = Vec::new();
    events(|entry| {
        if entry["id"] == id {
            records.push(entry);
        }
    })?;
    if records.is_empty() {
        bail!("No approval logs found for ID {id}");
    }
    Ok(json!({"id":id,"events":records}))
}
pub fn print_list(filter: &Filter, json_output: bool) -> Result<()> {
    let records = list(filter)?;
    if json_output {
        println!("{}", serde_json::to_string_pretty(&records)?);
    } else if records.is_empty() {
        println!("No approval logs found.");
    } else {
        for record in records {
            print_record(&record, false);
        }
    }
    Ok(())
}
fn print_record(record: &Value, json_output: bool) {
    if json_output {
        println!("{record}");
    } else {
        // Escape control characters supplied by tool names/reasons before terminal display.
        let text = |key: &str| -> String {
            record[key]
                .as_str()
                .unwrap_or("")
                .chars()
                .flat_map(|c| {
                    if c.is_control() {
                        c.escape_default().collect::<Vec<_>>()
                    } else {
                        vec![c]
                    }
                })
                .collect()
        };
        println!(
            "{}  {}  {:9}  {}  stage={}\n  {}",
            text("timestamp"),
            text("id"),
            text("decision"),
            text("tool"),
            text("stage"),
            text("reason")
        );
        for key in ["command", "cwd"] {
            if record[key].as_str().is_some_and(|s| !s.is_empty()) {
                println!("  {key}: {}", text(key));
            }
        }
    }
}

/// Incremental reader retaining incomplete lines and reopening replaced/truncated files.
#[derive(Default)]
struct Tail {
    identity: Option<(u64, u64)>,
    offset: u64,
    pending: Vec<u8>,
}
impl Tail {
    fn poll(&mut self, mut visit: impl FnMut(Value)) -> Result<()> {
        let path = config::log_dir().join("approvals.jsonl");
        let mut file = match File::open(path) {
            Ok(file) => file,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(e.into()),
        };
        FileExt::lock_shared(&file)?;
        let metadata = file.metadata()?;
        FileExt::unlock(&file)?;
        let identity = (metadata.dev(), metadata.ino());
        if self.identity != Some(identity) || metadata.len() < self.offset {
            self.identity = Some(identity);
            self.offset = 0;
            self.pending.clear();
        }
        file.seek(SeekFrom::Start(self.offset))?;
        let mut reader = BufReader::new(file.take(metadata.len() - self.offset));
        loop {
            let n = reader.read_until(b'\n', &mut self.pending)?;
            self.offset += n as u64;
            if n == 0 {
                break;
            }
            if self.pending.last() == Some(&b'\n') {
                if let Ok(entry) = serde_json::from_slice(&self.pending) {
                    visit(entry);
                }
                self.pending.clear();
            }
        }
        Ok(())
    }
}
pub async fn follow(filter: &Filter, json_output: bool) -> Result<()> {
    let mut tail = Tail::default();
    let mut recent = VecDeque::new();
    tail.poll(|entry| {
        if let Some(record) = summary(&entry, filter) {
            recent.push_back(record);
            if recent.len() > filter.limit {
                recent.pop_front();
            }
        }
    })?;
    // Follow mode prints oldest first, including the initial snapshot.
    for record in recent {
        print_record(&record, json_output);
    }
    std::io::stdout().flush()?;
    let mut interval = tokio::time::interval(std::time::Duration::from_millis(200));
    loop {
        tokio::select! {
            signal = tokio::signal::ctrl_c() => { signal?; return Ok(()); }
            _ = interval.tick() => {
                tail.poll(|entry| {
                    if let Some(record) = summary(&entry, filter) { print_record(&record, json_output); }
                })?;
                std::io::stdout().flush()?;
            }
        }
    }
}
