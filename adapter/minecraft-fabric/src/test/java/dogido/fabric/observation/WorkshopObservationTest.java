package dogido.fabric.observation;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.observation.mobs.BossTracker;
import dogido.fabric.observation.mobs.CombatObserver;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.mobs.ThreatObserver;
import dogido.fabric.observation.sound.SoundObserver;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class WorkshopObservationTest {
    // WSの周期通知には現在の安全情報が届き、未観測の地形・匂い・所持品は出現／消失にしない。
    // 実際の送信JSONを検査し、不要な走査が呼ばれたら失敗。Minecraft内のフレーム時間は別確認。
    @Test void workshopHeartbeatOmitsUnmeasuredSectionsAndDoesNotScanTheWorld() {
        var safety = new JsonObject();
        safety.addProperty("air_supply", 51);
        safety.addProperty("game_paused", false);
        var player = new JsonObject();
        player.addProperty("health", 16);
        var frame = new WorldObservationFrame(true, () -> safety,
            () -> {throw new AssertionError("block scan");},
            () -> new WorldObservationFrame.Materials(player,new JsonObject(),null,new JsonArray(),null),
            bounds -> {throw new AssertionError("world entity scan");});
        var mobs = new MobClassifier();
        var threats = new ThreatObserver(new ThreatObserver.Settings(32,24,12,4,1,2,20),
            mobs,new BossTracker(),()->{},()->3);
        var sound = new SoundObserver(16,mobs,org.slf4j.LoggerFactory.getLogger("test"),()->3);
        var combat = new CombatObserver(threats,sound,null,32,24,()->3);
        var capture = new EventObservationCapture(null,null,null,null,combat,()->3,()->3,()->null,()->null);
        var event = capture.buildStatusSnapshot(null,null,frame,List.of(),List.of(),List.of(),List.of());
        assertTrue(event.getAsJsonObject("meta").get("workshop_observation").getAsBoolean());
        assertEquals(player,event.getAsJsonObject("player"));
        assertEquals(safety,event.getAsJsonObject("world"));
        assertTrue(event.getAsJsonObject("combat").has("player_engaged"));
        for (String key : List.of("inventory","look_target","passive_mobs","ambient_sounds","nearby_composition","smell_observation","recent_block_breaks")) {
            assertFalse(event.has(key),key);
        }
    }
}
