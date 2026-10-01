# 戦闘結果から会話の名前許可への接点

`Engine::observe` の直後に `Engine::take_name_updates()` を一度呼び、同じ受理済みの
生イベントとともに `ChatObservationMemory::observe` へ渡す。`Outcomes` 単体にも
同じ drain API がある。戻り値は `NameOutcomeUpdate` で、現在の存在や撃破の判断ではない。

```text
受信・重複/sequence検査
  → combat.observe(raw_event, ...)
  → names = combat.take_name_updates()
  → chat_observation.observe(raw_event, names, labels)
```

次のイベントを observe すると未取得の更新は置き換わる。キューではないので、
遅い戦闘音声処理や `take_notes` のタイミングで取り出さない。
2回目の take は空。snapshot の読取りや返答生成から再投入しない。

## 材料の範囲

- `confirmed_types`: `hostile_defeated` / `creeper_detonated` / `combat_ended` に含まれる、
  初めて受け取った明示 outcome の種ID。殺した主体、爆発、自然死を一括で撃破扱いしない。
- 即時通知の名前は全 outcome を対象にする。発話側の「爆散イベントでは爆散だけ」などの
  選択と分けるのが元Pythonの名前記憶の規則。発話と短期の戦闘ノートは変更しない。
- `combat_ended` は残敵やボス確認があって安堵を発話できなくても名前更新を確定する。
  `aftermath` の発話可否や再生成功から名前を作らない。
- `legacy_disappeared_types`: `hostile_outcomes=None` の旧adapterだけ。変更前の mode、
  戦闘hint、設定以内の直近被弾を元どおり使い、生の視認ID差分から名前を取り出す。
  明示された空配列 `[]` では消失推定をしない。音だけの部分イベントへ前の full frame を補わない。
- 旧adapterの追跡ID表は毎イベントで更新し、combat_ended 後に空にする。
  既知の次元同士の切替では旧追跡を捨てる。Noneを挟む規則も元Pythonどおり。

消費した個体IDを visual/hearing memo から除く処理は `chat_observation` が所有する。
名前更新が空の再配送でも、同じ raw event の consumed ID 除去は実施する。
IDが一つでもある場合はIDだけ、全件IDなしの場合だけ種名で除く従来契約を変更しない。

## 正本との差を明示する点

Python は combat_ended の未通知結果を名前へ入れても即時通知の台帳へ追加せず、
戦闘終了/プレイヤー死亡/次元変更でその台帳を消す。そのため同じ死亡通知の再配送で
名前の10秒期限が延び得る。新hookは指定どおり名前専用の session 台帳でこれを防ぐ。
名前台帳を更新することは、Rust の既存戦闘台帳・撃破カウント・台詞・ノートを変更しない。

Rust の戦闘台帳は既存どおり4096件で古いキーを除く。一方、名前台帳は session 中は
期限や件数で除去しない。除去すれば古い再配送が新しい名前更新になるため。
session を破棄すると台帳も破棄する。次元の戦闘resetでは名前台帳だけ引き継ぎ、
名前表示の期限と次元フィルターは引き続き `chat_observation` が所有する。

現Fabricは明示outcomeに UUID を送る。IDなしの明示outcomeは既存Rustと同じ
種/結果/evidenceの合成キーしかなく、同じ型の別個体と再配送を区別できない。
その場合も再配送による延命を避ける側に固定する。死亡欄自体が無い旧adapterの
再視認→再消失は独立した視認差分なので、別の名前更新として扱われる。

## 検証境界

元Pythonの持続する状態機械を47系列・199 stepで実行し、名前更新時のmodeと変更結果を採取。
168 stepは一致。31 stepは上記の再配送抑止だけの意図した差として、元結果と新結果を
fixtureに両方保存する。modeは正本の更新境界値を入力し、RustとPythonの戦闘mode全体が
一致したという主張にはしない。

新規7テストは系列比較に加え、Engine経由、TTL境界、部分イベント、明示空結果、
発話/ノートとの分離、次元変更、4096件超の再配送、memoからの消費ID除去を確認する。
実Minecraft、実音声、モデルは呼ばない。Sessionへの呼出配線は別の統合工程。
