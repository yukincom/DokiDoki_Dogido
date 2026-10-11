package dogido.fabric.protocol;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.DogidoBuildInfo;
import dogido.fabric.observation.mobs.AmbientMobObservation;
import dogido.fabric.observation.mobs.HostileOutcomeObservation;
import dogido.fabric.observation.mobs.PassiveMobSelection;
import dogido.fabric.observation.mobs.ThreatObservation;
import dogido.fabric.observation.smell.SmellObserver;
import dogido.fabric.observation.smell.SmellPolicy;
import dogido.fabric.observation.sound.AudioThreatObservation;
import java.util.List;

import static dogido.fabric.observation.ObservationNumbers.round;
import static dogido.fabric.protocol.EventObservationJson.buildHostileOutcomes;

public final class EventJsonBuilder {
    private final JsonObject root;

    public EventJsonBuilder(String eventName, String sourceKind, String priorityHint, String certainty,
            String observedAt, SmellObserver.Result smell) {
        this.root = baseEnvelope(eventName, sourceKind, priorityHint, certainty, observedAt, smell);
    }

    public void sequence(long sequence) { root.addProperty("sequence", sequence); }
    public void player(JsonObject player) { root.add("player", player); }
    public void world(JsonObject world) { root.add("world", world); }
    public void visualThreats(List<ThreatObservation> threats) { root.add("visual_threats", buildVisualThreats(threats)); }
    public void auditoryThreats(List<AudioThreatObservation> threats) { root.add("auditory_threats", buildAuditoryThreats(threats)); }
    public void ambientSounds(List<AudioThreatObservation> sounds) { root.add("ambient_sounds", buildAmbientSounds(sounds)); }
    public void passiveMobs(List<AmbientMobObservation> mobs) { attachPassiveMobs(root, mobs); }
    public void inventory(JsonObject inventory) { root.add("inventory", inventory); }
    public void droppedItems(JsonArray droppedItems) { root.add("dropped_items", droppedItems); }
    public void recentBlockBreaks(JsonArray breaks) { root.add("recent_block_breaks", breaks); }
    public void lookTarget(JsonObject target) { if (target != null) root.add("look_target", target); }
    public void combat(JsonObject combat) { root.add("combat", combat); }
    public void combatOutcomes(JsonObject combat, java.util.Collection<HostileOutcomeObservation> outcomes) {
        combat.add("hostile_outcomes", EventObservationJson.buildHostileOutcomes(outcomes));
        root.add("combat", combat);
    }
    public void meta(JsonObject meta) { root.add("meta", meta); }
    public JsonObject finish() { return root; }

    public void nearbyMaterials(JsonObject nearbyResources, JsonObject nearbyMobComposition) {
        root.add("nearby_resources", nearbyResources.get("resources"));
        // 拡張した気づき表は全snapshotの周期だけ採る。緊急の部分通知へ古い表を転記しない。
        if (nearbyResources.has("types")) {
            JsonObject composition = new JsonObject();
            composition.add("mobs", nearbyMobComposition.get("entries").deepCopy());
            composition.add("mobs_truncated", nearbyMobComposition.get("truncated").deepCopy());
            composition.add("blocks", nearbyResources.getAsJsonObject("types").get("ids"));
            composition.add("blocks_truncated", nearbyResources.getAsJsonObject("types").get("truncated"));
            root.add("nearby_composition", composition);
        }
    }

    private static JsonObject baseEnvelope(String eventName, String sourceKind, String priorityHint, String certainty,
        String observedAt, SmellObserver.Result smell) {
        JsonObject root = new JsonObject();
        root.addProperty("schema_version", DogidoBuildInfo.SCHEMA_VERSION);
        root.addProperty("game", DogidoBuildInfo.GAME);
        root.addProperty("adapter", DogidoBuildInfo.ADAPTER_NAME);
        root.addProperty("observed_at", observedAt);

        JsonObject event = new JsonObject();
        event.addProperty("name", eventName);
        event.addProperty("source_kind", sourceKind);
        event.addProperty("priority_hint", priorityHint);
        event.addProperty("certainty", certainty);
        root.add("event", event);
        root.add("smell_observation", buildSmellObservation(smell));
        return root;
    }

    public static JsonObject buildMeta(String deathCause, String userText) {
        JsonObject json = new JsonObject();
        json.addProperty("adapter_build", DogidoBuildInfo.ADAPTER_BUILD);
        json.addProperty("profile_name", DogidoBuildInfo.PROFILE_NAME);
        json.addProperty("debug", false);
        if (deathCause != null && !deathCause.isBlank()) {
            json.addProperty("death_cause", deathCause);
        }
        if (userText != null && !userText.isBlank()) {
            json.addProperty("user_text", userText);
        }
        return json;
    }

    // v2の送信境界。内部recordを丸ごとJSON化せず、公開する項目だけを列挙する。
    // none/suppressedでもcomponents=[]とbasisを送り、「古い形式だが補完して受理」は行わない。
    private static JsonObject buildSmellObservation(SmellObserver.Result result) {
        SmellPolicy.Observation observation = result.observation();
        JsonObject json = new JsonObject();
        json.addProperty("status", observation.status());
        JsonArray components = new JsonArray();
        for (SmellPolicy.Component component : observation.components()) {
            components.add(buildSmellComponent(component));
        }
        if (observation.rainAfter() != null) {
            // 同じ雨上がりを毎snapshotで繰り返し話さないための窓ID。
            // 個体IDや座標ではない。生成文へは渡さず、出力完了の照合にだけ使う。
            JsonObject notice = new JsonObject();
            notice.addProperty("window_id", result.rainAfterWindowId());
            notice.add("component", buildSmellComponent(observation.rainAfter()));
            json.add("rain_after_notice", notice);
        }
        json.add("components", components);
        json.addProperty("temperature_modifier", observation.temperatureModifier());
        json.addProperty("rain_after_active", observation.rainAfterActive());
        addOptionalProperty(json, "suppression_reason", observation.suppressionReason());
        json.addProperty("basis", "smell_policy_v2");
        return json;
    }

    // 通常臭と一度の雨上がりで成分の外形を共有する。方位はどの成分にも持たせない。
    private static JsonObject buildSmellComponent(SmellPolicy.Component component) {
        JsonObject row = new JsonObject();
        row.addProperty("smell_id", component.smellId());
        row.addProperty("category", component.category());
        row.addProperty("valence", component.valence());
        row.addProperty("source_kind", component.sourceKind());
        row.addProperty("effective_strength", component.effectiveStrength());
        return row;
    }

    private static void addOptionalProperty(JsonObject json, String name, String value) {
        if (value != null) {
            json.addProperty(name, value);
        }
    }

    private static JsonArray buildVisualThreats(List<ThreatObservation> threats) {
        JsonArray array = new JsonArray();
        for (ThreatObservation threat : threats) {
            JsonObject entry = new JsonObject();
            entry.addProperty("type", threat.type());
            entry.addProperty("entity_id", threat.uuid().toString());
            entry.add("identity", threat.identity().toJson());
            entry.addProperty("distance", round(threat.distance()));

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", threat.horizontalDirection());
            direction.addProperty("cardinal", threat.cardinalDirection());
            direction.addProperty("vertical", threat.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("approaching", threat.approaching());
            entry.addProperty("fuse_active", threat.fuseActive());
            entry.addProperty("on_fire", threat.onFire());
            entry.addProperty("in_water", threat.inWater());
            // A held warning target may be occluded now. An explicit empty
            // environment withholds fresh measurements without removing the threat.
            entry.add("environment", threat.lineOfSight() ? threat.environment().toJson() : new JsonObject());
            entry.addProperty("certainty", "high");
            array.add(entry);
        }
        return array;
    }

    private static JsonArray buildAuditoryThreats(List<AudioThreatObservation> audioThreats) {
        JsonArray array = new JsonArray();
        for (AudioThreatObservation threat : audioThreats) {
            JsonObject entry = new JsonObject();
            entry.addProperty("label", threat.label());
            entry.addProperty("source_id", threat.sourceId());
            entry.addProperty("sound_event", threat.soundEvent());
            entry.addProperty("heard_ago_ms", threat.heardAgoMs());

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", threat.horizontalDirection());
            direction.addProperty("vertical", threat.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("distance_band", threat.distanceBand());
            entry.addProperty("certainty", threat.certainty());
            entry.addProperty("spoken_name_allowed", threat.spokenNameAllowed());
            array.add(entry);
        }
        return array;
    }

    private static JsonArray buildAmbientSounds(List<AudioThreatObservation> ambientSounds) {
        JsonArray array = new JsonArray();
        int limit = Math.min(ambientSounds.size(), 12);
        for (int index = 0; index < limit; index += 1) {
            AudioThreatObservation sound = ambientSounds.get(index);
            JsonObject entry = new JsonObject();
            entry.addProperty("type", sound.label());
            if (sound.identity() != null) entry.add("identity", sound.identity().toJson());
            entry.addProperty("source_id", sound.sourceId());
            entry.addProperty("sound_event", sound.soundEvent());
            entry.addProperty("heard_ago_ms", sound.heardAgoMs());

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", sound.horizontalDirection());
            direction.addProperty("vertical", sound.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("distance_band", sound.distanceBand());
            entry.addProperty("certainty", sound.certainty());
            array.add(entry);
        }
        return array;
    }

    private static JsonArray buildPassiveMobs(List<AmbientMobObservation> ambientMobs) {
        JsonArray array = new JsonArray();
        for (AmbientMobObservation mob : ambientMobs) {
            JsonObject entry = new JsonObject();
            entry.addProperty("type", mob.type());
            entry.add("identity", mob.identity().toJson());
            entry.add("environment", mob.environment().toJson());
            entry.addProperty("distance", round(mob.distance()));

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", mob.horizontalDirection());
            direction.addProperty("vertical", mob.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("certainty", "high");
            if (mob.temperament() != null && !mob.temperament().isBlank()) {
                entry.addProperty("temperament", mob.temperament());
            }
            if (mob.cautionReason() != null && !mob.cautionReason().isBlank()) {
                entry.addProperty("caution_reason", mob.cautionReason());
            }
            if (mob.isBaby() != null) {
                entry.addProperty("is_baby", mob.isBaby());
            }
            if (mob.profession() != null && !mob.profession().isBlank()) {
                entry.addProperty("profession", mob.profession());
            }
            if (mob.villagerType() != null && !mob.villagerType().isBlank()) {
                entry.addProperty("villager_type", mob.villagerType());
            }
            array.add(entry);
        }
        return array;
    }

    private static void attachPassiveMobs(JsonObject root, List<AmbientMobObservation> ambientMobs) {
        root.add("passive_mobs", buildPassiveMobs(PassiveMobSelection.regular(ambientMobs)));
        root.add("companion_candidates", buildPassiveMobs(PassiveMobSelection.companions(
            ambientMobs, mob -> mob.identity().tamed() || mob.identity().customName() != null)));
        // Count the full scan before the four routine observation slots are selected.
        long villagers = ambientMobs.stream().filter(mob -> "villager".equals(mob.type())).count();
        root.getAsJsonObject("world").addProperty("visible_villager_count", villagers);
    }
}
