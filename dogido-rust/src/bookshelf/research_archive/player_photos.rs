//! プレイヤーが選んだF2画像。自動撮影の研究原本とは別の、非上書き画像bundle。
use super::*;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum PlayerPhotoTarget {
    Poem {
        poem_id: String,
        original_created_at: DateTime<Utc>,
    },
    Encyclopedia {
        entry_id: String,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PlayerPhoto {
    pub id: String,
    pub filename: String,
    pub sha256: String,
    pub width: u32,
    pub height: u32,
}

impl PlayerPhoto {
    fn key(&self) -> Result<&str> {
        let key = self
            .id
            .strip_prefix("f2-")
            .context("invalid_player_photo_id")?;
        ensure!(
            valid_hash(key) && valid_hash(&self.sha256),
            "invalid_player_photo_hash"
        );
        ensure!(
            crate::screenshot::valid_filename(&self.filename)
                && !self.filename.starts_with("dogido_"),
            "invalid_player_photo_filename"
        );
        Ok(key)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ArchivedPlayerPhoto {
    pub schema_version: u32,
    pub archive_id: String,
    pub target: PlayerPhotoTarget,
    pub photo: PlayerPhoto,
    pub record_sha256: String,
}

#[cfg(unix)]
fn entry_component(id: &str) -> Result<String> {
    ensure!(!id.is_empty() && id.len() <= 160, "invalid_encyclopedia_id");
    // ExFATでも使える、衝突しない可逆表記。コロン等をunderscoreへ潰さない。
    let mut name = String::from("entry-");
    for byte in id.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_') {
            name.push(char::from(byte));
        } else {
            name.push_str(&format!("%{byte:02X}"));
        }
    }
    ensure!(name.len() <= 240, "invalid_encyclopedia_id");
    Ok(name)
}

impl Archive {
    #[cfg(unix)]
    fn player_photo_root(
        &self,
        root: &safe::Dir,
        target: &PlayerPhotoTarget,
        create: bool,
    ) -> Result<safe::Dir> {
        match target {
            PlayerPhotoTarget::Poem {
                poem_id,
                original_created_at,
            } => {
                let (poem, _) = self
                    .for_poem(poem_id, *original_created_at)?
                    .poem_root(root, create)?;
                let photos = poem.child("player-images", create)?;
                if create {
                    poem.sync()?;
                    let scope = self.for_poem(poem_id, *original_created_at)?;
                    root.child(&scope.year, false)?.sync()?;
                    root.sync()?;
                }
                Ok(photos)
            }
            PlayerPhotoTarget::Encyclopedia { entry_id } => {
                let parent = root.child("encyclopedia", create)?;
                let name = entry_component(entry_id)?;
                let identity = json!({"schema_version":VERSION,"archive_id":self.config.archive_id,"entry_id":entry_id});
                let entry = match parent.child(&name, false) {
                    Ok(entry) => {
                        let old = entry
                            .read("identity.json", MAX_RECORD)?
                            .context("encyclopedia_identity_missing")?;
                        ensure!(
                            serde_json::from_slice::<Value>(&old)? == identity,
                            "encyclopedia_identity_conflict"
                        );
                        entry
                    }
                    Err(error) if not_found(&error) && create => {
                        let entry = parent.child(&name, true)?;
                        entry.once("identity.json", &encoded(&identity)?)?;
                        entry.sync()?;
                        parent.sync()?;
                        root.sync()?;
                        entry
                    }
                    Err(error) => return Err(error),
                };
                let photos = entry.child("photos", create)?;
                if create {
                    entry.sync()?;
                    parent.sync()?;
                    root.sync()?;
                }
                Ok(photos)
            }
        }
    }

    pub fn store_player_photo(
        &self,
        target: &PlayerPhotoTarget,
        photo: &PlayerPhoto,
        png: &[u8],
    ) -> Result<ArchivedPlayerPhoto> {
        let key = photo.key()?;
        ensure!(hash(png) == photo.sha256, "player_photo_changed");
        ensure!(
            png_dimensions(png)? == (photo.width, photo.height),
            "player_photo_dimensions_changed"
        );
        let bytes = encoded(
            &json!({"schema_version":VERSION,"archive_id":self.config.archive_id,
            "target":target,"photo":photo,"origin":"minecraft_f2_filename"}),
        )?;
        let result = ArchivedPlayerPhoto {
            schema_version: VERSION,
            archive_id: self.config.archive_id.clone(),
            target: target.clone(),
            photo: photo.clone(),
            record_sha256: hash(&bytes),
        };
        #[cfg(unix)]
        {
            let root = self.checked_root()?;
            let _lock = root.lock(LOCK, false)?;
            let parent = self.player_photo_root(&root, target, true)?;
            let bundle = parent.child(key, true)?;
            if bundle.read("commit.json", MAX_RECORD)?.is_some() {
                self.verified_player_photo(&bundle, &result)?;
            }
            // 同一bundleの再送でも全fileと全階層を同期する。前回の同期失敗後に
            // commitだけ見えている状態を、元画像を消せる成功へ昇格させない。
            bundle.once("record.json", &bytes)?;
            bundle.once("image.png", png)?;
            bundle.sync()?;
            bundle.once("commit.json", &encoded(&serde_json::to_value(&result)?)?)?;
            bundle.sync()?;
            parent.sync()?;
            root.sync()?;
            self.verified_player_photo(&bundle, &result)?;
            Ok(result)
        }
        #[cfg(not(unix))]
        {
            let _ = key;
            anyhow::bail!("archive_platform_unsupported")
        }
    }

    pub fn read_player_photo(&self, record: &ArchivedPlayerPhoto) -> Result<Vec<u8>> {
        let key = record.photo.key()?;
        #[cfg(unix)]
        {
            let root = self.checked_root()?;
            let parent = self.player_photo_root(&root, &record.target, false)?;
            self.verified_player_photo(&parent.child(key, false)?, record)
        }
        #[cfg(not(unix))]
        {
            let _ = key;
            anyhow::bail!("archive_platform_unsupported")
        }
    }

    #[cfg(unix)]
    fn verified_player_photo(
        &self,
        bundle: &safe::Dir,
        wanted: &ArchivedPlayerPhoto,
    ) -> Result<Vec<u8>> {
        ensure!(
            wanted.schema_version == VERSION && wanted.archive_id == self.config.archive_id,
            "player_photo_archive_mismatch"
        );
        let committed = bundle
            .read("commit.json", MAX_RECORD)?
            .context("player_photo_incomplete")?;
        ensure!(
            serde_json::from_slice::<ArchivedPlayerPhoto>(&committed)? == *wanted,
            "player_photo_commit_conflict"
        );
        let record = bundle
            .read("record.json", MAX_RECORD)?
            .context("player_photo_incomplete")?;
        ensure!(
            hash(&record) == wanted.record_sha256,
            "player_photo_record_changed"
        );
        let value: Value = serde_json::from_slice(&record)?;
        ensure!(
            value
                == json!({"schema_version":VERSION,"archive_id":self.config.archive_id,
            "target":wanted.target,"photo":wanted.photo,"origin":"minecraft_f2_filename"}),
            "player_photo_record_conflict"
        );
        let bytes = bundle
            .read("image.png", MAX_PNG)?
            .context("player_photo_incomplete")?;
        ensure!(
            hash(&bytes) == wanted.photo.sha256
                && png_dimensions(&bytes)? == (wanted.photo.width, wanted.photo.height),
            "player_photo_changed"
        );
        Ok(bytes)
    }
}
