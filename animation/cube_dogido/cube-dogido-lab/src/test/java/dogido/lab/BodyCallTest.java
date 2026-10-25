package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 呼び寄せの立ち位置・再到着・作業切替を実際の移動幾何と組み合わせて確認する。画面描画やゲーム内通信の検査ではない。 */
class BodyCallTest {
    private final BodyTuning tuning = new BodyTuning();
    private final BodyController.TargetSpace open = (p,w) -> true;
    private BodyController.Owner owner(double z, boolean working) {
        return new BodyController.Owner(new Vec3d(0,0,z),0,1.1,working);
    }
    private BodyController.Plan plan(BodyController c, Vec3d body, BodyController.Owner owner, long tick,
            BodyController.TargetSpace space) {
        return c.plan(body,owner,tick,true,Mode.FOLLOW,false,tuning,space);
    }

    @Test void frontIsPreferredAndBlockedFrontFallsBackWithinTwoBlocks() {
        BodyController c = new BodyController(1);
        c.call(UUID.randomUUID(),owner(0,false),0);
        var front = plan(c,new Vec3d(0,1,10),owner(0,false),0,open);
        assertEquals(0,front.target().x,1e-6); assertEquals(1.75,front.target().z,1e-6);
        var side = plan(c,front.target(),owner(0,false),1,(p,w) -> p.z <= 0.01);
        assertNotEquals("no_safe_target",side.reason());
        assertTrue(side.target().horizontalLength() <= 2);
        assertTrue(side.target().z <= .01);
    }

    @Test void workDuringArrivalImmediatelyChangesTheTargetToSideOrRear() {
        BodyController c = new BodyController(2);
        c.call(UUID.randomUUID(),owner(0,false),0);
        Vec3d front = plan(c,Vec3d.ZERO,owner(0,false),0,open).target();
        plan(c,front.add(0,0,.3),owner(0,false),1,open);
        var busy = plan(c,front,owner(0,true),10,open);
        assertTrue(busy.target().z <= .001); assertTrue(busy.target().horizontalLength() <= 2);
        assertEquals("moving",busy.reason()); assertFalse(c.takeArrived());
        for (var candidate : CallPlacement.candidates(owner(0,true),2)) {
            assertTrue(candidate.z <= .001); assertTrue(candidate.horizontalLength() <= 2);
        }
    }

    @Test void currentArrivalImmediatelyReleasesCallAndOwnerTurnsOrSmallMovesDoNotReattach() {
        BodyController c = new BodyController(3);
        c.call(UUID.randomUUID(),owner(0,false),0);
        Vec3d first = plan(c,Vec3d.ZERO,owner(0,false),0,open).target();
        var moved = plan(c,first,owner(3,false),25,open);
        assertEquals(4.75,moved.target().z,1e-6); assertFalse(c.takeArrived());
        var arrival = plan(c,moved.target(),owner(3,false),26,open);
        assertEquals(Activity.HOLDING,arrival.activity());
        assertTrue(c.takeArrived()); assertFalse(c.takeArrived()); assertFalse(c.calling());
        assertNull(c.takeCallEnd(),"成功を取消で上書きしない");
        for (int tick=27; tick<300; tick++) {
            var turned = new BodyController.Owner(new Vec3d(0,0,3.8),tick*13,1.1);
            var normal = plan(c,moved.target(),turned,tick,open);
            assertEquals(Activity.HOLDING,normal.activity());
            assertEquals(moved.target(),normal.target());
            assertFalse(c.takeArrived()); assertNull(c.takeCallEnd());
        }
        c.inspect(UUID.randomUUID(),new Vec3d(5,1,3),600);
        assertEquals(Activity.MOVING,plan(c,moved.target(),owner(3,false),301,open).activity());
    }

    @Test void realSteeringReachesTwoBlocksAfterOwnerMovesWhileApproaching() {
        for (boolean working : new boolean[]{false,true}) for (int phase = 0; phase < 120; phase += 15) {
            BodyController c = new BodyController(4);
            c.call(UUID.randomUUID(),owner(0,working),0);
            Vec3d position = new Vec3d(-8,1.1,-6), velocity = Vec3d.ZERO;
            IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
            for (int i = 0; i < phase; i++) wave.advance();
            boolean arrived = false;
            for (int tick=0; tick<390; tick++) {
                var current = owner(tick<60 ? tick*.04 : 2.4,working);
                var p = plan(c,position,current,tick,open);
                Box player = new Box(-.3,0,current.feet().z-.3,.3,1.8,current.feet().z+.3);
                Vec3d start = position;
                Vec3d target = p.target().add(0,wave.offset(),0);
                Vec3d destination = working ? BodyMotion.aroundFront(start,target,current.feet(),current.yaw()) : target;
                velocity = BodyMotion.steer(start,destination,tuning.speedFor(p.activity(),start.distanceTo(p.target()),
                    start.distanceTo(current.feet())),velocity,List.of(player),
                    d -> !working || BodyMotion.frontClear(start,d,current.feet(),current.yaw()));
                var floated = BodyMotion.withHover(start,velocity,wave.nextStep(),List.of(player),
                    d -> !working || BodyMotion.frontClear(start,d,current.feet(),current.yaw()));
                position = position.add(floated.movement());
                if (floated.applied()) wave.advance();
                assertFalse(BodyMotion.box(position).intersects(player));
                if (c.takeArrived()) { arrived = true; assertTrue(position.subtract(current.feet()).horizontalLength() <= 2); break; }
            }
            assertTrue(arrived,"working="+working+", phase="+phase+", position="+position);
        }
    }

    @Test void unsafeCandidatesNeverProduceArrivalAndCallStillExpires() {
        BodyController c = new BodyController(5);
        c.call(UUID.randomUUID(),owner(0,false),0);
        for (long tick : new long[]{0,20,100,399}) {
            assertEquals("no_safe_target",plan(c,Vec3d.ZERO,owner(0,false),tick,(p,w)->false).reason());
            assertFalse(c.takeArrived());
        }
        plan(c,Vec3d.ZERO,owner(0,false),400,open);
        assertFalse(c.calling()); assertEquals("call_timeout",c.takeCallEnd());
    }

    @Test void warpContinuationKeepsOriginalDeadlineAndMustRecheckArrival() {
        BodyController c = new BodyController(6);
        UUID id=UUID.randomUUID(); c.call(id,owner(0,false),0);
        var carried=c.continuation(); c.reset();
        c.call(id,owner(0,false),350); c.retainCallDeadline(carried);
        assertFalse(c.takeArrived());
        plan(c,new Vec3d(0,1,8),owner(0,false),400,open);
        assertFalse(c.calling()); assertEquals("call_timeout",c.takeCallEnd());
    }
    @Test void dangerPreventsArrivalUntilTheCurrentPositionIsSafe() {
        BodyController c = new BodyController(7);
        c.call(UUID.randomUUID(),owner(0,false),0);
        Vec3d target = plan(c,Vec3d.ZERO,owner(0,false),0,open).target();
        BodySafety danger = BodySafety.capture(new Vec3d(0,.9,0),List.of(
            new BodySafety.Enemy(new Box(-.3,0,1,.3,1.8,1.6),Vec3d.ZERO)));
        for (int tick : new int[]{10,50,100}) {
            assertEquals(Activity.RETREATING,c.plan(target,owner(0,false),tick,true,Mode.FOLLOW,false,tuning,open,danger).activity());
            assertFalse(c.takeArrived());
        }
        plan(c,target,owner(0,false),101,open); assertTrue(c.takeArrived()); assertFalse(c.calling());
        plan(c,target,owner(0,false),102,open); assertFalse(c.takeArrived());
    }
}
