# Cube Dogido Lab

Minecraft Java 1.21.11 / Fabricで動く3Dドギドの身体です。浮遊する既存モデルに、移動・HP・休息・食事・復活を持たせます。

0.4.0では、自分とプレイヤーの位置を同じサーバー時刻で測り、既存adapter・Rustの会話入力へ渡す読取り接続を追加しました。現在／前回の位置理解を確かめる第一段階です。DIRECT操作は0.3.0で追加済み。モデルによる行動選択は後続です。

## 導入

1. Minecraftを終了する。
2. 使用する起動構成の`mods`で旧`cube-dogido-lab`を退避し、`build/libs/cube-dogido-lab-0.4.0.jar`へ置き換える。同じModの新旧を同時に入れない。
3. Minecraft **1.21.11**、Fabric Loader **0.18.4以上**、Fabric APIで起動する。

2D表示用Modと併用できます。2D表示は既存Modの`/dogidohud hide`・`/dogidohud show`で切り替えます。身体単体の試験にHTTPサーバーやLAN公開は不要です。

## 最初の操作

既存の3Dドギドの8ブロック以内で、結び付けたい一体を指定します。

```mcfunction
/dogidobody bind @e[type=dogido_experiment:dogido,sort=nearest,limit=1]
```

持ち主を登録すると、DIRECTで行動指定を待ちます。身体がまだない場合だけ、先に召喚します。

```mcfunction
/summon dogido_experiment:dogido ~ ~1 ~2
```

召喚位置はワールドのZ方向で、プレイヤーの前方とは限りません。

## 身体のルール

- HP上限は20。持ち主がサバイバル／アドベンチャーのときだけ通常ダメージを受けます。クリエイティブ／スペクテイター、持ち主不在中は通常ダメージを受けません。
- ブロックを通過でき、落下・溺れは無効。葉以外の衝突形状に身体が重なり続けると壁内滞在に数えます。
- 壁内の猶予は初期値100tick。その後1HPずつ20tick間隔で損耗します。外へ出る判断は外部の操作に任せます。ダメージ無効のモードでは壁内損耗も無効です。
- `REST`を指定して静止すると、初期値100tick待った後、60tick間隔で1HP回復します。被弾・移動で休息の蓄積は途切れます。HP低下だけでは休みません。
- 食事は一個を実際に消費し、栄養値に相当するHPを上限まで回復します。通常の消費効果と容器の残りも処理します。空腹ゲージや食事アニメーションはありません。
- 持ち主が食べ物を持って身体を右クリックすると、その一個を与えられます。HP満タン・倒れている間は受け取りません。
- フィールドの食料は、落ちている可食アイテム、実のあるスイートベリー／グロウベリー、成熟したニンジン／ジャガイモ／ビートルートを扱います。植物は食べた後、実なし／成長初期へ戻ります。
- HP0で倒れ、初期値100tick後に**持ち主の現在座標**で全回復して復活します。個体の所有関係を保持し、経験値・通常ドロップを発生させません。新しい世代で行動指定を待ちます。
- 持ち主不在・復活位置未ロードの場合は理由を返して復活を待ちます。身体が未ロードの場合、読み込み直後まで時間は進まず、別個体を自動作成しません。

20tickは通常動作時の約1秒です。ポーズやサーバーの処理速度により実時間は変わります。時間・回復量は実機調整用の初期値です。

DIRECTでは火・溶岩や敵からの距離を理由に移動先を差し替えず、同一次元で遠く離れただけでは自動ワープしません。生きたプレイヤーやモブとの物理衝突、読み込み範囲とワールド境界は移動側で検査します。

## 身体単体の試験コマンド

| コマンド | 内容 |
|---|---|
| `/dogidobody direct` | DIRECTの待機へ切り替える |
| `/dogidobody move ~ ~1 ~4` | 指定座標へ移動する。`~`はコマンド実行者が基準。身体から32ブロック以内 |
| `/dogidobody rest` | 現在地で休息する |
| `/dogidobody wait` | 現在地で待つ。休息回復は行わない |
| `/dogidobody food` | 身体から8ブロック以内の、見通せる食料候補IDを表示する |
| `/dogidobody eat <food_id>` | 表示された一件を指定して食べる。現在2ブロック以内で届くことが必要 |
| `/dogidobody call` | 持ち主の正面へ呼び寄せる |
| `/dogidobody status` | 動作・HP・壁内時間・復活待ち・位置を表示する |
| `/dogidobody unbind` | 持ち主との結び付けを解除する |

`food`の候補は100tick以内に再照合します。遠い食べ物には、まず移動が必要です。

従来の`follow`・`explore`・`retreat`・`safe`・`hold`は、**決め打ちの動きを比較する試験コマンド**として残しています。これらには0.2.0の目標選択・危険回避が含まれます。`direct`へ戻すと、指定された行動だけを実行します。本線の外部状態指定はDIRECTに限定します。

### 調整

```mcfunction
/dogidobody tune speed 0.14
/dogidobody tune wall_grace_ticks 100
/dogidobody tune wall_damage_interval 20
/dogidobody tune wall_damage 1
/dogidobody tune rest_delay_ticks 100
/dogidobody tune rest_heal_interval 60
/dogidobody tune rest_healing 1
/dogidobody tune respawn_delay_ticks 100
```

調整値は個体の読み込み中だけ有効です。再読み込みや別次元への再生成では初期値へ戻ります。所有者・HP・倒れている状態・復活までの残り時間・復活回数・壁内時間は保存されます。休息や移動の指示は再読み込み後に指定し直します。

## ビルドと接続

Java 21以上でビルドします。

```sh
./gradlew --offline test build
```

既存キャッシュがない環境では、通常の`./gradlew test build`で依存関係を取得します。

- `build/libs/cube-dogido-lab-0.4.0.jar`：Minecraftへ入れるMod
- `build/libs/cube-dogido-lab-0.4.0-body-api.jar`：adapterのコンパイル用。`mods`へ入れない
- `build/libs/cube-dogido-lab-0.4.0-sources.jar`：ソース

[API v3と最小例](docs/body-api.md) · [本線へ合体するときの対応表・検証手順](docs/mainline-integration.md)

## 確認状態

2026-10-12に本線mainへの統合・対応Modの配置・普段のサーバーappとMinecraftの接続を確認しました。[導入記録と次の実機操作](../../../docs/2026-10-12-body-p1-install.md)を参照してください。

移動・時間ルール・要求検証は自動テスト、Minecraft APIへの配線はコンパイルで検査しています。0.4.0のゲーム内描画、被弾、食事、保存再開、復活、次元転送、C2S/S2C実通信は実機確認待ちです。Rust／既存adapterへの位置観測接続はコードと自動テストまで。実モデルの位置理解・行動選択は未確認です。[P1の検証範囲と実機手順](../../../docs/2026-10-11-body-integration-plan.md#p1-静止した身体から自分とプレイヤーの位置関係を把握する)を参照してください。

食料の見通し観測と身体のHP等は実装済みです。身体位置からの一般的なモブ視認・スメル・構造物センサーは`NOT_IMPLEMENTED`として区別します。花・雷雨・就寝の専用行動は未実装です。

元モデルは`source-assets/`にあります。既存のモデル・テクスチャ・手のアニメーションを使い、身体は幅・高さ0.625ブロックです。
