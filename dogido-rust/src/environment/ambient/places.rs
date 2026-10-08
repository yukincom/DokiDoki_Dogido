use super::*;
#[derive(Clone, Default)]
pub(super) struct Places {
    structure: Option<String>,
}
impl Places {
    pub fn current_structure(&self) -> Option<&str> {
        self.structure.as_deref()
    }
    pub fn update(&mut self, e: &GameEvent) {
        if e.event.name == EventName::StatusSnapshot {
            self.structure = e
                .world
                .structure
                .as_deref()
                .map(|id| {
                    crate::chat_catalog::strip(id.rsplit(':').next().unwrap_or("")).to_lowercase()
                })
                .filter(|s| !s.is_empty());
        }
    }
    pub fn dimension_changed(&mut self) {
        self.structure = None;
    }
}
