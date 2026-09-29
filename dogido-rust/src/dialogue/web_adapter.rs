//! session所有の休眠Chromeアダプタ。stdioプロセスは初回要求時だけ起動する。
use crate::language::web::{Proposal, SearchResult};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{path::PathBuf, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{mpsc, oneshot, watch},
};

#[derive(Clone, Debug)]
pub struct Config {
    pub python: PathBuf,
    pub helper: PathBuf,
    pub timeout: Duration,
}
struct Request {
    value: Value,
    cancel: watch::Receiver<bool>,
    reply: oneshot::Sender<Result<Value>>,
}
#[derive(Clone)]
pub struct Client {
    sender: mpsc::Sender<Request>,
    stop: watch::Sender<bool>,
}
impl Client {
    /// jobはhostのshutdown台帳へ登録する。Cloneは同じsessionの寿命を共有する。
    pub fn new(config: Config) -> (Self, tokio::task::JoinHandle<()>) {
        let (sender, rx) = mpsc::channel(1);
        let (stop, stopped) = watch::channel(false);
        let job = tokio::spawn(run(config, rx, stopped));
        (Self { sender, stop }, job)
    }
    pub fn close(&self) {
        let _ = self.stop.send(true);
    }
    async fn call(&self, mut value: Value, cancel: &watch::Receiver<bool>) -> Result<Value> {
        ensure!(
            !*self.stop.borrow() && !*cancel.borrow() && cancel.has_changed().is_ok(),
            "web cancelled"
        );
        value["request_id"] = format!("web:{}", uuid::Uuid::new_v4().simple()).into();
        let (reply, result) = oneshot::channel();
        self.sender
            .send(Request {
                value,
                cancel: cancel.clone(),
                reply,
            })
            .await
            .context("web adapter stopped")?;
        result.await.context("web adapter ended")?
    }
    pub async fn inspect(&self, cancel: &watch::Receiver<bool>) -> Result<Value> {
        let reply = self.call(json!({"op":"inspect"}), cancel).await?;
        ensure!(
            reply["availability"]["available"].is_boolean()
                && reply["availability"]["reason"].is_string(),
            "invalid web availability"
        );
        Ok(reply["availability"].clone())
    }
    pub async fn search(
        &self,
        proposal: &Proposal,
        cancel: &watch::Receiver<bool>,
    ) -> Result<SearchResult> {
        let reply = self
            .call(json!({"op":"search","proposal":proposal}), cancel)
            .await?;
        ensure!(
            reply.get("error").is_none(),
            "web adapter {}",
            reply["error"]
        );
        serde_json::from_value(reply["result"].clone()).context("invalid web result")
    }
}
async fn cancelled(cancel: &mut watch::Receiver<bool>) {
    loop {
        if *cancel.borrow_and_update() {
            return;
        }
        if cancel.changed().await.is_err() {
            return;
        }
    }
}
struct Process {
    child: Child,
    input: ChildStdin,
    output: tokio::io::Lines<BufReader<ChildStdout>>,
}
impl Process {
    fn spawn(config: &Config) -> Result<Self> {
        let mut command = Command::new(&config.python);
        command
            .arg(&config.helper)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.as_std_mut().process_group(0);
        }
        let mut child = command.spawn().context("start web adapter")?;
        let input = child.stdin.take().context("web stdin")?;
        let output = BufReader::new(child.stdout.take().context("web stdout")?).lines();
        tracing::info!(event="web_adapter_started",pid=?child.id());
        Ok(Self {
            child,
            input,
            output,
        })
    }
    async fn exchange(&mut self, value: &Value) -> Result<Value> {
        self.input
            .write_all(format!("{value}\n").as_bytes())
            .await?;
        self.input.flush().await?;
        let line = self.output.next_line().await?.context("web adapter EOF")?;
        ensure!(line.len() <= 1_000_000, "web adapter frame too large");
        let reply: Value = serde_json::from_str(&line)?;
        ensure!(
            reply["request_id"] == value["request_id"],
            "wrong web adapter request"
        );
        Ok(reply)
    }
    async fn close(mut self) {
        let pid = self.child.id();
        #[cfg(unix)]
        if let Some(pid) = pid.map(|p| p as libc::pid_t) {
            let shutdown = async {
                let _ = self
                    .input
                    .write_all(b"{\"op\":\"close\",\"request_id\":\"shutdown\"}\n")
                    .await;
                let _ = self.input.shutdown().await;
                // wait/try_waitはここでは使わない。終了したleaderもreapせずPIDを保持する。
                loop {
                    if child_exited_without_reaping(pid)? {
                        return std::io::Result::Ok(());
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            };
            let _ = tokio::time::timeout(Duration::from_secs(15), shutdown).await;
            // 正常closeには上の猶予を与え、異常exit/close失敗/timeoutの残存SDK子も回収する。
            // WNOWAITで親子関係を再確認。leaderをまだreapしていないのでPIDは再利用されない。
            // spawn成功時にprocess_group(0)で確定したPGIDを使う。macOSでは未reapの
            // 終了leaderでもgetpgidがESRCHとなるため、終了後にPGIDを取り直さない。
            if child_exited_without_reaping(pid).is_ok() {
                unsafe {
                    libc::kill(-pid, libc::SIGKILL);
                    // leader自身がgroupを変更した場合も、未reapの所有PIDだけを止める。
                    libc::kill(pid, libc::SIGKILL);
                }
            }
            // groupへのsignalは必ずreap前で終える。wait後のPID/PGIDには一切触れない。
            let _ = self.child.wait().await;
        }
        #[cfg(not(unix))]
        {
            let shutdown = async {
                let _ = self
                    .input
                    .write_all(b"{\"op\":\"close\",\"request_id\":\"shutdown\"}\n")
                    .await;
                let _ = self.input.shutdown().await;
                self.child.wait().await
            };
            if tokio::time::timeout(Duration::from_secs(15), shutdown)
                .await
                .is_err()
            {
                let _ = self.child.kill().await;
                let _ = self.child.wait().await;
            }
        }
        tracing::info!(event = "web_adapter_stopped", ?pid);
    }
}
#[cfg(unix)]
fn child_exited_without_reaping(pid: libc::pid_t) -> std::io::Result<bool> {
    loop {
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let status = unsafe {
            libc::waitid(
                libc::P_PID,
                pid as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        if status == 0 {
            return Ok(unsafe { info.si_pid() } == pid);
        }
        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

async fn run(
    config: Config,
    mut receiver: mpsc::Receiver<Request>,
    mut stop: watch::Receiver<bool>,
) {
    let mut process: Option<Process> = None;
    loop {
        let next = tokio::select! {biased;_=cancelled(&mut stop)=>None,r=receiver.recv()=>r};
        let Some(mut request) = next else { break };
        if *request.cancel.borrow() || request.cancel.has_changed().is_err() {
            let _ = request.reply.send(Err(anyhow::anyhow!("web cancelled")));
            continue;
        }
        if process.is_none() {
            match Process::spawn(&config) {
                Ok(p) => process = Some(p),
                Err(e) => {
                    let _ = request.reply.send(Err(e));
                    continue;
                }
            }
        }
        let outcome = tokio::select! {biased;
            _=cancelled(&mut stop)=>Err(anyhow::anyhow!("web adapter closed")),
            _=cancelled(&mut request.cancel)=>Err(anyhow::anyhow!("web cancelled")),
            result=tokio::time::timeout(config.timeout,process.as_mut().unwrap().exchange(&request.value))=>match result{Ok(r)=>r,Err(_)=>Err(anyhow::anyhow!("web timeout"))},
        };
        if outcome.is_err()
            && let Some(p) = process.take()
        {
            p.close().await;
        }
        let _ = request.reply.send(outcome);
    }
    if let Some(p) = process {
        p.close().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn config() -> (Config, Fixture) {
        let dir = std::env::temp_dir().join(format!("dogido-web-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&dir).unwrap();
        let helper = dir.join("mock.py");
        fs::write(&helper,r#"import json,os,sys
for line in sys.stdin:
    v=json.loads(line)
    if v['op']=='close': break
    if v['op']=='inspect':
        print(json.dumps({'request_id':v['request_id'],'availability':{'available':True,'reason':str(os.getpid())}}),flush=True)
    elif v['op']=='search':
        # 実処理中でもclose/EOFを受け取り、終了できる模擬adapter。
        sys.stdin.readline()
        break
"#).unwrap();
        (
            Config {
                python: "/usr/bin/python3".into(),
                helper,
                timeout: Duration::from_secs(3),
            },
            Fixture(dir),
        )
    }
    fn proposal() -> Proposal {
        Proposal {
            question: "狐".into(),
            target: "狐".into(),
            facet: "meaning".into(),
            search_terms: vec!["狐".into()],
            web_query: "狐".into(),
            trigger_reason: "explicit_request".into(),
            known_urls: vec![],
        }
    }
    #[tokio::test]
    async fn adapter_remains_dormant_and_explicit_close_joins_child() {
        let (config, _files) = config();
        let (client, job) = Client::new(config);
        let (_tx, rx) = watch::channel(false);
        let availability = client.inspect(&rx).await.unwrap();
        let pid = availability["reason"]
            .as_str()
            .unwrap()
            .parse::<i32>()
            .unwrap();
        assert_eq!(unsafe { libc::kill(pid, 0) }, 0);
        #[cfg(unix)]
        assert_eq!(unsafe { libc::getpgid(pid) }, pid);
        client.close();
        tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    }
    #[tokio::test]
    async fn cancellation_ends_inflight_search_and_reaps_child() {
        let (config, _files) = config();
        let (client, job) = Client::new(config);
        let (tx, rx) = watch::channel(false);
        let availability = client.inspect(&rx).await.unwrap();
        let pid = availability["reason"]
            .as_str()
            .unwrap()
            .parse::<i32>()
            .unwrap();
        let c = client.clone();
        let search = tokio::spawn(async move { c.search(&proposal(), &rx).await });
        tokio::task::yield_now().await;
        tx.send(true).unwrap();
        assert!(
            tokio::time::timeout(Duration::from_secs(3), search)
                .await
                .unwrap()
                .unwrap()
                .is_err()
        );
        client.close();
        tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    }
}

#[cfg(all(test, unix))]
mod descendant_tests {
    use super::*;
    use std::{fs, process::Child as StdChild};
    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    struct Unrelated(StdChild);
    impl Drop for Unrelated {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    fn fixture(mode: &str) -> (Config, Fixture) {
        let root =
            std::env::temp_dir().join(format!("dogido-web-descendant-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("mode"), mode).unwrap();
        let helper = root.join("mock.py");
        fs::write(&helper, r#"import json,os,subprocess,sys,time
from pathlib import Path
root=Path(__file__).parent
mode=(root/'mode').read_text()
child=None
for line in sys.stdin:
    request=json.loads(line)
    if request['op']=='inspect':
        child=subprocess.Popen(['/bin/sleep','60'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        (root/'pids').write_text(str(os.getpid())+' '+str(child.pid))
        print(json.dumps({'request_id':request['request_id'],'availability':{'available':True,'reason':'ready'}}),flush=True)
    elif request['op']=='search':
        (root/'searching').write_text('started')
        if mode=='abrupt_exit': os._exit(1)
        # cancellation still reaches this thread through the next close frame.
    elif request['op']=='close':
        if mode=='close_exception': raise RuntimeError('simulated SDK close failure')
        time.sleep(.2)
        child.terminate()
        child.wait()
        (root/'graceful').write_text('SDK close completed')
        break
"#).unwrap();
        (
            Config {
                python: "/usr/bin/python3".into(),
                helper,
                timeout: Duration::from_secs(3),
            },
            Fixture(root),
        )
    }
    async fn wait_path(path: &std::path::Path) {
        tokio::time::timeout(Duration::from_secs(3), async {
            while !path.exists() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
    }
    fn pids(files: &Fixture) -> Vec<libc::pid_t> {
        fs::read_to_string(files.0.join("pids"))
            .unwrap()
            .split_whitespace()
            .map(|p| p.parse().unwrap())
            .collect()
    }
    async fn disappeared(ids: &[libc::pid_t]) {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if ids.iter().all(|pid| unsafe { libc::kill(*pid, 0) } == -1
                    && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)) { break; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.expect("adapter and owned SDK child must disappear");
    }
    fn proposal() -> Proposal {
        Proposal {
            question: "狐".into(),
            target: "狐".into(),
            facet: "meaning".into(),
            search_terms: vec!["狐".into()],
            web_query: "狐".into(),
            trigger_reason: "explicit_request".into(),
            known_urls: vec![],
        }
    }
    async fn finish(client: &Client, job: tokio::task::JoinHandle<()>) {
        client.close();
        tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap();
    }
    #[tokio::test]
    async fn normal_close_gives_sdk_time_to_reap_its_child() {
        let (config, files) = fixture("normal");
        let (client, job) = Client::new(config);
        let (_tx, rx) = watch::channel(false);
        client.inspect(&rx).await.unwrap();
        let ids = pids(&files);
        finish(&client, job).await;
        assert!(files.0.join("graceful").exists());
        disappeared(&ids).await;
    }
    #[tokio::test]
    async fn abrupt_adapter_exit_reaps_owned_sdk_child_before_pid_release() {
        use std::os::unix::process::CommandExt;
        let unrelated = Unrelated(
            std::process::Command::new("/bin/sleep")
                .arg("60")
                .process_group(0)
                .spawn()
                .unwrap(),
        );
        let (config, files) = fixture("abrupt_exit");
        let mut process = Process::spawn(&config).unwrap();
        process
            .exchange(&json!({"op":"inspect", "request_id":"test"}))
            .await
            .unwrap();
        let ids = pids(&files);
        assert_eq!(unsafe { libc::getpgid(ids[0]) }, ids[0]);
        assert_eq!(unsafe { libc::getpgid(ids[1]) }, ids[0]);
        assert!(
            process
                .exchange(&json!({"op":"search", "request_id":"search"}))
                .await
                .is_err()
        );
        tokio::time::timeout(Duration::from_secs(3), async {
            while !child_exited_without_reaping(ids[0]).unwrap() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        // Repeated observation does not consume the exit; the leader still anchors the PGID.
        assert!(child_exited_without_reaping(ids[0]).unwrap());
        assert_eq!(unsafe { libc::kill(-ids[0], 0) }, 0);
        process.close().await;
        disappeared(&ids).await;
        assert_eq!(unsafe { libc::kill(unrelated.0.id() as libc::pid_t, 0) }, 0);
    }
    #[tokio::test]
    async fn adapter_close_exception_cannot_leave_sdk_child_alive() {
        let (config, files) = fixture("close_exception");
        let (client, job) = Client::new(config);
        let (_tx, rx) = watch::channel(false);
        client.inspect(&rx).await.unwrap();
        let ids = pids(&files);
        finish(&client, job).await;
        assert!(!files.0.join("graceful").exists());
        disappeared(&ids).await;
    }
    #[tokio::test]
    async fn cancelled_search_closes_adapter_and_its_sdk_child() {
        let (config, files) = fixture("cancel");
        let (client, job) = Client::new(config);
        let (tx, rx) = watch::channel(false);
        client.inspect(&rx).await.unwrap();
        let ids = pids(&files);
        let c = client.clone();
        let search = tokio::spawn(async move { c.search(&proposal(), &rx).await });
        wait_path(&files.0.join("searching")).await;
        tx.send(true).unwrap();
        assert!(
            tokio::time::timeout(Duration::from_secs(3), search)
                .await
                .unwrap()
                .unwrap()
                .is_err()
        );
        finish(&client, job).await;
        assert!(files.0.join("graceful").exists());
        disappeared(&ids).await;
    }
}
