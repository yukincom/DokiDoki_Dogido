//! Explicit, bounded world assistance. No I/O or model/tool execution occurs here.
//! The caller supplies a fresh, complete snapshot and preserves combat speech priority.
pub mod intent;
pub mod selection;
mod state;
pub use selection::{RiskPolicy, SelectHotbarCommand, WeaponSelection, select_weapon_slot};
pub use state::*;
pub const SELECT_HOTBAR_CAPABILITY: &str = "client.hotbar.select.v1";
pub const SELECT_SWORD_RULE_VERSION: &str = "2026-09-02.1";
#[cfg(test)]
mod tests;
