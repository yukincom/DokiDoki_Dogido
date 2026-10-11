//! 身体のserver実測を型検証し、接続ごとの自他位置と前回値へ投影する。
//! 入力はHTTP body_observation、出力は会話用データ。移動・発話判断・永続保存は行わない。
pub mod contracts;
pub mod evaluation;
mod state;
pub use state::State;
#[cfg(test)]
mod tests;
