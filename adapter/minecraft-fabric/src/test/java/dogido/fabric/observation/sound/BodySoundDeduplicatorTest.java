package dogido.fabric.observation.sound;

import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 模擬の発音源・位置・時刻で、server/clientの同じ発音だけが一件になるかを検査する。 */
class BodySoundDeduplicatorTest {
    private static final Vec3d SOURCE = new Vec3d(10,64,20);
    private static final long SECOND = 1_000_000_000L;

    @Test void serverThenClientAndClientThenServerKeepOriginalEmissionAge() {
        for (boolean serverFirst : new boolean[]{true,false}) {
            var policy = new BodySoundDeduplicator();
            long firstAt = serverFirst ? SECOND : SECOND+500_000_000L;
            long secondAt = serverFirst ? SECOND+500_000_000L : SECOND;
            policy.reconcile("weather.rain",SOURCE,firstAt,"first",serverFirst,2*SECOND);
            var duplicate = policy.reconcile("weather.rain",SOURCE.add(.1,0,0),secondAt,"second",!serverFirst,2*SECOND+100);
            assertEquals(SECOND,duplicate.heardAt());
            assertEquals("first",duplicate.previousSourceId());
            assertEquals(0,policy.pendingCount());
        }
    }
    @Test void distinctEventsPositionsAndRepeatedRealSoundsRemainIndependent() {
        var policy = new BodySoundDeduplicator();
        policy.reconcile("block.fire.ambient",SOURCE,SECOND,"a",false,SECOND);
        assertNull(policy.reconcile("block.fire.ambient",SOURCE,SECOND+100,"b",false,SECOND+100).previousSourceId());
        assertNull(policy.reconcile("weather.rain",SOURCE,SECOND,"c",true,SECOND+200).previousSourceId());
        assertNull(policy.reconcile("block.fire.ambient",SOURCE.add(3,0,0),SECOND,"d",true,SECOND+300).previousSourceId());
        assertNull(policy.reconcile("block.fire.ambient",SOURCE,3*SECOND,"e",true,3*SECOND).previousSourceId());
    }
    @Test void contextResetAndCapacityBoundPreventCrossContextOrUnboundedMatches() {
        var policy = new BodySoundDeduplicator();
        for (int i = 0; i < 1000; i++) policy.reconcile("sound"+i,SOURCE,SECOND,"id"+i,true,SECOND);
        assertEquals(128,policy.pendingCount());
        policy.reset();
        assertNull(policy.reconcile("sound999",SOURCE,SECOND,"client",false,SECOND).previousSourceId());
    }
    // 見込み: 同種の二体が同じ位置で発音しても、確認済みの別UUIDは別の音源。
    // 合格: 模擬server/client音を照合しても前のIDを引き継がず、二つの発音が独立して残る。
    @Test void differentKnownEmittersCannotBecomeOneSound() {
        var policy = new BodySoundDeduplicator();
        String a = java.util.UUID.randomUUID().toString(), b = java.util.UUID.randomUUID().toString();
        policy.reconcile("entity.skeleton.step", SOURCE, SECOND, a, true, SECOND);
        var second = policy.reconcile("entity.skeleton.step", SOURCE, SECOND, b, false, SECOND);
        assertNull(second.previousSourceId());
        assertEquals(2, policy.pendingCount());
    }

}
