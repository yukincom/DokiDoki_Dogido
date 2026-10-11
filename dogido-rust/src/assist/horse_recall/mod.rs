//! Minecraft由来の所有馬候補と、今回の明示依頼を結ぶ限定支援。
//! モデルは対象と依頼の意味を選ぶ。個体・所有・現在性の照合、命令配送はコードが所有する。
pub mod contracts;
pub mod transport;
pub use contracts::{Candidate, Snapshot};

pub const CAPABILITY: &str = "integrated_server.horse.recall.v1";
