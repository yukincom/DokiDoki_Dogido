//! 身体のserver実測と身体の眼による視認を型検証し、接続ごとの現在・前回へ投影する。
//! 入力はHTTP body_observation、出力は会話用データ。移動・発話判断・永続保存は行わない。
pub mod contracts;
pub mod evaluation;
pub mod senses;
mod state;
pub use state::State;
#[cfg(test)]
pub(crate) mod tests;
