//! 身体の興味候補を同じ世代の実測と照合し、モデルの限定選択を期限付き命令へ変える。
//! モデル呼出し・プロンプト・発話・実移動は呼出元が所有し、ここは入力検証と接続内の状態を持つ。
mod contracts;
mod state;
pub use contracts::{
    ActionResult, Activity, Candidate, CandidateKind, Mode, Observation, Observed, Outcome,
};
pub use state::{Prepared, State};

#[cfg(test)]
mod tests;
