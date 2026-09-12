use std::{
    fs,
    io::Write,
    os::unix::fs::PermissionsExt,
    path::Path,
    process::{Command, Stdio},
};

const VERSION: &str = "v0.3.0";
const BINARY: &str = r#"#!/bin/sh
case "$1" in
  --version) echo 'agy-auto-approve 0.3.0';;
  register) printf '%s\n' "$*" >> "$HOME/registered";;
  *) exit 1;;
esac
"#;
fn executable(path: &Path, text: &str) {
    fs::write(path, text).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}
struct Fixture {
    dir: tempfile::TempDir,
    asset: String,
}
impl Fixture {
    fn new(system: &str, machine: &str, target: &str) -> Self {
        let dir = tempfile::tempdir().unwrap();
        for name in ["bin", "assets", "payload", "home", "scratch"] {
            fs::create_dir(dir.path().join(name)).unwrap();
        }
        executable(&dir.path().join("payload/agy-auto-approve"), BINARY);
        let asset = format!("agy-auto-approve-{VERSION}-{target}.tar.gz");
        assert!(
            Command::new("tar")
                .arg("-czf")
                .arg(dir.path().join("assets").join(&asset))
                .arg("-C")
                .arg(dir.path().join("payload"))
                .arg("agy-auto-approve")
                .status()
                .unwrap()
                .success()
        );
        let hash = Command::new("/bin/sh").args(["-c", "if command -v sha256sum >/dev/null 2>&1; then sha256sum \"$1\"; else shasum -a 256 \"$1\"; fi", "hash"])
            .arg(dir.path().join("assets").join(&asset)).output().unwrap();
        assert!(hash.status.success());
        let hash = String::from_utf8(hash.stdout)
            .unwrap()
            .split_whitespace()
            .next()
            .unwrap()
            .to_owned();
        fs::write(
            dir.path().join("assets/SHA256SUMS"),
            format!("{hash}  {asset}\n"),
        )
        .unwrap();
        executable(
            &dir.path().join("bin/uname"),
            &format!(
                "#!/bin/sh\ncase \"$1\" in -s) echo '{system}';; -m) echo '{machine}';; *) exit 1;; esac\n"
            ),
        );
        executable(
            &dir.path().join("bin/curl"),
            r#"#!/bin/sh
printf '%s\n' "$@" >> "$REQUEST_LOG"
output=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output=$2; shift 2;;
    --write-out|--proto|--proto-redir|--retry|--connect-timeout|--max-time) shift 2;;
    --*) shift;;
    *) url=$1; shift;;
  esac
done
case "$url" in
  https://github.com/jjyr/agy-auto-approve/releases/latest)
    printf 'https://github.com/jjyr/agy-auto-approve/releases/tag/%s' "${LATEST_TAG:-v0.3.0}";;
  https://github.com/jjyr/agy-auto-approve/releases/download/*)
    [ "${FAIL_DOWNLOAD:-0}" != 1 ] || exit 22
    cp "$ASSET_DIR/${url##*/}" "$output";;
  *) exit 22;;
esac
"#,
        );
        Self { dir, asset }
    }
    fn command(&self) -> Command {
        let mut c = Command::new("/bin/sh");
        c.arg("-s")
            .arg("--")
            .env("HOME", self.dir.path().join("home"))
            .env_remove("AGY_INSTALL_DIR")
            .env("TMPDIR", self.dir.path().join("scratch"))
            .env(
                "PATH",
                format!(
                    "{}:{}",
                    self.dir.path().join("bin").display(),
                    std::env::var("PATH").unwrap_or_default()
                ),
            )
            .env("ASSET_DIR", self.dir.path().join("assets"))
            .env("REQUEST_LOG", self.dir.path().join("requests"))
            .current_dir(self.dir.path());
        c
    }
    fn run(&self, mut command: Command, flags: &[&str]) -> std::process::Output {
        let mut child = command
            .args(flags)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(include_bytes!("../scripts/install.sh"))
            .unwrap();
        child.wait_with_output().unwrap()
    }
    fn installed(&self) -> std::path::PathBuf {
        self.dir.path().join("home/.local/bin/agy-auto-approve")
    }
    fn assert_clean(&self) {
        assert_eq!(
            fs::read_dir(self.dir.path().join("scratch"))
                .unwrap()
                .count(),
            0
        );
    }
}
#[test]
fn stdin_installer_resolves_and_installs_all_four_platforms() {
    for (system, machine, target) in [
        ("Darwin", "arm64", "aarch64-apple-darwin"),
        ("Darwin", "x86_64", "x86_64-apple-darwin"),
        ("Linux", "aarch64", "aarch64-unknown-linux-musl"),
        ("Linux", "x86_64", "x86_64-unknown-linux-musl"),
    ] {
        let f = Fixture::new(system, machine, target);
        let out = f.run(f.command(), &[]);
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(fs::read_to_string(f.installed()).unwrap(), BINARY);
        assert_eq!(
            fs::read_to_string(f.dir.path().join("home/registered")).unwrap(),
            "register\n"
        );
        let requests = fs::read_to_string(f.dir.path().join("requests")).unwrap();
        assert_eq!(requests.matches("/releases/latest").count(), 1);
        assert!(requests.contains(&format!("/download/{VERSION}/{}", f.asset)));
        assert!(requests.contains(&format!("/download/{VERSION}/SHA256SUMS")));
        f.assert_clean();
    }
}
#[test]
fn explicit_version_skips_latest_and_respects_registration_flags() {
    let f = Fixture::new("Linux", "amd64", "x86_64-unknown-linux-musl");
    let out = f.run(f.command(), &["--version", VERSION, "--cli-only"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        !fs::read_to_string(f.dir.path().join("requests"))
            .unwrap()
            .contains("/latest")
    );
    assert_eq!(
        fs::read_to_string(f.dir.path().join("home/registered")).unwrap(),
        "register --cli-only\n"
    );
    fs::remove_file(f.dir.path().join("home/registered")).unwrap();
    assert!(
        f.run(f.command(), &["--version", VERSION, "--no-register"])
            .status
            .success()
    );
    assert!(!f.dir.path().join("home/registered").exists());
    f.assert_clean();
}
#[test]
fn failed_download_or_checksum_preserves_existing_installation() {
    for failure in ["download", "mismatch", "missing", "duplicate", "version"] {
        let f = Fixture::new("Linux", "x86_64", "x86_64-unknown-linux-musl");
        fs::create_dir_all(f.installed().parent().unwrap()).unwrap();
        fs::write(f.installed(), "previous binary").unwrap();
        let mut c = f.command();
        match failure {
            "download" => {
                c.env("FAIL_DOWNLOAD", "1");
            }
            "mismatch" => fs::write(
                f.dir.path().join("assets/SHA256SUMS"),
                format!("{}  {}\n", "0".repeat(64), f.asset),
            )
            .unwrap(),
            "missing" => fs::write(f.dir.path().join("assets/SHA256SUMS"), "").unwrap(),
            "duplicate" => {
                let p = f.dir.path().join("assets/SHA256SUMS");
                let text = fs::read_to_string(&p).unwrap();
                fs::write(p, text.repeat(2)).unwrap();
            }
            "version" => {
                let new_asset = f.asset.replace(VERSION, "v9.0.0");
                fs::rename(
                    f.dir.path().join("assets").join(&f.asset),
                    f.dir.path().join("assets").join(&new_asset),
                )
                .unwrap();
                let p = f.dir.path().join("assets/SHA256SUMS");
                fs::write(
                    &p,
                    fs::read_to_string(&p).unwrap().replace(VERSION, "v9.0.0"),
                )
                .unwrap();
                c.env("LATEST_TAG", "v9.0.0");
            }
            _ => unreachable!(),
        }
        let out = f.run(c, &[]);
        assert!(!out.status.success(), "{failure}");
        assert_eq!(
            fs::read_to_string(f.installed()).unwrap(),
            "previous binary"
        );
        assert!(!f.dir.path().join("home/registered").exists());
        f.assert_clean();
    }
}
#[test]
fn unsupported_platform_and_invalid_versions_fail_before_download() {
    for (system, machine, flags) in [
        ("Linux", "riscv64", vec![]),
        ("Windows", "x86_64", vec![]),
        ("Linux", "x86_64", vec!["--version", "v0.3.0-beta"]),
        (
            "Linux",
            "x86_64",
            vec!["--binary", "/tmp/example", "--version", VERSION],
        ),
    ] {
        let f = Fixture::new(system, machine, "x86_64-unknown-linux-musl");
        assert!(!f.run(f.command(), &flags).status.success());
        assert!(!f.installed().exists());
        assert!(!f.dir.path().join("requests").exists());
    }
}
