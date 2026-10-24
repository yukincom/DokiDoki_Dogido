//! 検証済み撮影画像と撮影時の全記録を、明示設定した研究保存先へ一度だけ保存する。
//! setupだけが識別札と設定を作る。通常処理は既存root・識別札・固定lockを確認し、
//! 原句のJST日時でYYYY/MMDDHHMMへ分け、capture UUIDのbundleへ記録／画像／完了札の順で追記する。
//! 同じ分に別の原句があればsuffixを足さず停止する。GETからは呼ばない。
//! 不変性は本モジュールの非上書き契約であり、ExFATのchmodや物理的な書込禁止ではない。
use crate::screenshot::Receipt;
use anyhow::{Context, Result, ensure};
use chrono::{DateTime, Datelike, FixedOffset, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

mod player_photos;
mod internal_representations;
pub use internal_representations::{CaptureFile, local_capture_file};
pub use player_photos::{ArchivedPlayerPhoto, PlayerPhoto, PlayerPhotoTarget};

const VERSION: u32 = 1;
const CONFIG: &str = "research-archive.json";
const MARKER: &str = ".dogido-research-archive.json";
const LOCK: &str = ".research-archive.lock";
const MAX_PNG: usize = 64 * 1024 * 1024;
const MAX_RECORD: usize = 16 * 1024 * 1024;

/// memory/bookshelf内だけに保存する私有設定。rootはsetup前に明示作成済みであること。
/// volume_uuidは照合済み実機の記録用であり、このモジュールによる物理volume検査ではない。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub schema_version: u32,
    pub root: PathBuf,
    pub archive_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub volume_uuid: Option<String>,
}

impl Config {
    pub fn new(root: PathBuf, archive_id: String, volume_uuid: Option<String>) -> Result<Self> {
        let config = Self {
            schema_version: VERSION,
            root,
            archive_id,
            volume_uuid,
        };
        config.validate()?;
        Ok(config)
    }
    fn validate(&self) -> Result<()> {
        ensure!(self.schema_version == VERSION, "archive_config_version");
        ensure!(self.root.is_absolute(), "archive_root_not_absolute");
        uuid::Uuid::parse_str(&self.archive_id).context("archive_invalid_id")?;
        if let Some(id) = &self.volume_uuid {
            uuid::Uuid::parse_str(id).context("archive_invalid_volume_id")?;
        }
        Ok(())
    }
    #[cfg(unix)]
    fn marker(&self) -> Value {
        json!({"schema_version":VERSION,"archive_id":self.archive_id,"volume_uuid":self.volume_uuid})
    }
}

/// 保存先の設定値だけを保持する。各I/Oでroot・識別札を開き直すため、再接続にも対応する。
/// 設定変更後はcallerがloadし直す。既存handleが新しい保存先へ黙って切り替わることはない。
#[derive(Clone, Debug)]
pub struct Archive {
    config: Config,
}

/// 原句IDと元作成時刻だけから研究用の一句を指定する。生成後の編集日時では移動しない。
#[derive(Clone, Debug)]
pub struct PoemArchive {
    archive: Archive,
    poem_id: String,
    original_created_at: DateTime<Utc>,
    year: String,
    minute: String,
}

/// 一句の研究記録snapshot。relative_pathは表示用で、入力パスとして読み戻さない。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ArchivedSnapshot {
    pub archive_id: String,
    pub poem_id: String,
    pub sha256: String,
    pub relative_path: String,
}

/// 呼出側の登録台帳に記録する保存結果。ファイルシステムの絶対パスは公開しない。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ArchivedCapture {
    pub schema_version: u32,
    pub archive_id: String,
    pub capture_id: String,
    pub sha256: String,
    pub record_sha256: String,
    pub byte_length: u64,
    pub width: u32,
    pub height: u32,
}

/// 明示setup。既存rootを使用し、同一marker/configは再使用するが、異なる内容は上書きしない。
/// 初回setup中にクラッシュして不完全なファイルが残った場合も、自動削除せず診断を返す。
pub fn configure(memory: &Path, config: &Config) -> Result<Archive> {
    configure_with_expected(memory, config, None)
}

/// 保存先変更は現在設定との一致確認を必須にする。旧rootの画像・記録は移動も削除もしない。
/// expected=Noneは未設定または同一設定の再送だけ。別設定への変更にはload_configの値を渡す。
pub fn configure_with_expected(
    memory: &Path,
    config: &Config,
    expected: Option<&Config>,
) -> Result<Archive> {
    config.validate()?;
    #[cfg(unix)]
    {
        let root = safe::Dir::open(&config.root)?;
        let shelf = safe::Dir::open(memory)?.child("bookshelf", true)?;
        let _local_lock = shelf.lock(".research-archive-config.lock", true)?;
        let current = shelf
            .read(CONFIG, MAX_RECORD)?
            .map(|bytes| serde_json::from_slice::<Config>(&bytes))
            .transpose()?;
        ensure!(
            current.as_ref() == Some(config) || current.as_ref() == expected,
            "archive_config_conflict"
        );
        if let Some(bytes) = root.read(MARKER, MAX_RECORD)? {
            ensure!(
                serde_json::from_slice::<Value>(&bytes)? == config.marker(),
                "archive_marker_mismatch"
            );
        }
        let _lock = root.lock(LOCK, true)?;
        if let Some(bytes) = root.read(MARKER, MAX_RECORD)? {
            ensure!(serde_json::from_slice::<Value>(&bytes)? == config.marker(), "archive_marker_mismatch");
            root.existing_file(MARKER)?.sync_all()?;
        } else {
            root.once(MARKER, &encoded(&config.marker())?)?;
        }
        root.sync()?;
        let bytes = encoded(&serde_json::to_value(config)?)?;
        if current.as_ref() == Some(config) {
            // 既存設定は値で照合済み。空白やkey順の違いを接続失敗にしない。
            shelf.existing_file(CONFIG)?.sync_all()?;
        } else if current.is_some() {
            shelf.replace_config(&bytes)?;
        } else {
            shelf.once(CONFIG, &bytes)?;
        }
        shelf.sync()?;
        Ok(Archive {
            config: config.clone(),
        })
    }
    #[cfg(not(unix))]
    {
        let _ = (memory, expected);
        anyhow::bail!("archive_platform_unsupported")
    }
}

/// 設定が無い場合だけNone。切断・marker不一致・破損はErrであり、代替保存先は作らない。
/// 読み取り専用だがディスクI/Oを伴うので、HTTP GETの投影生成ではなくworkerから呼ぶ。
pub fn load(memory: &Path) -> Result<Option<Archive>> {
    let Some(config) = load_config(memory)? else {
        return Ok(None);
    };
    open(config).map(Some)
}

/// 保存済み写真の旧保存先を読む場合も、固定された識別札を照合する。作成・設定変更はしない。
pub fn open(config: Config) -> Result<Archive> {
    config.validate()?;
    let archive = Archive { config };
    #[cfg(unix)]
    {
        archive.checked_root()?;
        Ok(archive)
    }
    #[cfg(not(unix))]
    {
        let _ = archive;
        anyhow::bail!("archive_platform_unsupported")
    }
}

/// SSD切断中にも設定画面へ選択中のrootを表示するための読込。外付けrootには触れない。
pub fn load_config(memory: &Path) -> Result<Option<Config>> {
    #[cfg(unix)]
    {
        let shelf = match safe::Dir::open(memory).and_then(|dir| dir.child("bookshelf", false)) {
            Ok(dir) => dir,
            Err(error) if not_found(&error) => return Ok(None),
            Err(error) => return Err(error),
        };
        let Some(bytes) = shelf.read(CONFIG, MAX_RECORD)? else {
            return Ok(None);
        };
        let config: Config = serde_json::from_slice(&bytes).context("archive_invalid_config")?;
        config.validate()?;
        Ok(Some(config))
    }
    #[cfg(not(unix))]
    {
        let _ = memory;
        anyhow::bail!("archive_platform_unsupported")
    }
}

/// 保存先の再選択時だけ既存識別札を読む。通常のworkerは設定済み識別子を必ず照合する。
pub fn existing_config(root: &Path) -> Result<Option<Config>> {
    #[cfg(unix)]
    {
        let Some(bytes) = safe::Dir::open(root)?.read(MARKER, MAX_RECORD)? else {
            return Ok(None);
        };
        let marker: Value = serde_json::from_slice(&bytes)?;
        ensure!(
            marker["schema_version"] == VERSION,
            "archive_config_version"
        );
        Ok(Some(Config::new(
            root.into(),
            marker["archive_id"]
                .as_str()
                .context("archive_invalid_id")?
                .into(),
            marker["volume_uuid"].as_str().map(Into::into),
        )?))
    }
    #[cfg(not(unix))]
    {
        let _ = root;
        anyhow::bail!("archive_platform_unsupported")
    }
}

impl Archive {
    pub fn config(&self) -> &Config {
        &self.config
    }

    /// 保存完了時に記録したheadを読む。hash名の辞書順で最新版を推測しない。
    /// pathは閉じた書式に検証し、原句identity・全bytesのhashを照合する。書込みなし。
    pub fn read_snapshot(&self, head: &ArchivedSnapshot) -> Result<Value> {
        ensure!(head.archive_id == self.config.archive_id, "archive_snapshot_scope");
        ensure!(valid_hash(&head.sha256), "archive_invalid_hash");
        let parts: Vec<_> = head.relative_path.split('/').collect();
        ensure!(parts.len() == 4
            && parts[0].len() == 4 && parts[0].bytes().all(|b| b.is_ascii_digit())
            && parts[1].len() == 8 && parts[1].bytes().all(|b| b.is_ascii_digit())
            && parts[2] == "snapshots" && parts[3] == format!("{}.json", head.sha256),
            "archive_invalid_snapshot_path");
        #[cfg(unix)]
        {
            let root = self.checked_root()?;
            let dir = root.child(parts[0], false)?.child(parts[1], false)?;
            let identity: Value = serde_json::from_slice(&dir.read("identity.json", MAX_RECORD)?
                .context("archive_poem_identity_missing")?)?;
            let at = DateTime::parse_from_rfc3339(identity["original_created_at"].as_str()
                .context("archive_missing_original_date")?)?.with_timezone(&Utc);
            let scoped = self.for_poem(&head.poem_id, at)?;
            ensure!(identity == scoped.identity() && scoped.year == parts[0] && scoped.minute == parts[1],
                "archive_poem_identity_conflict");
            let bytes = dir.child("snapshots", false)?.read(parts[3], MAX_RECORD)?
                .context("archive_snapshot_missing")?;
            ensure!(hash(&bytes) == head.sha256, "archive_snapshot_changed");
            let value: Value = serde_json::from_slice(&bytes)?;
            ensure!(value["identity"] == identity && value["record"].is_object(), "archive_snapshot_conflict");
            Ok(value["record"].clone())
        }
        #[cfg(not(unix))]
        { anyhow::bail!("archive_platform_unsupported") }
    }

    /// I/Oなしのscope作成。JST元作成日時をYYYY/MMDDHHMMへ整形する。秒は名前に含めない。
    pub fn for_poem(
        &self,
        poem_id: &str,
        original_created_at: DateTime<Utc>,
    ) -> Result<PoemArchive> {
        ensure!(
            !poem_id.trim().is_empty() && poem_id.len() <= 4096,
            "archive_invalid_poem_id"
        );
        let jst = original_created_at.with_timezone(&FixedOffset::east_opt(9 * 3600).unwrap());
        ensure!(
            (1..=9999).contains(&jst.year()),
            "archive_invalid_created_at"
        );
        Ok(PoemArchive {
            archive: self.clone(),
            poem_id: poem_id.into(),
            original_created_at,
            year: jst.format("%Y").to_string(),
            minute: jst.format("%m%d%H%M").to_string(),
        })
    }

    #[cfg(unix)]
    fn checked_root(&self) -> Result<safe::Dir> {
        let root = safe::Dir::open(&self.config.root).context("archive_unavailable")?;
        let bytes = root
            .read(MARKER, MAX_RECORD)?
            .context("archive_marker_missing")?;
        ensure!(
            serde_json::from_slice::<Value>(&bytes)? == self.config.marker(),
            "archive_marker_mismatch"
        );
        root.existing_file(LOCK)?;
        Ok(root)
    }
}

impl PoemArchive {
    fn identity(&self) -> Value {
        json!({"schema_version":VERSION,"archive_id":self.archive.config.archive_id,
            "poem_id":self.poem_id,"original_created_at":self.original_created_at})
    }

    /// callerが作った原句・対話・メモ等の全JSONを、内容hash名の追加snapshotへ保存する。
    /// 内容の収集や状態変更はcallerの担当。既存snapshot・原本は変更しない。
    pub fn snapshot(&self, record: &Value) -> Result<ArchivedSnapshot> {
        ensure!(record.is_object(), "archive_snapshot_not_object");
        let bytes = encoded(&json!({"identity":self.identity(),"record":record}))?;
        ensure!(bytes.len() <= MAX_RECORD, "archive_record_too_large");
        let sha256 = hash(&bytes);
        #[cfg(unix)]
        {
            let root = self.archive.checked_root()?;
            let _lock = root.lock(LOCK, false)?;
            let (poem, directory) = self.poem_root(&root, true)?;
            let snapshots = poem.child("snapshots", true)?;
            snapshots.once(&format!("{sha256}.json"), &bytes)?;
            snapshots.sync()?;
            poem.sync()?;
            Ok(ArchivedSnapshot {
                archive_id: self.archive.config.archive_id.clone(),
                poem_id: self.poem_id.clone(),
                relative_path: format!("{directory}/snapshots/{sha256}.json"),
                sha256,
            })
        }
        #[cfg(not(unix))]
        {
            let _ = sha256;
            anyhow::bail!("archive_platform_unsupported")
        }
    }

    #[cfg(unix)]
    fn poem_root(&self, root: &safe::Dir, create: bool) -> Result<(safe::Dir, String)> {
        let year = root.child(&self.year, create)?;
        let wanted = self.identity();
        // 既存の原句identityだけで選ぶ。記録中のpathや変更後の時刻は使用しない。
        match year.child(&self.minute, false) {
            Ok(dir) => {
                let bytes = dir
                    .read("identity.json", MAX_RECORD)?
                    .context("archive_poem_identity_missing")?;
                ensure!(
                    serde_json::from_slice::<Value>(&bytes)? == wanted,
                    "archive_poem_identity_conflict"
                );
                return Ok((dir, format!("{}/{}", self.year, self.minute)));
            }
            Err(error) if not_found(&error) => {}
            Err(error) => return Err(error),
        }
        ensure!(create, "archive_poem_missing");
        let dir = year.child(&self.minute, true)?;
        dir.once("identity.json", &encoded(&wanted)?)?;
        dir.child("internal-representations", true)?.sync()?;
        dir.sync()?;
        year.sync()?;
        root.sync()?;
        Ok((dir, format!("{}/{}", self.year, self.minute)))
    }

    /// 同じcommand、receipt、provenance、画像の再送は同じ結果になる。provenanceは丸ごと保持する。
    /// receipt.directory/filename/poem_idは記録用であり、保存パスの組み立てや入力画像取得には使わない。
    /// 呼出側が同一handleで検査済みの画像bytesを渡す。本処理もhashとPNG構造を再検査する。
    pub fn store(
        &self,
        command: &str,
        receipt: &Receipt,
        provenance: &Value,
        png: &[u8],
    ) -> Result<ArchivedCapture> {
        ensure!(
            receipt.poem_id == self.poem_id,
            "archive_capture_poem_mismatch"
        );
        let key = capture_key(command)?;
        let (width, height) = png_dimensions(png)?;
        ensure!(
            valid_hash(&receipt.sha256) && hash(png) == receipt.sha256,
            "archive_image_hash_mismatch"
        );
        ensure!(provenance.is_object(), "archive_provenance_not_object");
        let record = json!({"schema_version":VERSION,"archive_id":self.archive.config.archive_id,"poem":self.identity(),
            "capture_id":command,"receipt":receipt,"provenance":provenance,
            "image":{"sha256":receipt.sha256,"byte_length":png.len(),"width":width,"height":height}});
        let record_bytes = encoded(&record)?;
        ensure!(record_bytes.len() <= MAX_RECORD, "archive_record_too_large");
        let result = ArchivedCapture {
            schema_version: VERSION,
            archive_id: self.archive.config.archive_id.clone(),
            capture_id: command.into(),
            sha256: receipt.sha256.clone(),
            record_sha256: hash(&record_bytes),
            byte_length: png.len() as u64,
            width,
            height,
        };
        #[cfg(unix)]
        {
            let root = self.archive.checked_root()?;
            let _lock = root.lock(LOCK, false)?;
            // 切断や親pathの差替え後にも同じ開いたdirectoryへしか書かない。
            // 識別札はlock取得後も照合し、別の保管庫への登録を避ける。
            ensure!(
                serde_json::from_slice::<Value>(
                    &root
                        .read(MARKER, MAX_RECORD)?
                        .context("archive_marker_missing")?
                )? == self.archive.config.marker(),
                "archive_marker_mismatch"
            );
            let (poem, _) = self.poem_root(&root, true)?;
            let captures = poem.child("original-images", true)?;
            let bundle = captures.child(&key, true)?;
            if bundle.read("commit.json", MAX_RECORD)?.is_some() {
                let (existing, _, _) = self.verified(&bundle, command, &receipt.sha256)?;
                ensure!(existing == result, "archive_capture_conflict");
                return Ok(existing);
            }
            // 完了札のない既存bundleは同一bytesだけ再使用する。部分破損は手動復旧対象。
            bundle.once("record.json", &record_bytes)?;
            bundle.once("image.png", png)?;
            bundle.sync()?;
            bundle.once("commit.json", &encoded(&serde_json::to_value(&result)?)?)?;
            bundle.sync()?;
            captures.sync()?;
            poem.sync()?;
            let (saved, _, _) = self.verified(&bundle, command, &receipt.sha256)?;
            ensure!(saved == result, "archive_capture_conflict");
            Ok(saved)
        }
        #[cfg(not(unix))]
        {
            let _ = (key, result);
            anyhow::bail!("archive_platform_unsupported")
        }
    }

    /// workerで使う読み取り。画像と全記録のhash・identity・完了札を再照合してから返す。
    /// リクエストのファイルパスを受け取らず、安全なcapture IDだけを使う。書込みは一切しない。
    pub fn read(&self, command: &str, expected_hash: &str) -> Result<Vec<u8>> {
        #[cfg(unix)]
        {
            let key = capture_key(command)?;
            let root = self.archive.checked_root()?;
            let (poem, _) = self.poem_root(&root, false)?;
            let bundle = poem.child("original-images", false)?.child(&key, false)?;
            Ok(self.verified(&bundle, command, expected_hash)?.1)
        }
        #[cfg(not(unix))]
        {
            let _ = (command, expected_hash);
            anyhow::bail!("archive_platform_unsupported")
        }
    }

    /// 研究用の完全なreceipt/provenanceを検証して読む。GET用の要約cacheとは分離する。
    pub fn record(&self, command: &str, expected_hash: &str) -> Result<Value> {
        #[cfg(unix)]
        {
            let key = capture_key(command)?;
            let root = self.archive.checked_root()?;
            let (poem, _) = self.poem_root(&root, false)?;
            let bundle = poem.child("original-images", false)?.child(&key, false)?;
            Ok(self.verified(&bundle, command, expected_hash)?.2)
        }
        #[cfg(not(unix))]
        {
            let _ = (command, expected_hash);
            anyhow::bail!("archive_platform_unsupported")
        }
    }

    #[cfg(unix)]
    fn verified(
        &self,
        bundle: &safe::Dir,
        command: &str,
        expected: &str,
    ) -> Result<(ArchivedCapture, Vec<u8>, Value)> {
        ensure!(valid_hash(expected), "archive_invalid_hash");
        let committed = bundle
            .read("commit.json", MAX_RECORD)?
            .context("archive_incomplete")?;
        let commit: ArchivedCapture =
            serde_json::from_slice(&committed).context("archive_invalid_commit")?;
        ensure!(
            commit.schema_version == VERSION
                && commit.archive_id == self.archive.config.archive_id
                && commit.capture_id == command
                && commit.sha256 == expected,
            "archive_capture_conflict"
        );
        let raw = bundle
            .read("record.json", MAX_RECORD)?
            .context("archive_incomplete")?;
        ensure!(hash(&raw) == commit.record_sha256, "archive_record_changed");
        let record: Value = serde_json::from_slice(&raw)?;
        ensure!(
            record["schema_version"] == VERSION
                && record["archive_id"] == self.archive.config.archive_id
                && record["poem"] == self.identity()
                && record["capture_id"] == command
                && record["receipt"]["sha256"] == expected
                && record["image"]["sha256"] == expected
                && record["image"]["byte_length"] == commit.byte_length
                && record["image"]["width"] == commit.width
                && record["image"]["height"] == commit.height,
            "archive_record_conflict"
        );
        let png = bundle
            .read("image.png", MAX_PNG)?
            .context("archive_incomplete")?;
        ensure!(
            hash(&png) == expected && png.len() as u64 == commit.byte_length,
            "archive_image_changed"
        );
        ensure!(
            png_dimensions(&png)? == (commit.width, commit.height),
            "archive_image_changed"
        );
        Ok((commit, png, record))
    }
}

fn capture_key(command: &str) -> Result<String> {
    let id = command
        .strip_prefix("shot_")
        .context("archive_invalid_capture_id")?;
    let uuid = uuid::Uuid::parse_str(id).context("archive_invalid_capture_id")?;
    ensure!(
        uuid.hyphenated().to_string() == id,
        "archive_invalid_capture_id"
    );
    Ok(id.into())
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn encoded(value: &Value) -> Result<Vec<u8>> {
    // JSONのkey挿入順だけが異なる再送も同じ記録hashになる。値や配列順は変えない。
    fn canonical(value: &Value) -> Value {
        match value {
            Value::Object(map) => Value::Object(
                map.iter()
                    .map(|(k, v)| (k.clone(), canonical(v)))
                    .collect::<std::collections::BTreeMap<_, _>>()
                    .into_iter()
                    .collect(),
            ),
            Value::Array(items) => Value::Array(items.iter().map(canonical).collect()),
            other => other.clone(),
        }
    }
    Ok(serde_json::to_vec(&canonical(value))?)
}
#[cfg(unix)]
fn not_found(error: &anyhow::Error) -> bool {
    error.chain().any(|e| {
        e.downcast_ref::<std::io::Error>()
            .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound)
    })
}

/// 原寸PNGのsignature・IHDR・寸法・chunk境界/CRC・IDAT/IENDを照合する。画素の再符号化はしない。
/// 圧縮pixelの完全decodeではないため、撮影受信側の同一ファイル検証を置き換える用途ではない。
fn png_dimensions(bytes: &[u8]) -> Result<(u32, u32)> {
    ensure!(
        bytes.len() >= 45 && bytes.len() <= MAX_PNG && &bytes[..8] == b"\x89PNG\r\n\x1a\n",
        "archive_invalid_png"
    );
    let mut offset = 8usize;
    let mut dimensions = None;
    let mut data = false;
    while offset < bytes.len() {
        ensure!(bytes.len() - offset >= 12, "archive_invalid_png");
        let size = u32::from_be_bytes(bytes[offset..offset + 4].try_into()?) as usize;
        ensure!(size <= bytes.len() - offset - 12, "archive_invalid_png");
        let kind = &bytes[offset + 4..offset + 8];
        let body = &bytes[offset + 8..offset + 8 + size];
        let crc = u32::from_be_bytes(bytes[offset + 8 + size..offset + 12 + size].try_into()?);
        ensure!(
            crc32(&bytes[offset + 4..offset + 8 + size]) == crc,
            "archive_invalid_png_crc"
        );
        if dimensions.is_none() {
            ensure!(kind == b"IHDR" && size == 13, "archive_invalid_png");
            let w = u32::from_be_bytes(body[..4].try_into()?);
            let h = u32::from_be_bytes(body[4..8].try_into()?);
            ensure!(
                w > 0 && h > 0 && w <= 16384 && h <= 16384,
                "archive_invalid_png"
            );
            ensure!(
                matches!(
                    (body[9], body[8]),
                    (0, 1 | 2 | 4 | 8 | 16) | (2, 8 | 16) | (3, 1 | 2 | 4 | 8) | (4 | 6, 8 | 16)
                ) && body[10] == 0
                    && body[11] == 0
                    && body[12] <= 1,
                "archive_invalid_png"
            );
            dimensions = Some((w, h));
        } else {
            ensure!(kind != b"IHDR", "archive_invalid_png");
        }
        offset += size + 12;
        if kind == b"IDAT" {
            data = true;
        }
        if kind == b"IEND" {
            ensure!(
                size == 0 && data && offset == bytes.len(),
                "archive_invalid_png"
            );
            return dimensions.context("archive_invalid_png");
        }
    }
    anyhow::bail!("archive_invalid_png")
}
fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = u32::MAX;
    for byte in bytes {
        crc ^= *byte as u32;
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb88320u32 & (0u32.wrapping_sub(crc & 1)));
        }
    }
    !crc
}

/// Directoryを開いたまま相対I/Oを行う。全祖先と最終要素のsymlinkを辿らず、
/// 外付けの切断後に同名のローカルdirectoryへmkdir_allで落下する経路を持たない。
#[cfg(unix)]
mod safe {
    use super::*;
    use std::{
        ffi::CString,
        fs::{File, OpenOptions},
        io::{Read, Write},
        os::{
            fd::{AsRawFd, FromRawFd},
            unix::{ffi::OsStrExt, fs::OpenOptionsExt},
        },
        path::Component,
    };
    pub(super) struct Dir(File);
    impl Dir {
        pub(super) fn open(path: &Path) -> Result<Self> {
            let full = if path.is_absolute() {
                path.to_path_buf()
            } else {
                std::env::current_dir()?.join(path)
            };
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open("/")?;
            let mut dir = Self(file);
            let mut walked = PathBuf::from("/");
            for component in full.components() {
                match component {
                    Component::RootDir | Component::CurDir => {}
                    Component::Normal(name) => {
                        walked.push(name);
                        dir = dir.child_os(name, false).with_context(|| format!("archive_directory_open: {}", walked.display()))?;
                    }
                    _ => anyhow::bail!("archive_unsafe_directory"),
                }
            }
            Ok(dir)
        }
        fn name(name: &std::ffi::OsStr) -> Result<CString> {
            let bytes = name.as_bytes();
            ensure!(
                !bytes.is_empty() && bytes != b"." && bytes != b".." && !bytes.contains(&b'/'),
                "archive_unsafe_name"
            );
            Ok(CString::new(bytes)?)
        }
        fn fd(&self, name: &str, flags: i32) -> Result<File> {
            let name = Self::name(name.as_ref())?;
            // SAFETY: dirfd stays live and name is NUL-terminated; owned fd is closed by File.
            let fd = unsafe {
                libc::openat(
                    self.0.as_raw_fd(),
                    name.as_ptr(),
                    flags | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
                    0o666,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            let file = unsafe { File::from_raw_fd(fd) };
            ensure!(file.metadata()?.is_file(), "archive_unsafe_file");
            Ok(file)
        }
        pub(super) fn child(&self, name: &str, create: bool) -> Result<Self> {
            self.child_os(name.as_ref(), create)
        }
        fn child_os(&self, name: &std::ffi::OsStr, create: bool) -> Result<Self> {
            let name = Self::name(name)?;
            if create {
                let status = unsafe { libc::mkdirat(self.0.as_raw_fd(), name.as_ptr(), 0o777) };
                if status != 0 {
                    let error = std::io::Error::last_os_error();
                    if error.kind() != std::io::ErrorKind::AlreadyExists {
                        return Err(error.into());
                    }
                }
            }
            let fd = unsafe {
                libc::openat(
                    self.0.as_raw_fd(),
                    name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            Ok(Self(unsafe { File::from_raw_fd(fd) }))
        }
        pub(super) fn existing_file(&self, name: &str) -> Result<File> {
            self.fd(name, libc::O_RDONLY)
        }
        pub(super) fn new_file(&self, name: &str) -> Result<File> {
            self.fd(name, libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL)
        }
        pub(super) fn lock(&self, name: &str, create: bool) -> Result<File> {
            let file = self.fd(name, libc::O_RDWR | if create { libc::O_CREAT } else { 0 })?;
            file.lock().context("archive_lock_failed")?;
            Ok(file)
        }
        pub(super) fn read(&self, name: &str, limit: usize) -> Result<Option<Vec<u8>>> {
            let file = match self.existing_file(name) {
                Ok(file) => file,
                Err(error) if not_found(&error) => return Ok(None),
                Err(error) => return Err(error),
            };
            ensure!(
                file.metadata()?.len() <= limit as u64,
                "archive_file_too_large"
            );
            let mut bytes = vec![];
            file.take(limit as u64 + 1).read_to_end(&mut bytes)?;
            ensure!(bytes.len() <= limit, "archive_file_too_large");
            Ok(Some(bytes))
        }
        pub(super) fn once(&self, name: &str, bytes: &[u8]) -> Result<()> {
            if let Some(old) = self.read(name, bytes.len())? {
                ensure!(old == bytes, "archive_existing_file_conflict");
                // 前回がwrite後・sync前に失敗した場合も、同じbytesの再送で同期だけ完了させる。
                self.existing_file(name)?.sync_all()?;
                return Ok(());
            }
            let mut file = self.fd(name, libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL)?;
            file.write_all(bytes)?;
            file.sync_all()?;
            Ok(())
        }
        pub(super) fn replace_config(&self, bytes: &[u8]) -> Result<()> {
            let temp = format!(".research-config-{}.tmp", uuid::Uuid::new_v4());
            self.once(&temp, bytes)?;
            let source = Self::name(temp.as_ref())?;
            let destination = Self::name(CONFIG.as_ref())?;
            // private設定のみ明示CAS変更する。研究原本にはこの置換処理を使わない。
            let status = unsafe {
                libc::renameat(
                    self.0.as_raw_fd(),
                    source.as_ptr(),
                    self.0.as_raw_fd(),
                    destination.as_ptr(),
                )
            };
            if status != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            self.sync()
        }
        pub(super) fn sync(&self) -> Result<()> {
            // 一部FAT系driverはdirectory fsyncを提供しない。個別fileは必ずsync_all済み。
            // その場合も次回読込時にcommit・記録・画像の全hashを照合し、不完全な物を公開しない。
            match self.0.sync_all() {
                Ok(()) => Ok(()),
                Err(e) if matches!(e.raw_os_error(), Some(libc::EINVAL | libc::ENOTSUP)) => Ok(()),
                Err(e) => Err(e.into()),
            }
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::{
        fs,
        os::unix::fs::symlink,
        sync::{Arc, Barrier},
    };

    struct Fixture {
        temp: PathBuf,
        memory: PathBuf,
        root: PathBuf,
        archive: Archive,
        poem: PoemArchive,
    }
    impl Fixture {
        fn new() -> Self {
            let temp =
                std::env::temp_dir().join(format!("dogido-archive-unit-{}", uuid::Uuid::new_v4()));
            fs::create_dir(&temp).unwrap();
            let temp = fs::canonicalize(temp).unwrap();
            let memory = temp.join("memory");
            let root = temp.join("ssd");
            fs::create_dir(&memory).unwrap();
            fs::create_dir(&root).unwrap();
            let config = Config::new(
                root.clone(),
                uuid::Uuid::new_v4().to_string(),
                Some("7D334A05-647F-3723-A8D2-3191ECE5EB59".into()),
            )
            .unwrap();
            let archive = configure(&memory, &config).unwrap();
            let poem = archive
                .for_poem(
                    "session:test:original",
                    "2026-09-12T00:29:59Z".parse().unwrap(),
                )
                .unwrap();
            Self {
                temp,
                memory,
                root,
                archive,
                poem,
            }
        }
        fn command(&self) -> String {
            "shot_c5b00d1b-fb44-4198-85fd-a5f156d19511".into()
        }
        fn receipt(&self, png: &[u8]) -> Receipt {
            serde_json::from_value(json!({"session_id":"test","poem_id":"session:test:original",
                "filename":"dogido_2026-09-12_09.29.59.png","directory":"/original/never-opened",
                "sha256":hash(png),"captured_at":"2026-09-12T00:29:59Z",
                "capture_view":{"viewpoint":"dogido_client_render_eye","body_entity_uuid":"body",
                    "body_epoch":"epoch","owner_uuid":"owner","dimension":"minecraft:overworld","camera_restored":true}})).unwrap()
        }
        fn provenance(&self) -> Value {
            json!({"original_poem":{"id":"original","lines":["元の句","第二行","第三行"]},
            "materials":[{"kind":"player_report","text":"本人のことば"}],"dialogue":[{"raw":"ええな！","completed":true}],
            "unknown_future_field":{"array":[1,null,true]},"note":"完全な記録を残す"})
        }
        fn poem_dir(&self) -> PathBuf {
            self.root.join("2026/09120929")
        }
        fn bundle(&self) -> PathBuf {
            self.poem_dir()
                .join("original-images/c5b00d1b-fb44-4198-85fd-a5f156d19511")
        }
        fn save(&self) -> ArchivedCapture {
            let png = png(0);
            self.poem
                .store(
                    &self.command(),
                    &self.receipt(&png),
                    &self.provenance(),
                    &png,
                )
                .unwrap()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.temp);
        }
    }
    fn png(pixel: u8) -> Vec<u8> {
        fn chunk(result: &mut Vec<u8>, kind: &[u8], body: &[u8]) {
            result.extend((body.len() as u32).to_be_bytes());
            let start = result.len();
            result.extend(kind);
            result.extend(body);
            result.extend(crc32(&result[start..]).to_be_bytes());
        }
        let mut result = b"\x89PNG\r\n\x1a\n".to_vec();
        chunk(
            &mut result,
            b"IHDR",
            &[0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0, 0, 0],
        );
        let raw = [0, pixel, pixel, pixel];
        let mut compressed = vec![0x78, 0x01, 0x01, 4, 0, 0xfb, 0xff];
        compressed.extend(raw);
        let (mut a, mut b) = (1u32, 0u32);
        for value in raw {
            a = (a + u32::from(value)) % 65521;
            b = (b + a) % 65521;
        }
        compressed.extend(((b << 16) | a).to_be_bytes());
        chunk(&mut result, b"IDAT", &compressed);
        chunk(&mut result, b"IEND", &[]);
        result
    }

    #[test]
    fn exact_image_receipt_and_complete_provenance_survive_reload() {
        let f = Fixture::new();
        let bytes = png(0);
        let saved = f.save();
        assert_eq!(fs::read(f.bundle().join("image.png")).unwrap(), bytes);
        let reopened = load(&f.memory)
            .unwrap()
            .unwrap()
            .for_poem(&f.poem.poem_id, f.poem.original_created_at)
            .unwrap();
        assert_eq!(reopened.read(&f.command(), &saved.sha256).unwrap(), bytes);
        let record = reopened.record(&f.command(), &saved.sha256).unwrap();
        assert_eq!(record["receipt"], json!(f.receipt(&bytes)));
        assert_eq!(record["provenance"], f.provenance());
        assert_eq!(saved.width, 1);
        assert_eq!(saved.height, 1);
        assert!(f.poem_dir().join("internal-representations").is_dir());
    }
    #[test]
    fn retry_is_identical_and_never_rewrites_existing_files() {
        let f = Fixture::new();
        let first = f.save();
        let paths = ["image.png", "record.json", "commit.json"].map(|name| f.bundle().join(name));
        let before = paths.each_ref().map(|path| {
            (
                fs::read(path).unwrap(),
                fs::metadata(path).unwrap().modified().unwrap(),
            )
        });
        assert_eq!(f.save(), first);
        for (path, (bytes, modified)) in paths.iter().zip(before) {
            assert_eq!(fs::read(path).unwrap(), bytes);
            assert_eq!(fs::metadata(path).unwrap().modified().unwrap(), modified);
        }
    }
    #[test]
    fn metadata_or_image_conflicts_never_overwrite_original() {
        let f = Fixture::new();
        f.save();
        let before = fs::read(f.bundle().join("record.json")).unwrap();
        let bytes = png(0);
        assert!(
            f.poem
                .store(
                    &f.command(),
                    &f.receipt(&bytes),
                    &json!({"other":"record"}),
                    &bytes
                )
                .is_err()
        );
        let changed = png(9);
        assert!(
            f.poem
                .store(
                    &f.command(),
                    &f.receipt(&changed),
                    &f.provenance(),
                    &changed
                )
                .is_err()
        );
        assert_eq!(fs::read(f.bundle().join("record.json")).unwrap(), before);
        assert_eq!(fs::read(f.bundle().join("image.png")).unwrap(), bytes);
    }
    #[test]
    fn missing_root_never_recreates_it_and_config_remains_readable() {
        let f = Fixture::new();
        let away = f.temp.join("unplugged");
        fs::rename(&f.root, &away).unwrap();
        assert!(load(&f.memory).is_err());
        assert!(load_config(&f.memory).unwrap().is_some());
        let bytes = png(0);
        assert!(
            f.poem
                .store(&f.command(), &f.receipt(&bytes), &f.provenance(), &bytes)
                .is_err()
        );
        assert!(f.poem.snapshot(&f.provenance()).is_err());
        assert!(!f.root.exists());
    }
    #[test]
    fn missing_or_mismatched_marker_does_not_create_poem_directories() {
        let f = Fixture::new();
        fs::remove_file(f.root.join(MARKER)).unwrap();
        assert!(f.poem.snapshot(&f.provenance()).is_err());
        assert!(!f.root.join("2026").exists());
        fs::write(f.root.join(MARKER), b"{}").unwrap();
        assert!(load(&f.memory).is_err());
        assert!(!f.root.join("2026").exists());
    }
    #[test]
    fn missing_configuration_reads_do_not_make_directories() {
        let f = Fixture::new();
        let empty = f.temp.join("unused-memory");
        fs::create_dir(&empty).unwrap();
        assert!(load(&empty).unwrap().is_none());
        assert!(!empty.join("bookshelf").exists());
        let absent = f.temp.join("absent");
        assert!(load(&absent).unwrap().is_none());
        assert!(!absent.exists());
    }
    #[test]
    fn setup_is_idempotent_but_never_overwrites_other_marker_or_config() {
        let f = Fixture::new();
        assert_eq!(
            configure(&f.memory, f.archive.config()).unwrap().config(),
            f.archive.config()
        );
        let mut wrong = f.archive.config.clone();
        wrong.archive_id = uuid::Uuid::new_v4().to_string();
        assert!(configure(&f.memory, &wrong).is_err());
        assert_eq!(load_config(&f.memory).unwrap().unwrap(), f.archive.config);
        let missing = Config::new(
            f.temp.join("missing-root"),
            uuid::Uuid::new_v4().to_string(),
            None,
        )
        .unwrap();
        assert!(configure(&f.memory, &missing).is_err());
        assert!(!missing.root.exists());
    }
    #[test]
    fn explicit_cas_reconfigure_retains_old_archive_even_when_old_root_is_offline() {
        let f = Fixture::new();
        f.save();
        let old = f.archive.config.clone();
        let old_bytes = fs::read(f.bundle().join("image.png")).unwrap();
        let away = f.temp.join("unplugged");
        fs::rename(&f.root, &away).unwrap();
        let next_root = f.temp.join("new-ssd");
        fs::create_dir(&next_root).unwrap();
        let next = Config::new(next_root.clone(), uuid::Uuid::new_v4().to_string(), None).unwrap();
        assert!(configure(&f.memory, &next).is_err());
        assert!(!next_root.join(MARKER).exists());
        configure_with_expected(&f.memory, &next, Some(&old)).unwrap();
        assert_eq!(load_config(&f.memory).unwrap(), Some(next.clone()));
        assert_eq!(
            fs::read(away.join(
                "2026/09120929/original-images/c5b00d1b-fb44-4198-85fd-a5f156d19511/image.png"
            ))
            .unwrap(),
            old_bytes
        );
        let mut stale = old.clone();
        stale.archive_id = uuid::Uuid::new_v4().to_string();
        assert!(configure_with_expected(&f.memory, &old, Some(&stale)).is_err());
        assert!(!f.root.exists());
    }
    #[test]
    fn root_parent_marker_and_lock_symlinks_are_rejected() {
        let f = Fixture::new();
        let link = f.temp.join("root-link");
        symlink(&f.root, &link).unwrap();
        let config = Config::new(link, uuid::Uuid::new_v4().to_string(), None).unwrap();
        assert!(configure(&f.memory, &config).is_err());
        let parent = f.temp.join("parent-link");
        symlink(&f.temp, &parent).unwrap();
        let config =
            Config::new(parent.join("ssd"), uuid::Uuid::new_v4().to_string(), None).unwrap();
        assert!(configure(&f.memory, &config).is_err());
        for name in [MARKER, LOCK] {
            let path = f.root.join(name);
            let aside = f.temp.join(format!("aside-{name}"));
            fs::rename(&path, &aside).unwrap();
            symlink(&aside, &path).unwrap();
            assert!(load(&f.memory).is_err());
            fs::remove_file(&path).unwrap();
            fs::rename(&aside, &path).unwrap();
        }
    }
    #[test]
    fn bundle_image_record_and_identity_symlinks_are_rejected() {
        let f = Fixture::new();
        let saved = f.save();
        for path in [
            f.bundle().join("image.png"),
            f.bundle().join("record.json"),
            f.poem_dir().join("identity.json"),
            f.bundle(),
        ] {
            let aside = f.temp.join(format!("aside-{}", uuid::Uuid::new_v4()));
            fs::rename(&path, &aside).unwrap();
            symlink(&aside, &path).unwrap();
            assert!(f.poem.read(&f.command(), &saved.sha256).is_err());
            fs::remove_file(&path).unwrap();
            fs::rename(&aside, &path).unwrap();
        }
    }
    #[test]
    fn incomplete_matching_bundle_finishes_but_partial_bytes_require_manual_recovery() {
        let f = Fixture::new();
        let saved = f.save();
        fs::remove_file(f.bundle().join("commit.json")).unwrap();
        assert!(f.poem.read(&f.command(), &saved.sha256).is_err());
        assert_eq!(f.save(), saved);
        fs::remove_file(f.bundle().join("commit.json")).unwrap();
        fs::write(f.bundle().join("record.json"), b"{partial").unwrap();
        let bytes = png(0);
        assert!(
            f.poem
                .store(&f.command(), &f.receipt(&bytes), &f.provenance(), &bytes)
                .is_err()
        );
        assert_eq!(
            fs::read(f.bundle().join("record.json")).unwrap(),
            b"{partial"
        );
        assert!(!f.bundle().join("commit.json").exists());
    }
    #[test]
    fn corrupt_committed_image_or_metadata_is_never_served_or_repaired() {
        let f = Fixture::new();
        let saved = f.save();
        fs::write(f.bundle().join("image.png"), png(4)).unwrap();
        assert!(f.poem.read(&f.command(), &saved.sha256).is_err());
        fs::write(f.bundle().join("image.png"), png(0)).unwrap();
        fs::write(f.bundle().join("record.json"), b"{}").unwrap();
        assert!(f.poem.record(&f.command(), &saved.sha256).is_err());
    }
    #[test]
    fn concurrent_identical_writers_commit_one_exact_bundle() {
        let f = Fixture::new();
        let barrier = Arc::new(Barrier::new(8));
        let joins: Vec<_> = (0..8)
            .map(|_| {
                let poem = f.poem.clone();
                let barrier = barrier.clone();
                let command = f.command();
                let bytes = png(0);
                let receipt = f.receipt(&bytes);
                let provenance = f.provenance();
                std::thread::spawn(move || {
                    barrier.wait();
                    poem.store(&command, &receipt, &provenance, &bytes).unwrap()
                })
            })
            .collect();
        let results: Vec<_> = joins.into_iter().map(|join| join.join().unwrap()).collect();
        assert!(results.iter().all(|result| result == &results[0]));
        assert_eq!(fs::read_dir(f.bundle()).unwrap().count(), 3);
    }
    #[test]
    fn concurrent_conflicting_writers_do_not_clobber() {
        let f = Fixture::new();
        let barrier = Arc::new(Barrier::new(2));
        let joins: Vec<_> = (0..2)
            .map(|index| {
                let poem = f.poem.clone();
                let barrier = barrier.clone();
                let command = f.command();
                let bytes = png(index);
                let receipt = f.receipt(&bytes);
                std::thread::spawn(move || {
                    barrier.wait();
                    poem.store(&command, &receipt, &json!({"writer":index}), &bytes)
                })
            })
            .collect();
        let results: Vec<_> = joins.into_iter().map(|join| join.join().unwrap()).collect();
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        let saved = results.into_iter().find_map(Result::ok).unwrap();
        assert_eq!(
            hash(&f.poem.read(&f.command(), &saved.sha256).unwrap()),
            saved.sha256
        );
    }
    #[test]
    fn jst_minute_name_has_no_seconds_and_collisions_fail_closed() {
        let f = Fixture::new();
        let first = f.poem.snapshot(&f.provenance()).unwrap();
        assert!(first.relative_path.starts_with("2026/09120929/snapshots/"));
        let second = f
            .archive
            .for_poem(
                "session:test:different",
                "2026-09-12T00:29:10Z".parse().unwrap(),
            )
            .unwrap();
        assert!(second.snapshot(&json!({"other":1})).is_err());
        assert_eq!(fs::read_dir(f.root.join("2026")).unwrap().count(), 1);
        assert_eq!(f.poem.snapshot(&f.provenance()).unwrap(), first);
    }
    #[test]
    fn snapshots_are_content_addressed_complete_and_key_order_independent() {
        let f = Fixture::new();
        let one: Value =
            serde_json::from_str(r#"{"a":1,"b":{"x":2,"y":3},"dialogue":["話した"]}"#).unwrap();
        let reordered: Value =
            serde_json::from_str(r#"{"dialogue":["話した"],"b":{"y":3,"x":2},"a":1}"#).unwrap();
        let first = f.poem.snapshot(&one).unwrap();
        assert_eq!(f.poem.snapshot(&reordered).unwrap(), first);
        let second = f.poem.snapshot(&json!({"note":"後の追記"})).unwrap();
        assert_ne!(first.sha256, second.sha256);
        let bytes = fs::read(f.root.join(&first.relative_path)).unwrap();
        assert_eq!(hash(&bytes), first.sha256);
        assert_eq!(
            serde_json::from_slice::<Value>(&bytes).unwrap()["record"],
            one
        );
        assert_eq!(
            fs::read_dir(f.poem_dir().join("snapshots"))
                .unwrap()
                .count(),
            2
        );
    }
    #[test]
    fn unsafe_capture_ids_and_invalid_png_are_rejected_before_creating_poem() {
        let f = Fixture::new();
        let bytes = png(0);
        let receipt = f.receipt(&bytes);
        for command in [
            "../bad",
            "shot_../../bad",
            "shot_C5B00D1B-FB44-4198-85FD-A5F156D19511",
        ] {
            assert!(
                f.poem
                    .store(command, &receipt, &f.provenance(), &bytes)
                    .is_err()
            );
        }
        let mut invalid = bytes.clone();
        invalid[20] ^= 1;
        assert!(
            f.poem
                .store(
                    &f.command(),
                    &f.receipt(&invalid),
                    &f.provenance(),
                    &invalid
                )
                .is_err()
        );
        assert!(!f.root.join("2026").exists());
    }
    #[test]
    fn read_never_creates_a_missing_lock_bundle_or_identity() {
        let f = Fixture::new();
        let bytes = png(0);
        assert!(f.poem.read(&f.command(), &hash(&bytes)).is_err());
        assert!(!f.root.join("2026").exists());
        fs::remove_file(f.root.join(LOCK)).unwrap();
        assert!(f.poem.read(&f.command(), &hash(&bytes)).is_err());
        assert!(!f.root.join(LOCK).exists());
    }
    #[test]
    fn configuration_symlink_and_unidentified_existing_poem_directory_are_rejected() {
        let f = Fixture::new();
        let path = f.memory.join("bookshelf").join(CONFIG);
        let aside = f.temp.join("config-original.json");
        fs::rename(&path, &aside).unwrap();
        symlink(&aside, &path).unwrap();
        assert!(load_config(&f.memory).is_err());
        assert!(configure(&f.memory, &f.archive.config).is_err());
        fs::create_dir_all(f.poem_dir()).unwrap();
        fs::write(f.poem_dir().join("personal.txt"), b"keep me").unwrap();
        assert!(f.poem.snapshot(&f.provenance()).is_err());
        assert!(!f.poem_dir().join("identity.json").exists());
        assert_eq!(
            fs::read(f.poem_dir().join("personal.txt")).unwrap(),
            b"keep me"
        );
    }
    #[test]
    fn opened_root_never_follows_a_replacement_path() {
        let f = Fixture::new();
        let root = safe::Dir::open(&f.root).unwrap();
        let moved = f.temp.join("detached-root");
        fs::rename(&f.root, &moved).unwrap();
        fs::create_dir(&f.root).unwrap();
        root.child("owned-test-directory", true).unwrap();
        assert!(moved.join("owned-test-directory").is_dir());
        assert!(!f.root.join("owned-test-directory").exists());
    }
    #[test]
    fn date_components_are_zero_padded_and_jst_year_rolls_over() {
        let f = Fixture::new();
        let poem = f
            .archive
            .for_poem("root:new-year", "2025-12-31T15:04:59Z".parse().unwrap())
            .unwrap();
        let snapshot = poem.snapshot(&json!({"source":"original"})).unwrap();
        assert!(snapshot.relative_path.starts_with("2026/01010004/"));
        assert!(f.root.join("2026/01010004/identity.json").is_file());
    }
}
