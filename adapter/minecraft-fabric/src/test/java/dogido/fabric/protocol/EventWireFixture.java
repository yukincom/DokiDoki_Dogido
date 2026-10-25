package dogido.fabric.protocol;

import com.google.gson.*;
import dogido.fabric.observation.*;
import dogido.fabric.observation.mobs.*;
import dogido.fabric.observation.smell.*;
import dogido.fabric.observation.sound.*;
import java.util.*;

/** Fixed observations for comparison with the preserved pre-split event builders. */
public final class EventWireFixture {
    public static final String TIME = "2026-10-09T00:00:00Z";
    public static final UUID ID = UUID.fromString("00000000-0000-0000-0000-000000000001");
    public static final MobEnvironmentObservation ENV = new MobEnvironmentObservation(false, false, false, true, false, false);
    public static final MobIdentityObservation IDENTITY = new MobIdentityObservation(ID.toString(), "テスト", false);
    public static final SmellPolicy.Observation SMELL = new SmellPolicy.Observation("present",
        List.of(new SmellPolicy.Component("soup", "food", "pleasant", "hotbar", 3)), 0, true, null,
        new SmellPolicy.Component("rain_after", "rain_after", "pleasant", "block", 1));
    public static final List<ThreatObservation> THREATS = List.of(new ThreatObservation(ID, "creeper", 4.126,
        "front", "south", "same", true, false, true, false, false, 0, 64, 4, 20, true, true, IDENTITY, ENV));
    public static final List<AudioThreatObservation> AUDIO = List.of(new AudioThreatObservation("zombie",
        ID.toString(), "entity.zombie.ambient", "left", "below", "close", "medium", true, 42, 125, IDENTITY));
    public static final List<AudioThreatObservation> SOUNDS = List.of(new AudioThreatObservation("rain",
        "played:rain", "weather.rain", "front", "same", "touching", "high", false, 43, 50, null));
    public static final List<AmbientMobObservation> MOBS = List.of(
        new AmbientMobObservation(ID, "villager", 3, "front", "same", "passive", null, false, "farmer", "plains", IDENTITY, ENV),
        new AmbientMobObservation(ID, "cow", 4, "left", "same", "passive", null, null, null, null, IDENTITY, ENV),
        new AmbientMobObservation(ID, "pig", 5, "right", "same", "passive", null, null, null, null, IDENTITY, ENV),
        new AmbientMobObservation(ID, "sheep", 6, "front", "above", "passive", null, null, null, null, IDENTITY, ENV),
        new AmbientMobObservation(ID, "wolf", 7, "back", "same", "neutral", "retaliates", null, null, null,
            new MobIdentityObservation(ID.toString(), "シロ", true), ENV));
    public static final List<HostileOutcomeObservation> OUTCOMES = List.of(
        new HostileOutcomeObservation(ID, "zombie", "player_kill", "server_death_event", 42));
    public static final List<HostileOutcomeObservation> DETONATIONS = List.of(
        new HostileOutcomeObservation(ID, "creeper", "creeper_detonation", "explosion_packet", 42));
    public static final String[][] EVENTS = {
        {"status_snapshot", "system", "background", "high"},
        {"threat_approaching", "visual", "urgent", "high"},
        {"hostile_audio_detected", "auditory", "normal", "medium"},
        {"ambient_mob_detected", "visual", "background", "high"},
        {"player_died", "system", "urgent", "high"},
        {"creeper_detonated", "system", "urgent", "high"},
        {"hostile_defeated", "system", "urgent", "high"},
        {"combat_ended", "system", "normal", "high"}
    };
    public static JsonObject object(String name) {
        JsonObject object = new JsonObject(); object.addProperty("fixture", name); return object;
    }
    public static JsonArray array(String name) {
        JsonArray array = new JsonArray(); array.add(object(name)); return array;
    }
    public static JsonObject mobComposition() {
        return JsonParser.parseString("{\"entries\":[{\"id\":\"villager\",\"quantity\":1,\"professions\":[]}],\"truncated\":false}").getAsJsonObject();
    }
    public static JsonObject blocks(boolean includeTypes) {
        JsonObject blocks = new JsonObject(); blocks.add("resources", array("resources"));
        if (includeTypes) blocks.add("types", JsonParser.parseString("{\"ids\":[\"minecraft:stone\"],\"truncated\":false}"));
        return blocks;
    }
}
