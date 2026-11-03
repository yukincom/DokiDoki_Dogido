package dogido.fabric.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** 固定した身体感覚JSONと実際の視線予算で、部分観測の保持・上限・標識を確かめる。実機での視認・聴取は別確認。 */
class BodySenseCapacityTest {
    private static JsonObject sample() {
        return JsonParser.parseString("""
            {
              "status":"observed","world":{"weather":"rain","biome":"plains"},
              "visual_threats":[{"type":"zombie"}],"passive_mobs":[{"type":"cow"}],
              "smell_observation":{"status":"none"},"hearing_truncated":false,
              "auditory_threats":[],"ambient_sounds":[{"type":"rain","heard_ago_ms":150}],
              "nearby_composition":{"mobs":[],"blocks":["minecraft:stone"],"mobs_truncated":false,"blocks_truncated":false}
            }
            """).getAsJsonObject();
    }

    // 見込み: 通常サイズの観測は標識の追加も順序変更も不要。合格: 元のJSONと完全に同じ。固定入力の送信整形を検査する。
    @Test void ordinaryFrameKeepsEveryField() {
        var sample = sample();
        var expected = sample.deepCopy();
        BodySenseCapacity.fit(sample);
        assertEquals(expected,sample);
    }

    // 見込み: 大きなブロック種類があっても、視認した両群の個体・天候・実音を残せる。
    // 合格: 実バイト数が上限内、石まで残り、種類表だけが不完全。実送信整形を固定入力で検査する。
    @Test void overlargeKindsKeepMobsClimateHearingAndSmallerKinds() {
        var sample = sample();
        var expected = sample.deepCopy();
        var kinds = sample.getAsJsonObject("nearby_composition");
        kinds.getAsJsonArray("blocks").add("雪".repeat(23_000));
        kinds.getAsJsonArray("blocks").add("minecraft:snow");
        BodySenseCapacity.fit(sample);
        assertTrue(sample.toString().getBytes(StandardCharsets.UTF_8).length <= BodySenseCapacity.MAX_BYTES);
        for (String field : java.util.List.of("status","world","passive_mobs","visual_threats","ambient_sounds","smell_observation"))
            assertEquals(expected.get(field),sample.get(field));
        assertEquals(JsonParser.parseString("[\"minecraft:stone\",\"minecraft:snow\"]"),kinds.get("blocks"));
        assertTrue(kinds.get("blocks_truncated").getAsBoolean());
        assertFalse(kinds.get("mobs_truncated").getAsBoolean());
        assertEquals(JsonParser.parseString("[\"nearby_composition\"]"),sample.get("incomplete"));
    }

    // 見込み: 実音の周辺根拠だけが大きければ、雨音と元の年齢までは送れる。
    // 合格: 音本体と視認個体を保ち、削った根拠は聴覚だけに不完全と明示する。JSON圧縮の検査。
    @Test void oversizedSoundContextKeepsTheSoundAndItsOriginalAge() {
        var sample = sample();
        var expectedSound = sample.getAsJsonArray("ambient_sounds").get(0).deepCopy();
        sample.getAsJsonArray("ambient_sounds").get(0).getAsJsonObject().addProperty("source_context","根拠".repeat(15_000));
        BodySenseCapacity.fit(sample);
        assertEquals(expectedSound,sample.getAsJsonArray("ambient_sounds").get(0));
        assertEquals(1,sample.getAsJsonArray("passive_mobs").size());
        assertTrue(sample.get("hearing_truncated").getAsBoolean());
        assertEquals(JsonParser.parseString("[\"hearing\"]"),sample.get("incomplete"));
    }

    // 見込み: 敵の視線が集中しても、同じframeの非敵対個体用に64個体×3本の余地が残る。
    // 合格: 敵の未測定を記録し、以降192本だけ取得可、合計4096本で停止する。ray配分であり実視認ではない。
    @Test void crowdedThreatsLeaveRaysForOtherVisibleMobsWithinTheSameTotal() {
        var vision = new BodyOriginVision(10,net.minecraft.util.math.Vec3d.ZERO,0,0);
        var threats = vision.forThreats();
        for (int i=0; i<BodyOriginVision.MAX_RAYS-192; i++) assertTrue(threats.takeRay());
        assertFalse(threats.takeRay());
        assertEquals(1,vision.missedRays());
        for (int i=0; i<192; i++) assertTrue(vision.takeRay());
        assertFalse(vision.takeRay());
        assertEquals(2,threats.missedRays());
    }
}
