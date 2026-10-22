package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyFollowTest {
    private final BodyTuning tuning = new BodyTuning();
    private final BodyController.Owner owner = new BodyController.Owner(Vec3d.ZERO,0,1.1);
    private final BodyController.TargetSpace open = (p,wall) -> true;
    private BodyController.Plan plan(BodyController controller, Vec3d body, long tick, BodySafety safety) {
        return controller.plan(body,owner,tick,true,Mode.FOLLOW,false,tuning,open,safety);
    }
    @Test void inspectionArrivalDwellsAtItsPositionThenLooksWithoutMovingOrChangingTheOwner() {
        BodyController controller = new BodyController(14);
        Vec3d destination = new Vec3d(5,1,0);
        controller.inspect(UUID.randomUUID(),destination,600);
        plan(controller,new Vec3d(2,1,0),0,BodySafety.EMPTY);
        assertNull(controller.idleYaw(0,25));
        var arrival = plan(controller,destination,590,BodySafety.EMPTY);
        assertEquals("inspection_dwell",arrival.reason());
        assertEquals(Outcome.SUCCEEDED,controller.takeInspectionResult().outcome());
        float yaw = 25;
        boolean looked = false;
        for (int tick = 590; tick < 1200; tick++) {
            var waiting = plan(controller,destination,tick,BodySafety.EMPTY);
            assertEquals(Activity.HOLDING,waiting.activity());
            assertEquals(destination,waiting.target());
            if (tick < 690) {
                assertEquals("inspection_dwell",waiting.reason());
                assertTrue(controller.inspectionDwelling(tick));
            } else assertFalse(controller.inspectionDwelling(tick));
            float next = controller.idleYaw(tick,yaw);
            if (next != yaw) looked = true;
            yaw = next;
        }
        assertTrue(looked);
        assertFalse(controller.inspectionDwelling(1200));
    }

    @Test void catchingUpWaitsThreeSecondsAndAnInspectionStopsOnlyTheLookaround() {
        BodyController controller=new BodyController(21);
        var follow=plan(controller,new Vec3d(20,1,0),0,BodySafety.EMPTY);
        Vec3d body=follow.target();
        assertEquals("follow_settling",plan(controller,body,10,BodySafety.EMPTY).reason());
        assertEquals("follow_settling",plan(controller,body,69,BodySafety.EMPTY).reason());
        assertEquals("within_follow_distance",plan(controller,body,70,BodySafety.EMPTY).reason());
        controller.idleYaw(70,0);
        assertTrue(controller.lookingAround());
        controller.inspect(UUID.randomUUID(),new Vec3d(3,1,2),700);
        assertFalse(controller.lookingAround());
        assertEquals(Activity.MOVING,plan(controller,body,71,BodySafety.EMPTY).activity());
        assertNull(controller.idleYaw(71,0));
    }

    @Test void followCallDangerAndModeChangesPreemptDwellAndLookaround() {
        for (String priority : List.of("distance","call","danger","direct","disabled","owner")) {
            BodyController controller = new BodyController(15);
            Vec3d body = new Vec3d(5,1,0);
            controller.inspect(UUID.randomUUID(),body,600);
            plan(controller,body,0,BodySafety.EMPTY);
            assertTrue(controller.inspectionDwelling(1));
            if (priority.equals("call")) controller.call(UUID.randomUUID(),owner,1);
            BodySafety danger = priority.equals("danger") ? new BodySafety(List.of(new BodySafety.Corridor(body,body,1))) : BodySafety.EMPTY;
            controller.plan(priority.equals("distance") ? new Vec3d(20,1,0) : body,
                priority.equals("owner") ? null : owner,1,!priority.equals("disabled"),
                priority.equals("direct") ? Mode.DIRECT : Mode.FOLLOW,false,tuning,open,danger);
            assertFalse(controller.inspectionDwelling(1),priority);
            assertNull(controller.idleYaw(1,30),priority);
        }
    }
    @Test void emergencyFollowStartsBeyondTwelveAndContinuesUntilBesideTheOwner() {
        BodyController controller = new BodyController(1);
        assertEquals(Activity.HOLDING,plan(controller,new Vec3d(12,0,0),0,BodySafety.EMPTY).activity());
        var moving = plan(controller,new Vec3d(12.001,0,0),1,BodySafety.EMPTY);
        assertEquals(Activity.FOLLOWING,moving.activity());
        assertEquals(1.75,moving.target().horizontalLength(),1e-8);
        for (double distance : new double[]{7,5,3,2})
            assertEquals(Activity.FOLLOWING,plan(controller,new Vec3d(distance,0,0),2,BodySafety.EMPTY).activity());
        assertEquals(Activity.HOLDING,plan(controller,moving.target(),3,BodySafety.EMPTY).activity());
        assertFalse(controller.following());
        for (int tick = 4; tick < 300; tick++) {
            Vec3d body = new Vec3d(7,1,0);
            var turned = new BodyController.Owner(Vec3d.ZERO,tick*17,1.1);
            assertEquals(body,controller.plan(body,turned,tick,true,Mode.FOLLOW,false,tuning,open).target());
        }
    }
    @Test void sustainedMovementReachesBesideTheOwnerIncludingPlayerCollisionAndHover() {
        for (Vec3d start : List.of(new Vec3d(22,1,0),new Vec3d(-20,5,0),new Vec3d(0,1,22),new Vec3d(0,1,-22))) {
            BodyController controller = new BodyController(2);
            Vec3d position = start, velocity = Vec3d.ZERO;
            IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
            Box player = new Box(-.38,-.08,-.38,.38,1.88,.38);
            boolean stopped = false;
            for (int tick = 0; tick < 600; tick++) {
                var plan = plan(controller,position,tick,BodySafety.EMPTY);
                if (plan.activity() == Activity.HOLDING) { stopped = true; break; }
                double speed = tuning.speedFor(plan.activity(),position.distanceTo(plan.target()),position.distanceTo(owner.feet()));
                Vec3d hovering = plan.target().add(0,wave.offset(),0);
                velocity = BodyMotion.steer(position,hovering,speed,velocity,List.of(player),d -> true);
                var floated = BodyMotion.withHover(position,velocity,wave.nextStep(),List.of(player),d -> true);
                position = position.add(floated.movement());
                if (floated.applied()) wave.advance();
                assertFalse(BodyMotion.box(position).intersects(player));
            }
            assertTrue(stopped,start.toString());
            assertTrue(position.horizontalLength() <= 2,position.toString());
            assertTrue(Math.abs(position.y-owner.bodyHeight()) <= .2,position.toString());
            assertFalse(BodyMotion.nearFront(position,owner.feet(),owner.yaw()));
        }
    }
    @Test void movingOwnerUpdatesTheGoalAndCameraTurnsDoNotOrbitDuringOrAfterCatchup() {
        BodyController controller = new BodyController(3);
        Vec3d body = new Vec3d(20,1,0);
        var initial = plan(controller,body,0,BodySafety.EMPTY);
        var moved = new BodyController.Owner(new Vec3d(3,2,5),150,1.1);
        var next = controller.plan(body,moved,1,true,Mode.FOLLOW,false,tuning,open);
        assertEquals(initial.target().add(moved.feet()),next.target());
        var atOld = controller.plan(initial.target(),moved,2,true,Mode.FOLLOW,false,tuning,open);
        assertEquals(Activity.FOLLOWING,atOld.activity());
        assertEquals(Activity.HOLDING,controller.plan(next.target(),moved,3,true,Mode.FOLLOW,false,tuning,open).activity());
        assertEquals(next.target(),controller.plan(next.target(),owner,4,true,Mode.FOLLOW,false,tuning,open).target());
    }
    @Test void changingFollowDistanceChangesOnlyTheStartAndCannotOvertakeTheWarpThreshold() {
        tuning.set("follow_distance",10);
        BodyController controller = new BodyController(3);
        assertEquals(Activity.FOLLOWING,plan(controller,new Vec3d(15.001,0,0),0,BodySafety.EMPTY).activity());
        var eight = plan(controller,new Vec3d(8,0,0),1,BodySafety.EMPTY);
        assertEquals(Activity.FOLLOWING,eight.activity());
        assertEquals(Activity.HOLDING,plan(controller,eight.target(),2,BodySafety.EMPTY).activity());
        assertThrows(IllegalArgumentException.class,() -> tuning.set("follow_distance",23));
        tuning.set("follow_distance",20);
        assertThrows(IllegalArgumentException.class,() -> tuning.set("warp_distance",21));
    }
    @Test void changingOwnerPoseUpdatesArrivalHeightWithoutMovingTheChosenHorizontalSide() {
        BodyController controller = new BodyController(20);
        var initial = plan(controller,new Vec3d(20,1,0),0,BodySafety.EMPTY);
        var crouched = new BodyController.Owner(Vec3d.ZERO,90,.6);
        var next = controller.plan(initial.target(),crouched,1,true,Mode.FOLLOW,false,tuning,open);
        assertEquals(Activity.FOLLOWING,next.activity());
        assertEquals(initial.target().x,next.target().x,1e-8);
        assertEquals(initial.target().z,next.target().z,1e-8);
        assertEquals(.6,next.target().y,1e-8);
        assertEquals(Activity.HOLDING,controller.plan(next.target(),crouched,2,true,Mode.FOLLOW,false,tuning,open).activity());
    }
    @Test void blockedBesideGoalUsesAnotherSafeCandidateAndNoGoalDoesNotCompleteFollow() {
        BodyController controller = new BodyController(12);
        Vec3d position = new Vec3d(20,1,0), velocity = Vec3d.ZERO;
        Box block = new Box(1.25,-1,-.5,2.25,4,.5);
        BodyController.TargetSpace clear = (p,wall) -> !BodyMotion.box(p).intersects(block);
        var first = controller.plan(position,owner,0,true,Mode.FOLLOW,false,tuning,clear);
        assertEquals("distance_follow",first.reason());
        assertFalse(BodyMotion.box(first.target()).intersects(block));
        assertEquals(1.75,first.target().horizontalLength(),1e-8);
        for (int tick = 0; tick < 600 && controller.following(); tick++) {
            var plan = controller.plan(position,owner,tick,true,Mode.FOLLOW,false,tuning,clear);
            assertNotEquals("no_safe_target",plan.reason());
            double speed = tuning.speedFor(plan.activity(),position.distanceTo(plan.target()),position.distanceTo(owner.feet()));
            velocity = BodyMotion.steer(position,plan.target(),speed,velocity,List.of(block),d -> true);
            position = position.add(velocity);
            assertFalse(BodyMotion.box(position).intersects(block));
        }
        assertFalse(controller.following());
        assertTrue(position.horizontalLength() <= 2,"a single blocked goal must not prevent catching up");
        assertEquals("no_safe_target",controller.plan(new Vec3d(20,1,0),owner,601,true,Mode.FOLLOW,false,
            tuning,(p,wall) -> false).reason());
        assertEquals("no_safe_target",controller.plan(new Vec3d(4,1,0),owner,602,true,Mode.FOLLOW,false,
            tuning,(p,wall) -> false).reason());
        assertEquals(Activity.FOLLOWING,plan(controller,new Vec3d(4,1,0),603,BodySafety.EMPTY).activity());
    }
    @Test void workStartingDuringFollowReplansAndAvoidsCrossingTheCurrentWorkFront() {
        BodyController controller = new BodyController(16);
        Vec3d position = new Vec3d(20,1,0), velocity = Vec3d.ZERO;
        var first = plan(controller,position,0,BodySafety.EMPTY);
        var busy = new BodyController.Owner(Vec3d.ZERO,-90,1.1,true);
        assertTrue(BodyMotion.nearFront(first.target(),busy.feet(),busy.yaw()));
        Box player = new Box(-.38,-.08,-.38,.38,1.88,.38);
        for (int tick = 1; tick < 600; tick++) {
            var plan = controller.plan(position,busy,tick,true,Mode.FOLLOW,false,tuning,open);
            assertFalse(BodyMotion.nearFront(plan.target(),busy.feet(),busy.yaw()));
            if (plan.activity() == Activity.HOLDING) break;
            double speed = tuning.speedFor(plan.activity(),position.distanceTo(plan.target()),position.distanceTo(busy.feet()));
            Vec3d current = position;
            Vec3d destination = BodyMotion.aroundFront(position,plan.target(),busy.feet(),busy.yaw());
            velocity = BodyMotion.steer(position,destination,speed,velocity,List.of(player),
                d -> BodyMotion.frontClear(current,d,busy.feet(),busy.yaw()));
            position = position.add(velocity);
        }
        assertFalse(controller.following()); assertTrue(position.horizontalLength() <= 2);
    }
    @Test void warpResumeCatchesUpFromInsideTheStartRadiusAndModeOrOwnerLossClearsIt() {
        for (double radius : new double[]{3,5,8,11}) {
            BodyController controller = new BodyController(17);
            controller.reset(); controller.resumeFollowing();
            var resumed = plan(controller,new Vec3d(radius,1,0),0,BodySafety.EMPTY);
            assertEquals(Activity.FOLLOWING,resumed.activity());
            assertEquals(Activity.HOLDING,plan(controller,resumed.target(),1,BodySafety.EMPTY).activity());
        }
        for (String transition : List.of("mode","owner","disabled")) {
            BodyController controller = new BodyController(18);
            controller.resumeFollowing();
            controller.plan(new Vec3d(5,1,0),transition.equals("owner") ? null : owner,0,
                !transition.equals("disabled"),transition.equals("mode") ? Mode.HOLD : Mode.FOLLOW,false,tuning,open);
            assertFalse(controller.following());
            assertEquals(Activity.HOLDING,plan(controller,new Vec3d(5,1,0),1,BodySafety.EMPTY).activity());
        }
    }
    @Test void dangerOnTheFirstDistantTickRemembersTheCatchupAndInspectionCannotPreemptIt() {
        BodyController controller = new BodyController(19);
        Vec3d start = new Vec3d(20,1,0);
        BodySafety danger = new BodySafety(List.of(new BodySafety.Corridor(start,start,1)));
        assertEquals(Activity.RETREATING,plan(controller,start,0,danger).activity());
        assertTrue(controller.following());
        controller.inspect(UUID.randomUUID(),new Vec3d(5,1,0),600);
        var safe = plan(controller,new Vec3d(8,1,0),1,BodySafety.EMPTY);
        assertEquals(Activity.FOLLOWING,safe.activity());
        assertEquals("distance_follow_has_priority",controller.takeInspectionResult().reason());
        assertEquals(Activity.HOLDING,plan(controller,safe.target(),2,BodySafety.EMPTY).activity());
    }
    @Test void horizontalEnemyClosureSelectsAnUpwardEscapeAndMovementLeavesTheOverlap() {
        BodyController controller = new BodyController(4);
        List<BodySafety.Enemy> enemies = new ArrayList<>();
        for (int i = 0; i < 8; i++) {
            double angle = i*Math.PI/4, x = Math.cos(angle)*3.5, z = Math.sin(angle)*3.5;
            enemies.add(new BodySafety.Enemy(new Box(x-.3,0,z-.3,x+.3,1.8,z+.3),Vec3d.ZERO));
        }
        BodySafety safety = BodySafety.capture(new Vec3d(0,.9,0),enemies);
        Vec3d position = new Vec3d(0,.9-BodyTuning.BODY_SIZE/2,0), velocity = Vec3d.ZERO;
        var first = plan(controller,position,0,safety);
        assertEquals("avoid_enemy_upward",first.reason()); assertTrue(first.target().y > position.y);
        for (int tick = 0; tick < 80 && safety.threatened(position); tick++) {
            var current = plan(controller,position,tick,safety);
            Vec3d start = position;
            velocity = BodyMotion.steer(position,current.target(),tuning.retreatSpeed,velocity,List.of(),d -> safety.allowsStep(start,d));
            assertTrue(safety.allowsStep(position,velocity)); position = position.add(velocity);
        }
        assertFalse(safety.threatened(position)); assertTrue(position.y > 1.5);
    }
    @Test void approachingOverheadEnemyCausesSidewaysAvoidanceDuringFollowWait() {
        BodyController controller = new BodyController(5);
        BodySafety safety = BodySafety.capture(new Vec3d(12,.9,12),List.of(
            new BodySafety.Enemy(new Box(-.3,6,-.3,.3,7.8,.3),new Vec3d(0,-.6,0))));
        Vec3d body = new Vec3d(0,1,0);
        assertTrue(safety.threatened(body));
        var avoiding = plan(controller,body,0,safety);
        assertEquals(Activity.RETREATING,avoiding.activity());
        assertEquals("avoid_enemy_sideways",avoiding.reason());
        assertEquals(body.y,avoiding.target().y,1e-8);
        assertTrue(safety.allowsStep(body,avoiding.target().subtract(body)));
    }
    @Test void movingEnemiesAreRecapturedAndCannotBeCrossedMidStep() {
        Vec3d position = new Vec3d(-5,1,4), velocity = Vec3d.ZERO;
        Vec3d destination = new Vec3d(5,1,4);
        for (int tick = 0; tick < 100; tick++) {
            double enemyZ = 7-tick*.06;
            BodySafety safety = BodySafety.capture(new Vec3d(0,.9,0),List.of(
                new BodySafety.Enemy(new Box(-.3,0,enemyZ-.3,.3,1.8,enemyZ+.3),new Vec3d(0,0,-.06))));
            Vec3d start = position;
            Vec3d next = BodyMotion.steer(position,destination,.25,velocity,List.of(),d -> safety.allowsStep(start,d));
            if (next.lengthSquared() > 0) assertTrue(safety.allowsStep(position,next));
            position = position.add(next); velocity = next;
        }
        assertTrue(position.distanceTo(new Vec3d(-5,1,4)) > 1,"the check should not disable all movement");
    }
    @Test void impossibleEscapeStopsAndDirectHoldModesDoNotGetAutonomousAvoidance() {
        BodySafety danger = new BodySafety(List.of(new BodySafety.Corridor(Vec3d.ZERO,Vec3d.ZERO,100)));
        Vec3d body = new Vec3d(2,1,0);
        BodyController controller = new BodyController(6);
        var blocked = plan(controller,body,0,danger);
        assertEquals("no_safe_target",blocked.reason()); assertEquals(body,blocked.target());
        for (Mode mode : List.of(Mode.DIRECT,Mode.HOLD)) {
            var manual = controller.plan(body,owner,1,true,mode,false,tuning,open,danger);
            assertEquals(Activity.HOLDING,manual.activity()); assertEquals(body,manual.target());
        }
    }
    @Test void inspectionArrivesOnlyAfterMovementAndExpiresAtItsOriginalDeadline() {
        BodyController controller = new BodyController(7);
        UUID id = UUID.randomUUID(); Vec3d start = new Vec3d(2,1,0), destination = new Vec3d(5,1,0);
        controller.inspect(id,destination,20);
        assertEquals(Activity.MOVING,plan(controller,start,0,BodySafety.EMPTY).activity());
        assertNull(controller.takeInspectionResult());
        controller.inspect(id,new Vec3d(8,1,0),1000);
        assertEquals(destination,plan(controller,start,1,BodySafety.EMPTY).target());
        plan(controller,destination,2,BodySafety.EMPTY);
        var result = controller.takeInspectionResult();
        assertEquals(id,result.id()); assertEquals(Outcome.SUCCEEDED,result.outcome()); assertEquals("arrived",result.reason());
        assertNull(controller.takeInspectionResult());
        controller.inspect(UUID.randomUUID(),destination,20);
        plan(controller,start,20,BodySafety.EMPTY);
        assertEquals(Outcome.EXPIRED,controller.takeInspectionResult().outcome());
        controller.inspect(UUID.randomUUID(),destination,10000);
        plan(controller,start,21,BodySafety.EMPTY);
        plan(controller,start,621,BodySafety.EMPTY);
        assertEquals(Outcome.EXPIRED,controller.takeInspectionResult().outcome());
    }
    @Test void priorityChangesInterruptInspectionAndNeverResumeAnOldGoal() {
        BodyController controller = new BodyController(8);
        Vec3d destination = new Vec3d(5,1,0), body = new Vec3d(2,1,0);
        controller.inspect(UUID.randomUUID(),destination,600);
        plan(controller,new Vec3d(18,0,0),0,BodySafety.EMPTY);
        assertEquals("distance_follow_has_priority",controller.takeInspectionResult().reason());
        assertEquals(Activity.HOLDING,plan(controller,new Vec3d(1.75,1.1,0),1,BodySafety.EMPTY).activity());
        controller.inspect(UUID.randomUUID(),destination,600);
        controller.call(UUID.randomUUID(),owner,2);
        assertEquals("call_has_priority",controller.takeInspectionResult().reason());
        assertEquals(Activity.CALLING,plan(controller,body,2,BodySafety.EMPTY).activity());
        controller.reset(); controller.inspect(UUID.randomUUID(),destination,600);
        controller.plan(body,owner,3,true,Mode.FOLLOW,true,tuning,open);
        assertEquals("retreat_has_priority",controller.takeInspectionResult().reason());
        controller.reset(); controller.inspect(UUID.randomUUID(),destination,600);
        BodySafety danger = new BodySafety(List.of(new BodySafety.Corridor(body,body,1)));
        plan(controller,body,4,danger);
        assertEquals("danger_has_priority",controller.takeInspectionResult().reason());
        assertEquals(Activity.HOLDING,plan(controller,body,5,BodySafety.EMPTY).activity());
    }
    @Test void ownerLossDisabledControlAndModeChangeDiscardInspectionAndResetClearsTheLifecycle() {
        BodyController controller = new BodyController(9); Vec3d body = new Vec3d(2,1,0);
        for (String transition : List.of("owner","disabled","mode")) {
            controller.inspect(UUID.randomUUID(),new Vec3d(5,1,0),600);
            controller.plan(body,transition.equals("owner") ? null : owner,0,!transition.equals("disabled"),
                transition.equals("mode") ? Mode.HOLD : Mode.FOLLOW,false,tuning,open);
            assertEquals(Outcome.INTERRUPTED,controller.takeInspectionResult().outcome());
            assertEquals(Activity.HOLDING,plan(controller,body,1,BodySafety.EMPTY).activity());
        }
        controller.inspect(UUID.randomUUID(),new Vec3d(5,1,0),600); controller.reset();
        assertEquals(Activity.HOLDING,plan(controller,body,1,BodySafety.EMPTY).activity());
        assertNull(controller.takeInspectionResult());
    }
    @Test void inspectionRechecksGoalSafetyBeforeMovement() {
        BodyController controller = new BodyController(10);
        Vec3d body = new Vec3d(-4,1,0), target = new Vec3d(4,1,0);
        controller.inspect(UUID.randomUUID(),target,600);
        assertEquals(Activity.MOVING,plan(controller,body,0,BodySafety.EMPTY).activity());
        BodySafety safety = new BodySafety(List.of(new BodySafety.Corridor(target,target,.5)));
        assertEquals(Activity.HOLDING,plan(controller,body,1,safety).activity());
        assertEquals("inspection_target_unsafe",controller.takeInspectionResult().reason());
    }
    @Test void inspectionActuallyMovesToTheChosenThreeDimensionalStandoffAndReportsArrival() {
        BodyController controller = new BodyController(11);
        UUID id = UUID.randomUUID(); Vec3d position = new Vec3d(2,1,0), velocity = Vec3d.ZERO;
        Vec3d destination = new Vec3d(5,3,2);
        controller.inspect(id,destination,600);
        BodyController.InspectionResult result = null;
        for (int tick = 0; tick < 600 && result == null; tick++) {
            var plan = plan(controller,position,tick,BodySafety.EMPTY);
            result = controller.takeInspectionResult();
            if (result != null) break;
            double speed = tuning.speedFor(plan.activity(),position.distanceTo(plan.target()),position.distanceTo(owner.feet()));
            velocity = BodyMotion.steer(position,plan.target(),speed,velocity,List.of(),d -> true);
            position = position.add(velocity);
        }
        assertNotNull(result); assertEquals(id,result.id()); assertEquals(Outcome.SUCCEEDED,result.outcome());
        assertEquals("arrived",result.reason()); assertTrue(position.distanceTo(destination) <= .4);
    }
}
