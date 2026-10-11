package dogido.fabric.protocol;

import com.google.gson.*;
import dogido.fabric.observation.WorldObservationFrame;
import dogido.fabric.observation.mobs.BossTracker;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.mobs.ThreatObservation;
import dogido.fabric.observation.mobs.ThreatObserver;
import dogido.fabric.observation.smell.*;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

import static dogido.fabric.protocol.EventWireFixture.*;
import static org.junit.jupiter.api.Assertions.*;

class EventJsonBuilderTest {
    /** 実フィルタと実serializerの出力をRust模擬通信へ渡す。world走査と集計の実測は実機確認に残す。 */
    @Test void retainedEnemyWirePayloadIsExportedForRustRuntimeCheck() throws Exception {
        AtomicLong tick = new AtomicLong(100);
        ThreatObserver observer = new ThreatObserver(new ThreatObserver.Settings(16, 16, 3, 7, 6, 16, 100),
            new MobClassifier(), new BossTracker(), () -> {}, tick::get);
        assertTrue(observer.filterVisibleThreats(List.of(rangeThreat(16))).isEmpty());
        tick.set(106);
        var inner = observer.filterVisibleThreats(List.of(rangeThreat(16)));
        observer.updateCombatTracking(inner, List.of());
        assertEquals(1, inner.size());
        JsonObject artifact = new JsonObject();
        artifact.add("inner", rangeEvent("status_snapshot", 1, inner, observer.combatActive()));
        tick.set(107);
        var outer = observer.filterVisibleThreats(List.of(rangeThreat(20)));
        observer.updateCombatTracking(outer, List.of());
        assertEquals(1, outer.size());
        JsonObject outerEvent = rangeEvent("status_snapshot", 2, outer, observer.combatActive());
        assertEquals(ID.toString(), outerEvent.getAsJsonArray("visual_threats").get(0).getAsJsonObject().get("entity_id").getAsString());
        assertEquals(20.0, outerEvent.getAsJsonArray("visual_threats").get(0).getAsJsonObject().get("distance").getAsDouble());
        artifact.add("outer", outerEvent);
        tick.set(108);
        assertTrue(observer.filterVisibleThreats(List.of(rangeThreat(21.01))).isEmpty());
        observer.updateCombatTracking(List.of(), List.of());
        tick.set(206);
        assertFalse(observer.shouldSendCombatEnded(List.of(), List.of(), false));
        tick.set(207);
        assertTrue(observer.shouldSendCombatEnded(List.of(), List.of(), false));
        observer.endCombat();
        artifact.add("ended", rangeEvent("combat_ended", 3, List.of(), observer.combatActive()));
        Path output = Path.of("build", "test-results", "hostile-margin-wire.json");
        Files.createDirectories(output.getParent());
        Files.writeString(output, new GsonBuilder().setPrettyPrinting().create().toJson(artifact) + "\n", StandardCharsets.UTF_8);
    }

    private static ThreatObservation rangeThreat(double distance) {
        return new ThreatObservation(ID, "zombie", distance, "front", "south", "same", false,
            false, true, false, false, 0, 64, distance, 20, false, false, IDENTITY, ENV);
    }

    private static JsonObject rangeEvent(String name, long sequence, List<ThreatObservation> threats, boolean active) {
        EventJsonBuilder builder = new EventJsonBuilder(name, "system", "normal", "high", TIME,
            new SmellObserver.Result(new SmellPolicy.Observation("none", List.of(), 0, false, null, null), -1));
        builder.sequence(sequence);
        builder.player(JsonParser.parseString("{\"name\":\"試験\",\"health\":20}").getAsJsonObject());
        builder.world(JsonParser.parseString("{\"sky_visible\":true,\"ceiling_height\":20,\"local_light\":15}").getAsJsonObject());
        builder.visualThreats(threats);
        builder.auditoryThreats(List.of());
        JsonObject combat = new JsonObject();
        // 実ClientWorldを必要とするCombatObserverの集計はここではfixture。
        // 内側16の集計が0でも、実フィルタが保持した外側のvisual個体が送信されることを確認する。
        combat.addProperty("hostile_scan_distance", 16);
        combat.addProperty("hostiles_within_scan_ground", threats.stream().filter(t -> t.distance() <= 16).count());
        combat.addProperty("combat_active_hint", active);
        combat.add("hostile_outcomes", new JsonArray());
        builder.combat(combat);
        return builder.finish();
    }

    @Test void eightEventsMatchPreSplitPayloadsWithAndWithoutOptionalObservations() throws Exception {
        try (var stream = getClass().getResourceAsStream("event-wire-baseline.json")) {
            assertNotNull(stream);
            JsonArray cases = JsonParser.parseReader(new InputStreamReader(stream, StandardCharsets.UTF_8)).getAsJsonArray();
            assertEquals(16, cases.size());
            for (JsonElement element : cases) {
                JsonObject row = element.getAsJsonObject();
                String event = row.get("event").getAsString();
                boolean detail = row.get("detail").getAsBoolean();
                int index = 0;
                while (!EVENTS[index][0].equals(event)) index++;
                String[] descriptor = EVENTS[index];
                WorldObservationFrame frame = new WorldObservationFrame(() -> object("world"), () -> blocks(detail));
                JsonObject nearby = frame.blocks();
                EventJsonBuilder builder = new EventJsonBuilder(descriptor[0], descriptor[1], descriptor[2], descriptor[3],
                    TIME, new SmellObserver.Result(SMELL, 3600));
                builder.sequence(7);
                builder.player(object("player"));
                builder.world(frame.copy());
                boolean emptyCombat = index == 3 || index == 7;
                builder.visualThreats(index == 2 || emptyCombat ? List.of() : THREATS);
                builder.auditoryThreats(emptyCombat ? List.of() : AUDIO);
                if (index == 0) builder.ambientSounds(SOUNDS);
                builder.passiveMobs(MOBS);
                builder.inventory(object("inventory"));
                builder.nearbyMaterials(nearby, detail ? mobComposition() : null);
                builder.droppedItems(array("dropped"));
                builder.recentBlockBreaks(array("breaks"));
                builder.lookTarget(detail ? object("look") : null);
                JsonObject combat = object("combat");
                combat.addProperty("visual_count", emptyCombat ? 0 : THREATS.size());
                combat.addProperty("audio_count", emptyCombat ? 0 : AUDIO.size());
                if (index == 5 || index == 6) builder.combatOutcomes(combat, index == 5 ? DETONATIONS : OUTCOMES);
                else builder.combat(combat);
                builder.meta(EventJsonBuilder.buildMeta(index == 4 ? "fixture death" : null, "fixture input"));
                assertEquals(row.get("expected"), builder.finish(), event + " detail=" + detail);
                assertFalse(frame.copy().has("visible_villager_count"), "per-event augmentation must not alter the shared frame");
            }
        }
    }

    @Test void absentOrBlankMetadataIsOmitted() {
        assertFalse(EventJsonBuilder.buildMeta(null, null).has("death_cause"));
        assertFalse(EventJsonBuilder.buildMeta(" ", " ").has("user_text"));
        assertEquals("hello", EventJsonBuilder.buildMeta(null, "hello").get("user_text").getAsString());
    }

    @Test void noneAndSuppressedSmellsKeepAnExplicitEmptyComponentArray() {
        for (String status : List.of("none", "suppressed")) {
            SmellPolicy.Observation observation = new SmellPolicy.Observation(status, List.of(), -3, false,
                status.equals("suppressed") ? "rain" : null, null);
            JsonObject smell = new EventJsonBuilder("status_snapshot", "system", "background", "high", TIME,
                new SmellObserver.Result(observation, -1)).finish().getAsJsonObject("smell_observation");
            assertEquals(status, smell.get("status").getAsString());
            assertEquals(0, smell.getAsJsonArray("components").size());
            assertEquals("smell_policy_v2", smell.get("basis").getAsString());
            assertFalse(smell.has("rain_after_notice"));
            assertFalse(smell.has("direction"));
            assertEquals(status.equals("suppressed"), smell.has("suppression_reason"));
        }
    }
}
