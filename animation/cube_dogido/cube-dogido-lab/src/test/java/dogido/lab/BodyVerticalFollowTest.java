package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 崖・竪穴・洞窟の実形状を純粋な移動計算へ渡し、高低差の開始と安全位置への到着を検査する。ゲーム描画の試験ではない。 */
class BodyVerticalFollowTest {
    private final BodyTuning tuning = new BodyTuning();

    @Test void heightGapBeyondFourStartsFollowInsideEightAndReleasesAfterArrival() {
        for (double height : new double[]{-4.001,4.001}) {
            BodyController c = new BodyController(1);
            var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1);
            Vec3d position = new Vec3d(1.75,1.1+height,0);
            var plan = c.plan(position,owner,0,true,Mode.FOLLOW,false,tuning,(p,w) -> true);
            assertEquals(Activity.FOLLOWING,plan.activity(),"高さ差="+height);
            assertEquals(1.1,plan.target().y,1e-9);
            assertEquals(Activity.HOLDING,c.plan(plan.target(),owner,1,true,Mode.FOLLOW,false,tuning,(p,w) -> true).activity());
            assertFalse(c.following());
        }
    }

    @Test void fourBlockHeightBoundaryAndNormalFloatingDoNotContinuouslyRetriggerFollow() {
        var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1);
        for (double height : new double[]{-4,-1.25,-.18,0,.18,1.25,4}) {
            BodyController c = new BodyController(4);
            assertEquals(Activity.HOLDING,c.plan(new Vec3d(1.75,1.1+height,0),owner,0,true,Mode.FOLLOW,
                false,tuning,(p,w) -> true).activity(),"高さ差="+height);
        }
    }

    @Test void oneBlockShaftHasASafeVerticalGoalWhenEverySideGoalIsInsideStone() {
        var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1,true);
        Box player = new Box(-.3,0,-.3,.3,1.8,.3).expand(BodyTuning.LIVING_MARGIN);
        BodyController.TargetSpace shaft = (p,w) -> {
            Box body = BodyMotion.box(p);
            return body.minX >= -.5 && body.maxX <= .5 && body.minZ >= -.5 && body.maxZ <= .5
                && !body.intersects(player);
        };
        BodyController c = new BodyController(2);
        var plan = c.plan(new Vec3d(0,20,0),owner,0,true,Mode.FOLLOW,false,tuning,shaft);
        assertEquals(Activity.FOLLOWING,plan.activity());
        assertNotEquals("no_safe_target",plan.reason());
        assertTrue(shaft.clear(plan.target(),false));
        assertFalse(BodyMotion.nearFront(plan.target(),owner.feet(),owner.yaw()));
    }

    @Test void diagonalPlayerViewDoesNotHideAnOpenOneBlockCaveCorridor() {
        var owner = new BodyController.Owner(Vec3d.ZERO,22.5f,1.1,true);
        Box player = new Box(-.3,0,-.3,.3,1.8,.3).expand(BodyTuning.LIVING_MARGIN);
        BodyController.TargetSpace cave = (p,w) -> {
            Box body = BodyMotion.box(p);
            return body.minX >= -.5 && body.maxX <= .5 && body.minY >= 0 && body.maxY <= 2
                && !body.intersects(player);
        };
        BodyController c = new BodyController(3);
        var plan = c.plan(new Vec3d(0,1.1,-20),owner,0,true,Mode.FOLLOW,false,tuning,cave);
        assertNotEquals("no_safe_target",plan.reason());
        assertTrue(cave.clear(plan.target(),false));
        assertTrue(plan.target().horizontalLength() <= 2);
    }

    @Test void actualFollowAndCallReachAboveOrBelowInAShaftWithoutCrossingTheOwner() {
        for (boolean call : new boolean[]{false,true}) for (double startHeight : new double[]{-12,12}) {
            var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1,true);
            Box player = new Box(-.3,0,-.3,.3,1.8,.3).expand(BodyTuning.LIVING_MARGIN);
            BodyController.TargetSpace shaft = (p,w) -> {
                Box body = BodyMotion.box(p);
                return body.minX >= -.5 && body.maxX <= .5 && body.minZ >= -.5 && body.maxZ <= .5
                    && !body.intersects(player);
            };
            BodyController c = new BodyController(5);
            if (call) c.call(UUID.randomUUID(),owner,0);
            Vec3d position = new Vec3d(0,startHeight,0), velocity = Vec3d.ZERO;
            IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
            boolean arrived = false;
            for (int tick=0; tick<390; tick++) {
                var plan = c.plan(position,owner,tick,true,Mode.FOLLOW,false,tuning,shaft);
                assertNotEquals("no_safe_target",plan.reason());
                if (plan.activity() == Activity.HOLDING) {
                    if (!arrived) assertEquals(call,c.takeArrived());
                    arrived = true;
                }
                Vec3d start = position;
                Vec3d target = arrived ? position : plan.target().add(0,wave.offset(),0);
                Vec3d waypoint = BodyMotion.aroundFront(position,target,owner.feet(),owner.yaw());
                velocity = BodyMotion.steer(position,waypoint,tuning.speedFor(plan.activity(),position.distanceTo(plan.target()),position.length()),
                    velocity,List.of(player),d -> BodyMotion.frontClear(start,d,owner.feet(),owner.yaw()));
                var floated = BodyMotion.withHover(position,velocity,wave.nextStep(),List.of(player),
                    d -> shaft.clear(start.add(d),false) && BodyMotion.frontClear(start,d,owner.feet(),owner.yaw()));
                position = position.add(floated.movement());
                if (floated.applied()) wave.advance();
                assertTrue(shaft.clear(position,false),"shaft position="+position);
                assertTrue(startHeight > 0 ? position.y > 2.5 : position.y < -.7,"ownerを横切らない");
                if (arrived && tick>250) break;
            }
            assertTrue(arrived,"call="+call+", startHeight="+startHeight+", position="+position);
            assertFalse(c.calling()); assertFalse(c.following());
        }
    }

    @Test void narrowWarpFallbackIsSafeAndDoesNotReplaceTheNormalOpenSpaceLanding() {
        var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1,true);
        List<Vec3d> candidates = CallPlacement.warpCandidates(owner,false);
        assertEquals(3,candidates.getFirst().horizontalLength(),1e-9);
        Box player = new Box(-.3,0,-.3,.3,1.8,.3).expand(BodyTuning.LIVING_MARGIN);
        var inShaft = candidates.stream().filter(p -> {
            Box b = BodyMotion.box(p);
            return b.minX >= -.5 && b.maxX <= .5 && b.minZ >= -.5 && b.maxZ <= .5 && !b.intersects(player);
        }).filter(p -> !BodyMotion.nearFront(p,owner.feet(),owner.yaw())).findFirst();
        assertTrue(inShaft.isPresent()); assertEquals(2.75,inShaft.get().y,1e-9);
        assertTrue(CallPlacement.warpCandidates(owner,true).stream().allMatch(p -> p.horizontalLength() >= 3-1e-9));
    }

    @Test void inspectionHeightLeavesRoomForArrivalToleranceAndHoverAtEveryOwnerPose() {
        for (double eyeHeight : new double[]{.4,1.27,1.62}) for (double side : new double[]{-1,1}) {
            double followHeight=dogido.lab.api.BodyProtocol.followBodyHeight(eyeHeight,.625);
            double targetHeight=followHeight+side*3.2;
            assertTrue(dogido.lab.api.BodyProtocol.inspectionHeightAllowed(side*3.2));
            assertFalse(dogido.lab.api.BodyProtocol.inspectionHeightAllowed(side*3.201));
            assertFalse(dogido.lab.api.BodyProtocol.inspectionHeightAllowed(Double.NaN));
            var owner=new BodyController.Owner(Vec3d.ZERO,0,followHeight);
            BodyController c=new BodyController(8);
            var plan=c.plan(new Vec3d(1.75,targetHeight+side*(.4+2*.18),0),owner,0,true,Mode.FOLLOW,false,tuning,(p,w)->true);
            assertEquals(Activity.HOLDING,plan.activity());
        }
    }

    @Test void compactGoalDoesNotConfirmArrivalWhileOwnerHasMovedIntoCurrentBody() {
        var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1,true);
        Box player = new Box(-.3,0,-.3,.3,1.8,.3).expand(BodyTuning.LIVING_MARGIN);
        Vec3d goal = new Vec3d(0,1.1,-.85), overlapping = new Vec3d(0,1.1,-.66);
        BodyController.TargetSpace narrow = (p,w) -> p.distanceTo(goal) < .21 && !BodyMotion.box(p).intersects(player);
        for (boolean call : new boolean[]{false,true}) {
            BodyController c = new BodyController(7);
            if (call) c.call(UUID.randomUUID(),owner,0);
            c.plan(new Vec3d(0,1.1,-13),owner,0,true,Mode.FOLLOW,false,tuning,narrow);
            var blocked = c.plan(overlapping,owner,1,true,Mode.FOLLOW,false,tuning,narrow);
            assertEquals(call ? Activity.CALLING : Activity.FOLLOWING,blocked.activity());
            assertFalse(c.takeArrived());
            assertEquals(Activity.HOLDING,c.plan(goal,owner,2,true,Mode.FOLLOW,false,tuning,narrow).activity());
        }
    }

    @Test void inspectionArrivalAtEitherHoverExtremeStaysInsideHeightRangeForTwoCycles() {
        for (double side : new double[]{-1,1}) {
            var owner=new BodyController.Owner(Vec3d.ZERO,0,1.1);
            Vec3d goal=new Vec3d(1.75,1.1+side*3.2,0),position=goal.add(0,side*.399,0);
            BodyController c=new BodyController(9);
            c.inspect(UUID.randomUUID(),goal,600);
            assertEquals("inspection_dwell",c.plan(position,owner,0,true,Mode.FOLLOW,false,tuning,(p,w)->true).reason());
            IdleMotion.FloatCycle wave=new IdleMotion.FloatCycle();
            for (int phase=0;phase<(side > 0 ? 90 : 30);phase++) wave.advance();
            for (int tick=1;tick<=240;tick++) {
                position=position.add(0,wave.nextStep(),0); wave.advance();
                assertEquals(Activity.HOLDING,c.plan(position,owner,tick,true,Mode.FOLLOW,false,tuning,(p,w)->true).activity());
                assertTrue(Math.abs(position.y-1.1) < 4);
            }
        }
    }

    @Test void openSpaceStillChoosesBesideInsteadOfOverheadAndUnavailableSpaceNeverBecomesASuccess() {
        var owner = new BodyController.Owner(Vec3d.ZERO,0,1.1,true);
        BodyController c = new BodyController(6);
        var open = c.plan(new Vec3d(0,12,0),owner,0,true,Mode.FOLLOW,false,tuning,(p,w)->true);
        assertEquals(1.75,open.target().horizontalLength(),1e-9);
        var unavailable = c.plan(new Vec3d(0,12,0),owner,1,true,Mode.FOLLOW,false,tuning,(p,w)->false);
        assertEquals("no_safe_target",unavailable.reason()); assertTrue(c.following()); assertFalse(c.takeArrived());
    }
}
