//! 明るさ・天気・場所・モブ・匂いの観測から、環境反応の候補と発話機会を選ぶ。
//! dangerは危険度、ambientは平常時の反応、precipitationは現在地の雨雪、projectionは共有文脈を担当する。
//! reactionは選ばれた機会のモデル向け材料と返答検査を持つ。通信・再生・取消はdialogue側が実行する。
pub mod ambient;
pub mod danger;
pub mod precipitation;
pub mod projection;
pub(crate) mod reaction;
