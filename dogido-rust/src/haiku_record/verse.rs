//! Shared line identities and a borrowed, validated three-line record.
//! Meter, editing authority and adoption remain the owning domain's policy.
use super::HaikuLine;
use anyhow::{Result, ensure};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LinePosition {
    Upper,
    Middle,
    Lower,
}

impl LinePosition {
    pub const ALL: [Self; 3] = [Self::Upper, Self::Middle, Self::Lower];
    pub const fn index(self) -> usize {
        match self {
            Self::Upper => 0,
            Self::Middle => 1,
            Self::Lower => 2,
        }
    }
    pub const fn metadata(self) -> (&'static str, &'static str, &'static str) {
        match self {
            Self::Upper => ("line_1", "upper", "上五"),
            Self::Middle => ("line_2", "middle", "中七"),
            Self::Lower => ("line_3", "lower", "下五"),
        }
    }
    pub fn matches(self, line: &HaikuLine) -> bool {
        let (id, position, name) = self.metadata();
        line.line_index == self.index()
            && line.line_id == id
            && line.position == position
            && line.canonical_name == name
    }
}

pub fn reading(lines: &[HaikuLine]) -> String {
    lines
        .iter()
        .map(|l| l.reading_text.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}
pub fn surface(lines: &[HaikuLine]) -> String {
    lines
        .iter()
        .map(|l| l.surface_text.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

/// Construction verifies the existing emission contract. A Vec stays legal for
/// unresolved legacy records and pending ideas; they are not silently promoted.
#[derive(Debug, Clone, Copy)]
pub struct ResolvedLines<'a>(&'a [HaikuLine; 3]);

impl<'a> TryFrom<&'a [HaikuLine]> for ResolvedLines<'a> {
    type Error = anyhow::Error;
    fn try_from(lines: &'a [HaikuLine]) -> Result<Self> {
        let lines: &[HaikuLine; 3] = lines
            .try_into()
            .map_err(|_| anyhow::anyhow!("prepared haiku must contain three canonical lines"))?;
        for position in LinePosition::ALL {
            let index = position.index();
            let line = &lines[index];
            ensure!(
                position.matches(line),
                "invalid canonical haiku line identity at {index}"
            );
            for text in [&line.surface_text, &line.reading_text] {
                ensure!(
                    !text.trim().is_empty() && !text.contains(['\n', '\r']),
                    "empty or multiline canonical haiku line at {index}"
                );
            }
            ensure!(
                !line.provenance.trim().is_empty(),
                "missing line provenance at {index}"
            );
            ensure!(
                line.source_atom_ids.iter().all(|id| !id.trim().is_empty()),
                "empty source atom id at {index}"
            );
        }
        Ok(Self(lines))
    }
}

impl ResolvedLines<'_> {
    pub fn reading(self) -> String {
        reading(self.0)
    }
    pub fn surface(self) -> String {
        surface(self.0)
    }
}
