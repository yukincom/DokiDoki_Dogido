package dogido.lab;

import dogido.lab.control.BodyMotion;
import dogido.lab.control.BodyTuning;
import java.util.List;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class IdleMotionTest {
    @Test void hoverDoesNotAccumulateAltitude() {
        double y = 0;
        for (int tick = 1; tick <= IdleMotion.HOVER_PERIOD_TICKS*1000; tick++) {
            y += IdleMotion.hoverStep(tick);
            assertTrue(Math.abs(y) <= .180000001);
        }
        assertEquals(0, y, 1e-9);
    }

    @Test void movingAndWaitingShareTheSameGentleWaveWithoutSpeedOrAltitudeJumps() {
        IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
        Vec3d position = Vec3d.ZERO, motion = Vec3d.ZERO;
        double previousDy = wave.nextStep(), min = 0, max = 0;
        for (int tick = 0; tick < 1200; tick++) {
            // 走行→待機→走行でも実移動の上下波を保つ。慣性には通常移動だけを渡す。
            boolean moving = tick < 240 || tick >= 600;
            Vec3d target = moving ? new Vec3d(position.x+10,wave.offset(),0) : position;
            motion = BodyMotion.steer(position,target,.14,motion,List.of(),d -> true);
            var floated = BodyMotion.withHover(position,motion,wave.nextStep(),List.of(),d -> true);
            assertTrue(floated.applied());
            position = position.add(floated.movement()); wave.advance();
            assertEquals(wave.offset(),position.y,1e-9);
            assertTrue(Math.abs(floated.movement().y) < .01,"上下速度は毎tick 1cm未満");
            assertTrue(Math.abs(floated.movement().y-previousDy) < .0005,"折り返し・停止・再開で跳ねない");
            previousDy = floated.movement().y;
            min = Math.min(min,position.y); max = Math.max(max,position.y);
        }
        assertEquals(-.18,min,1e-9); assertEquals(.18,max,1e-9);
        assertEquals(0,position.y,1e-9);
    }

    @Test void blockedFloatPausesItsPhaseAndResumesWithoutAccumulatingAltitude() {
        IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
        Vec3d position = Vec3d.ZERO;
        for (int tick = 0; tick < 12000; tick++) {
            boolean safe = tick%120 < 60;
            var floated = BodyMotion.withHover(position,Vec3d.ZERO,wave.nextStep(),List.of(),d -> safe);
            position = position.add(floated.movement());
            if (floated.applied()) wave.advance();
            assertEquals(safe,floated.applied());
            assertEquals(wave.offset(),position.y,1e-9);
            assertTrue(Math.abs(position.y) <= .180000001);
        }
        assertEquals(0,position.y,1e-9);
    }

    @Test void lowCeilingAndLivingCollisionRejectTheExtraFloatButKeepTheSafeBaseStep() {
        Box ceiling = new Box(-10,BodyTuning.BODY_SIZE+.003,-10,10,2,10);
        Vec3d base = new Vec3d(.1,0,0);
        var blocked = BodyMotion.withHover(Vec3d.ZERO,base,IdleMotion.hoverStep(1),List.of(ceiling),d -> true);
        assertFalse(blocked.applied()); assertEquals(base,blocked.movement());
        // 通常ブロックは衝突リストにない制御でも、最終位置の既存空間検査で止める。
        var worldBlocked = BodyMotion.withHover(Vec3d.ZERO,base,IdleMotion.hoverStep(1),List.of(),
            d -> !BodyMotion.box(d).intersects(ceiling));
        assertFalse(worldBlocked.applied()); assertEquals(base,worldBlocked.movement());
    }

    @Test void safeCallHeightUnderLowCeilingCannotBecomeAnUnsafeHoverTarget() {
        Vec3d goal = new Vec3d(0,.75,0), position = new Vec3d(3,.75,0), motion = Vec3d.ZERO;
        Box ceiling = new Box(-10,1.5,-10,10,2,10);
        IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
        for (int i=0; i<30; i++) wave.advance(); // 上端位相。基準目標は空いているが補正目標は天井内。
        assertFalse(BodyMotion.box(goal).intersects(ceiling));
        assertTrue(BodyMotion.box(goal.add(0,wave.offset(),0)).intersects(ceiling));
        for (int tick=0; tick<80; tick++) {
            var target = BodyMotion.hoverTarget(goal,wave.offset(),p -> !BodyMotion.box(p).intersects(ceiling));
            assertFalse(target.applied()); assertEquals(goal,target.position());
            motion = BodyMotion.steer(position,target.position(),.14,motion,List.of(),d -> true);
            position = position.add(motion);
            assertFalse(BodyMotion.box(position).intersects(ceiling));
        }
        assertTrue(position.distanceTo(goal) < .2,"低い安全位置へ実際に到着する");
        assertEquals(.18,wave.offset(),1e-9,"浮遊休止中は位相も進めない");
    }

    @Test void loopClosesWithoutASnapForEveryAxis() {
        for (boolean left : new boolean[]{false, true}) {
            for (int axis = 0; axis < 3; axis++) {
                assertEquals(IdleMotion.angle(left, axis, 0),
                    IdleMotion.angle(left, axis, IdleMotion.LENGTH - .000001F), .0001);
                assertEquals(IdleMotion.angle(left, axis, .625F),
                    IdleMotion.angle(left, axis, IdleMotion.LENGTH * 3 + .625F), .0001);
            }
        }
    }

    @Test void handsFlapInOppositeDirections() {
        assertEquals(Math.toRadians(32.5), IdleMotion.angle(false, 2, .625F), 1e-6);
        assertEquals(Math.toRadians(-32.5), IdleMotion.angle(true, 2, .625F), 1e-6);
    }
}
