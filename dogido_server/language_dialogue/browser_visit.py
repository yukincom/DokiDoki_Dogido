"""一回のブラウザー引き継ぎ。前面アプリの実測は呼出元の責務。"""

from dataclasses import dataclass


WELCOME_BACK = "おかえり！ どうやった？ 気になったこと、オレにも聞かせてや。"


@dataclass
class BrowserVisit:
    token: str
    away: bool = False
    returned: bool = False
    greeted: bool = False
    refreshed: bool = False

    def observe(self, active):
        if not active:
            self.away = True
        elif self.away:
            self.returned = True
