"""調整成績によるcheckpoint選択と、改善のない連続epochを管理する。"""
from dataclasses import dataclass


@dataclass
class EarlyStopping:
    # 0は早期停止なし。選択順は完全一致数→span F1、同点なら前のcheckpointを保持。
    patience: int = 0
    best: tuple[int, float] = (-1, -1.0)
    stale_epochs: int = 0

    def __post_init__(self):
        if self.patience < 0:
            raise ValueError('early stopping patience must be non-negative')

    def observe(self, key: tuple[int, float]) -> bool:
        if key > self.best:
            self.best = key
            self.stale_epochs = 0
            return True
        self.stale_epochs += 1
        return False

    @property
    def should_stop(self) -> bool:
        return self.patience > 0 and self.stale_epochs >= self.patience
