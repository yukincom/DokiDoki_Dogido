use super::*;
use std::fs;

const HELPER: &str = r#"
import json,sys,os,time
from pathlib import Path
x=json.loads(sys.stdin.readline())
Path(x['pidfile']).write_text(str(os.getpid()))
def ask(op,**kw):
 print(json.dumps(dict(op=op,**kw)),flush=True)
 if op=='result': return {}
 return json.loads(sys.stdin.readline())
if x['case']=='before_plan':
 ask('chat_ground',input={})
 time.sleep(30)
 sys.exit()
p={'action':'check_entity_presence','focus':'現在の対象','entity_query':'ヤギ','evidence':[{'turn_id':'current','quote':'入力なし'}],
   'confidence':0,'source':'fallback','status':'fallback','presence_challenged':False,'repair':None}
r=ask('plan',input={'schema_version':1,'model':x['model'],'enable_thinking':False,'fallback':p,
 'details':{'allowed_actions':['check_entity_presence'],'history':[],
 'current':{'turn_id':'current','role':'user','text':''},'observations':{'observed_entities':[]},'routing_hints':{}}})
g={'schema_version':1,'source':'current_observation','plan':r['plan'],'topic_hits':[{'entry_id':'goat','label':'ヤギ','score':1}], 'observed_entities':[]}
reply=ask('chat_ground',input=g)
Path(x['readyfile']).write_text('grounded')
if x['case']=='cancel': time.sleep(30)
elif x['case']=='repeat': ask('chat_ground',input=g)
elif x['case']=='leaf': ask('chat_leaf',input={})
else: ask('result',text=reply['fixed_reply'] if x['case']=='ok' else 'ヤギがおるで。')
"#;
#[tokio::test]
async fn chat_grounding_bridge_accepts_native_fixed_text_and_reaps_protocol_failures() {
    for case in ["ok", "before_plan", "repeat", "leaf", "changed"] {
        let dir = std::env::temp_dir().join(format!("dogido-ground-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let helper = dir.join("helper.py");
        fs::write(&helper, HELPER).unwrap();
        let mut config = DialogueConfig {
            python: "/usr/bin/python3".into(),
            helper,
            model: "fixture".into(),
            reading_engine: "off".into(),
            ..Default::default()
        };
        config.haiku.memory_enabled = false;
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_millis(5)).unwrap();
        let (_owner, mut cancel) = watch::channel(false);
        let result=render(&config,&llm,json!({"case":case,"text":"","model":"fixture","pidfile":dir.join("pid"),"readyfile":dir.join("ready")}),&mut cancel).await;
        if case == "ok" {
            let reply = result.unwrap();
            assert_eq!(reply["text"], "今の観測では、ヤギは確認できてへんわ。");
            assert_eq!(reply["llm_reports"][0]["calls"], 0);
        } else {
            assert!(result.is_err(), "{case}");
        }
        assert_reaped(&dir);
        fs::remove_dir_all(&dir).unwrap();
    }
}
fn assert_reaped(dir: &std::path::Path) {
    let pid = fs::read_to_string(dir.join("pid")).unwrap();
    let output = std::process::Command::new("/bin/kill")
        .args(["-0", pid.trim()])
        .output()
        .unwrap();
    assert!(!output.status.success(), "helper still alive: {pid}");
}
#[tokio::test]
async fn chat_grounding_bridge_cancellation_after_grounding_reaps_helper_without_reply() {
    let dir = std::env::temp_dir().join(format!("dogido-ground-cancel-{}", uuid::Uuid::new_v4()));
    fs::create_dir_all(&dir).unwrap();
    let helper = dir.join("helper.py");
    fs::write(&helper, HELPER).unwrap();
    let mut config = DialogueConfig {
        python: "/usr/bin/python3".into(),
        helper,
        model: "fixture".into(),
        ..Default::default()
    };
    config.haiku.memory_enabled = false;
    let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_millis(5)).unwrap();
    let (owner, mut cancel) = watch::channel(false);
    let input = json!({"case":"cancel","text":"","model":"fixture","pidfile":dir.join("pid"),"readyfile":dir.join("ready")});
    let task = tokio::spawn(async move { render(&config, &llm, input, &mut cancel).await });
    tokio::time::timeout(Duration::from_secs(5), async {
        while !dir.join("ready").exists() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    owner.send(true).unwrap();
    let result = tokio::time::timeout(Duration::from_secs(4), task)
        .await
        .unwrap()
        .unwrap();
    assert!(result.unwrap_err().to_string().contains("cancelled"));
    assert_reaped(&dir);
    fs::remove_dir_all(&dir).unwrap();
}
