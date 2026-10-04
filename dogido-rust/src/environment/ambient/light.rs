//! 照明器具の所持数が増えた際に、無言・備えへの相槌・暗所回復への安堵から一件を選ぶための処理。
//! Ambientが完全なinventory snapshotの前後差を渡し、Danger由来の明るさ／暗所回復状態から許可actionと根拠を作る。
//! モデルの選択が戻ったら現在観測で再検証し、発話する場合はカタログ代替文付きのSpeechを配送側へ返す。
//! 明るさの測定・暗所警告の状態変更・モデル実行・音声再生は各担当に残し、入手方法や設置完了は推定しない。
use super::*;
use serde::Serialize;
/// Dangerが観測と閾値から算出した結果を受け取る。ここでは暗さや回復を所持数から決め直さない。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LightContext {
    pub surroundings_reasonably_lit: bool,
    pub severe_darkness: bool,
    pub nearby_light_present: bool,
    pub dark_push_context_before: bool,
    pub dark_push_recovered: bool,
}
/// 一回の選択依頼。公開するdetailsは許可actionと段階化した所持量、fallbackは無言の選択。
/// 正確な前後個数・作成時刻・当時の暗所状態はRust内に保持し、返ってきた選択の現在性を照合する。
#[derive(Clone, Debug, Serialize)]
pub struct LightPlanRequest {
    pub request_id: u64,
    pub details: Value,
    pub fallback_payload: Value,
    #[serde(skip)]
    created_at: u64,
    #[serde(skip)]
    previous: i64,
    #[serde(skip)]
    current: i64,
    #[serde(skip)]
    context: LightContext,
}
/// inventory内の松明・魂の松明・ランタン・魂のランタンを合算する。近くの設置済み光源とは別の所持数。
pub(super) fn count(e: &GameEvent) -> i64 {
    ["torch", "soul_torch", "lantern", "soul_lantern"]
        .iter()
        .map(|key| e.inventory.get(*key).copied().unwrap_or(0))
        .sum()
}
/// モデルへ渡す所持量を、0以下／1〜7／8〜31／32以上の4段階にする。
fn band(n: i64) -> &'static str {
    if n <= 0 {
        "empty"
    } else if n < 8 {
        "low"
    } else if n < 32 {
        "enough"
    } else {
        "abundant"
    }
}
/// 所持増分と暗所状態から選択可能なactionだけを返す。常にstay_silentを含み、無言だけならモデル依頼を省ける。
/// 増加なし・直近コメントありは無言。実際の暗所回復があれば安堵を許可し、それ以外で継続中の危険な暗さは抑制する。
pub(super) fn allowed(
    previous: i64,
    current: i64,
    c: &LightContext,
    recent: bool,
) -> Vec<&'static str> {
    let mut out = vec!["stay_silent"];
    if current <= previous || recent {
        return out;
    }
    if c.dark_push_recovered && c.dark_push_context_before {
        out.push("relief_after_darkness");
        return out;
    }
    // 32個以上で周囲も明るい場合は備えの実況を抑える。暗所回復への安堵は上の分岐で別に扱っている。
    if current >= 32 && c.surroundings_reasonably_lit || c.severe_darkness && !c.dark_push_recovered
    {
        return out;
    }
    // 初めて照明を確保した時か、32個未満で十分に明るくない時だけ備えへの相槌を候補にする。
    if previous <= 0 && current > 0 || current < 32 && !c.surroundings_reasonably_lit {
        out.push("acknowledge_supply_gain");
    }
    out
}
/// 依頼時に成立した観測をbasis_id付きで渡す。所持増分という事実と、暗所からの回復という事実を分ける。
pub(super) fn facts(previous: i64, current: i64, c: &LightContext, recent: bool) -> Value {
    let mut rows = vec![
        ("light_source_gain_observed", "true".to_owned()),
        ("supply_before", band(previous).into()),
        ("supply_after", band(current).into()),
        (
            "surroundings_light",
            if c.surroundings_reasonably_lit {
                "reasonably_lit"
            } else {
                "not_reasonably_lit"
            }
            .into(),
        ),
        ("dangerous_darkness", c.severe_darkness.to_string()),
        ("nearby_light_present", c.nearby_light_present.to_string()),
        (
            "dark_push_context_before",
            c.dark_push_context_before.to_string(),
        ),
        ("dark_push_recovered", c.dark_push_recovered.to_string()),
        ("recent_light_comment", recent.to_string()),
    ];
    if previous <= 0 && current > 0 {
        rows.push(("first_light_supply", "true".into()));
    }
    Value::Array(
        rows.into_iter()
            .map(|(id, value)| json!({"basis_id":id,"value":value}))
            .collect(),
    )
}
impl Ambient {
    /// 未処理の所持増分を一度消費し、発話の余地があれば選択依頼をpendingと配送待ちの両方へ登録する。
    /// 戻り値trueは依頼の準備完了であり、発話決定ではない。無言しか許可されなければfalseを返す。
    pub(super) fn light_request(
        &mut self,
        _e: &GameEvent,
        now: u64,
        focus: &AmbientFocus,
        s: &Settings,
    ) -> bool {
        let Some((previous, current)) = self.gain.take() else {
            return false;
        };
        let recent = !elapsed(
            now,
            self.last_light_comment,
            s.ms("darkness_llm_comment_cooldown_ms"),
        );
        // 前回コメントからの間隔は設定値を使う（既定5分）。本数増加だけでは暗所警告を解除しない。
        let actions = allowed(previous, current, &focus.light, recent);
        if actions.len() == 1 {
            return false;
        }
        self.light_serial = self.light_serial.saturating_add(1);
        let request = LightPlanRequest {
            request_id: self.light_serial,
            details: json!({"allowed_actions":actions,"facts":facts(previous,current,&focus.light,recent)}),
            fallback_payload: json!({"action":"stay_silent","basis_ids":["light_source_gain_observed"],"confidence":0.0}),
            created_at: now,
            previous,
            current,
            context: focus.light.clone(),
        };
        self.light_pending = Some(request.clone());
        self.light_ready = Some(request);
        true
    }
    /// 指定IDの応答を一度消費し、現在の所持数・会話優先・敵・暗所状態と、action／根拠／信頼度を照合する。
    /// 条件が揃った時だけ生成leaf付きSpeechを返し、コメント時刻を進める。無言・不正・古い応答はNone。
    pub fn resolve_light_plan(
        &mut self,
        id: u64,
        payload: &Value,
        e: &GameEvent,
        now: u64,
        focus: &AmbientFocus,
        s: &Settings,
    ) -> Option<Speech> {
        if self
            .light_pending
            .as_ref()
            .is_none_or(|r| r.request_id != id)
        {
            return None;
        }
        let request = self.light_pending.take()?;
        if self
            .light_ready
            .as_ref()
            .is_some_and(|r| r.request_id == id)
        {
            self.light_ready = None;
        }
        // 依頼後の会話開始・敵出現・在庫変化を再確認し、古い備えのコメントを現在の会話へ差し込まない。
        if focus.foreground
            || focus.player_priority
            || focus.boss_presence
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
            || count(e) != request.current
            || focus
                .last_player_input_at
                .or(self.last_player_input)
                .is_some_and(|at| at > request.created_at)
        {
            return None;
        }
        let status = payload["__dogido_status"].as_str().unwrap_or("accepted");
        if status != "accepted" {
            return None;
        }
        let action = payload["action"].as_str()?;
        if !request.details["allowed_actions"]
            .as_array()?
            .iter()
            .any(|v| v.as_str() == Some(action))
        {
            return None;
        }
        let recent = !elapsed(
            now,
            self.last_light_comment,
            s.ms("darkness_llm_comment_cooldown_ms"),
        );
        // 安堵の根拠は依頼時に実測した回復を保ちつつ、再び危険な暗さなら失効させる。
        // それ以外の明るさとクールダウンは現在値で許可actionを選び直す。
        let mut current_context = focus.light.clone();
        current_context.dark_push_context_before = request.context.dark_push_context_before;
        current_context.dark_push_recovered =
            request.context.dark_push_recovered && !focus.light.severe_darkness;
        if !allowed(request.previous, request.current, &current_context, recent).contains(&action) {
            return None;
        }
        // 根拠は依頼時に渡した重複しない1〜3件へ限定し、安堵／備えに対応する根拠も要求する。
        let basis = payload["basis_ids"].as_array()?;
        if !(1..=3).contains(&basis.len()) {
            return None;
        }
        let mut ids = vec![];
        for v in basis {
            let id = v.as_str()?.trim();
            if id.is_empty()
                || ids.contains(&id)
                || !request.details["facts"]
                    .as_array()?
                    .iter()
                    .any(|f| f["basis_id"].as_str() == Some(id))
            {
                return None;
            }
            ids.push(id);
        }
        if action == "relief_after_darkness" && !ids.contains(&"dark_push_recovered") {
            return None;
        }
        if action == "acknowledge_supply_gain"
            && !ids.iter().any(|id| {
                ["first_light_supply", "supply_before", "surroundings_light"].contains(id)
            })
        {
            return None;
        }
        // 発話には有限の0〜1の信頼度と0.78以上が必要。stay_silentは妥当でもSpeechを作らない。
        let confidence = payload["confidence"].as_f64()?;
        if !confidence.is_finite()
            || !(0.0..=1.0).contains(&confidence)
            || action != "stay_silent" && confidence < 0.78
            || action == "stay_silent"
        {
            return None;
        }
        let name = crate::combat::core::call_name(e, s);
        let prefix = if name == "プレイヤー" {
            String::new()
        } else {
            format!("{name}、")
        };
        let key = if action == "relief_after_darkness" {
            "light_source_relief"
        } else {
            "light_source_gain"
        };
        // 選ばれたactionに対応するカタログ文を生成失敗時の代替として用意し、言い回しはleafへ渡す。
        // 選択自体のfallbackはstay_silentなので、planner失敗だけを理由にこの固定文を発話しない。
        let text = catalog::general("darkness", key).replace("{prefix}", &prefix);
        let mut details = common_details(e, s);
        details["comment_action"] = action.into();
        details["surroundings_light"] = if focus.light.surroundings_reasonably_lit {
            "reasonably_lit"
        } else {
            "not_reasonably_lit"
        }
        .into();
        details["__ambient_guard"] = json!({"light_source_count":request.current});
        self.last_light_comment = Some(now);
        Some(leaf("light_source_gain", text, details, 0.48))
    }
}
