package dogido.lab;

import dogido.lab.control.FollowAttention;
import net.minecraft.util.math.MathHelper;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 実時間を待たず、到着・見回し・休止・取消を実yawの変化で確かめる。 */
class FollowAttentionTest {
    @Test void arrivalKeepsTheHeadingForExactlyFiveSecondsThenLooksAround() {
        for (long seed = 0; seed < 12; seed++) {
            FollowAttention attention = new FollowAttention(seed);
            long arrived = 590; // 長い移動の終了時刻。選択開始時刻から待機時間を数えない。
            attention.arrived(arrived);
            float yaw = 179;
            for (long tick = arrived; tick < arrived+FollowAttention.DWELL_TICKS; tick++) {
                assertTrue(attention.dwelling(tick));
                assertEquals(yaw,attention.yaw(tick,yaw));
            }
            assertFalse(attention.dwelling(arrived+FollowAttention.DWELL_TICKS));
            assertNotEquals(yaw,attention.yaw(arrived+FollowAttention.DWELL_TICKS,yaw));
        }
    }

    @Test void catchUpAndFastOwnerMotionNarrowAttentionForThreeSecondsButOrdinaryWalkingDoesNot() {
        FollowAttention attention=new FollowAttention(1);
        attention.observeOwner(100,net.minecraft.util.math.Vec3d.ZERO);
        assertTrue(attention.settling(159));
        assertFalse(attention.settling(160));
        attention.observeOwner(160,new net.minecraft.util.math.Vec3d(12,0,0)); // 4block/秒の通常歩行。
        assertFalse(attention.settling(160));
        attention.observeOwner(170,new net.minecraft.util.math.Vec3d(15,0,0)); // 6block/秒。
        assertTrue(attention.settling(229));
        assertEquals(20,attention.yaw(229,20));
        attention.observeOwner(170,new net.minecraft.util.math.Vec3d(100,0,0)); // 同tick再読は期限も位置も変えない。
        attention.observeOwner(230,new net.minecraft.util.math.Vec3d(15,0,0));
        assertFalse(attention.settling(230));
        attention.reset();
        attention.observeOwner(231,new net.minecraft.util.math.Vec3d(15,0,0));
        assertTrue(attention.settling(231));
    }

    @Test void stationaryWaitingHasSlowTurnsSeparatedByPausesAndCanSeeNewDirections() {
        FollowAttention attention = new FollowAttention(8);
        float yaw = 175, travelled = 0;
        int movingTicks = 0, pausedTicks = 0, pauseAfterTurn = 0, turns = 0;
        boolean wasMoving = false;
        for (int tick = 0; tick < 2400; tick++) {
            float next = attention.yaw(tick,yaw);
            float delta = Math.abs(MathHelper.wrapDegrees(next-yaw));
            assertTrue(delta <= FollowAttention.YAW_STEP+1e-4);
            boolean moving = delta > 1e-4;
            if (moving && !wasMoving) {
                if (turns > 0) assertTrue(pauseAfterTurn >= FollowAttention.PAUSE_MIN_TICKS-1);
                turns++; pauseAfterTurn = 0;
            }
            if (moving) movingTicks++; else { pausedTicks++; pauseAfterTurn++; }
            travelled += delta; yaw = next; wasMoving = moving;
        }
        assertTrue(turns >= 6);
        assertTrue(travelled > 360);
        assertTrue(pausedTicks > movingTicks);
    }

    @Test void resettingDiscardsDwellAndOldLookTarget() {
        FollowAttention attention = new FollowAttention(5);
        attention.arrived(50);
        attention.reset();
        assertFalse(attention.dwelling(51));
        assertEquals(20,attention.yaw(51,20));
        attention.yaw(1000,20);
        assertTrue(attention.looking());
        attention.reset();
        assertFalse(attention.looking());
        assertEquals(-90,attention.yaw(1001,-90));
    }
}
