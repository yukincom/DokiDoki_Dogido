package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyMovementTest {
    private final BodyTuning tuning = new BodyTuning();
    private final BodyController.Owner owner = new BodyController.Owner(Vec3d.ZERO,0,1.1);
    private final BodyController.TargetSpace open = (p,wall) -> true;

    @Test void thinLavaOnlyHurtsWhenTheBodyActuallyIntersectsTheLiquidSurface() {
        assertFalse(BodyMotion.touchesFluid(BodyMotion.box(new Vec3d(.5,.2,.5)),0,0,0,.125));
        assertTrue(BodyMotion.touchesFluid(BodyMotion.box(new Vec3d(.5,.1,.5)),0,0,0,.125));
        assertFalse(BodyMotion.touchesFluid(BodyMotion.box(new Vec3d(.5,.125,.5)),0,0,0,.125));
        assertFalse(BodyMotion.touchesFluid(BodyMotion.box(new Vec3d(.5,0,.5)),0,0,0,0));
    }

    @Test void normalTargetIsRightFrontAtShoulderAndExplorationUsesSeparateBand() {
        BodyController controller = new BodyController(42);
        var follow = controller.plan(new Vec3d(3,1,0),owner,0,true,Mode.FOLLOW,false,tuning,open);
        assertTrue(follow.target().x < 0); assertTrue(follow.target().z > 0);
        assertEquals(3,follow.target().horizontalLength(),1e-8);
        assertEquals(1.1,follow.target().y,1e-8);
        for (int tick = 1; tick < 3000; tick += 201) {
            var explore = controller.plan(follow.target(),owner,tick,true,Mode.EXPLORE,false,tuning,open);
            double radius = explore.target().horizontalLength();
            assertTrue(radius >= 5 && radius <= 10,"exploration radius="+radius);
        }
    }
    @Test void retreatIsBehindOwnerWhileCallUsesFrontAndReturnsToExploration() {
        BodyController controller = new BodyController(1);
        var retreat = controller.plan(new Vec3d(3,1,0),owner,0,true,Mode.EXPLORE,true,tuning,open);
        assertTrue(retreat.target().z < 0); assertEquals(Activity.RETREATING,retreat.activity());
        controller.call(UUID.randomUUID(),owner,1);
        var call = controller.plan(retreat.target(),owner,1,true,Mode.EXPLORE,false,tuning,open);
        assertTrue(call.target().z > 2); assertEquals(Activity.CALLING,call.activity());
        controller.plan(call.target(),owner,2,true,Mode.EXPLORE,false,tuning,open);
        assertTrue(controller.takeArrived());
        var restored = controller.plan(call.target(),owner,105,true,Mode.EXPLORE,false,tuning,open);
        assertEquals(Activity.EXPLORING,restored.activity());
        controller.reset();
        controller.call(UUID.randomUUID(),owner,106);
        assertNull(controller.takeCallEnd(),"a new lifecycle must not inherit the old call completion");
        assertFalse(controller.takeArrived());
    }
    @Test void nearbyEnemyCannotLeaveRetreatGoalInsideItsExclusionArea() {
        BodyController controller = new BodyController(6);
        // Enemy in front: its retreat clearance covers the preferred rear position.
        Box exclusion = new Box(-.3,0,.7,.3,1.8,1.3).expand(5);
        BodyController.TargetSpace safe = (p,wall) -> !BodyMotion.box(p).intersects(exclusion);
        Vec3d position = new Vec3d(-2.12,1.1,2.12), velocity = Vec3d.ZERO;
        var plan = controller.plan(position,owner,0,true,Mode.FOLLOW,true,tuning,safe);
        assertNotEquals("no_safe_target",plan.reason());
        assertTrue(plan.target().z < -tuning.retreatDistance);
        assertFalse(BodyMotion.box(plan.target()).intersects(exclusion));
        for (int tick = 0; tick < 500 && position.distanceTo(plan.target()) > .35; tick++) {
            Vec3d start = position;
            Vec3d waypoint = BodyMotion.aroundFront(position,plan.target(),owner.feet(),owner.yaw());
            Vec3d step = BodyMotion.steer(position,waypoint,tuning.retreatSpeed,velocity,List.of(exclusion),
                d -> BodyMotion.frontClear(start,d,owner.feet(),owner.yaw()));
            double before = BodyMotion.overlapDepth(BodyMotion.box(position),List.of(exclusion));
            position = position.add(step); velocity = step;
            assertTrue(BodyMotion.overlapDepth(BodyMotion.box(position),List.of(exclusion)) <= before+1e-7);
        }
        assertTrue(position.distanceTo(plan.target()) < .5,"blocked at "+position);
    }
    @Test void ordinaryTargetsRejectWallsButRetreatCanUseWallRefuge() {
        BodyController controller = new BodyController(2);
        BodyController.TargetSpace wallOnly = (p,wall) -> wall;
        assertEquals("no_safe_target",controller.plan(Vec3d.ZERO,owner,0,true,Mode.FOLLOW,false,tuning,wallOnly).reason());
        var retreat = controller.plan(Vec3d.ZERO,owner,1,true,Mode.FOLLOW,true,tuning,wallOnly);
        assertEquals("wall_refuge",retreat.reason()); assertTrue(retreat.allowWall());
    }
    @Test void suddenCameraTurnDoesNotMakeBodyInstantlyOrbitOwner() {
        BodyController controller = new BodyController(2);
        var first = controller.plan(Vec3d.ZERO,owner,0,true,Mode.FOLLOW,false,tuning,open);
        var turned = controller.plan(first.target(),new BodyController.Owner(Vec3d.ZERO,180,1.1),1,true,Mode.FOLLOW,false,tuning,open);
        assertEquals(first.target(),turned.target());
    }
    @Test void sweptMovementCannotTunnelThroughAPlayerEvenAtHighSpeed() {
        Box obstacle = new Box(-.3,0,2,.3,1.8,2.6);
        Vec3d start = new Vec3d(0,1,0), requested = new Vec3d(0,0,8);
        Vec3d step = BodyMotion.clip(BodyMotion.box(start),requested,List.of(obstacle));
        assertTrue(step.z > 1 && step.z < 2);
        assertFalse(BodyMotion.box(start.add(step)).intersects(obstacle));
    }
    @Test void anExistingOverlapCanEscapeButCannotCrossToTheOtherSide() {
        Box obstacle = new Box(-1,0,-1,1,2,1);
        Box body = BodyMotion.box(new Vec3d(.9,1,0));
        assertTrue(BodyMotion.clip(body,new Vec3d(.2,0,0),List.of(obstacle)).x > 0);
        assertEquals(Vec3d.ZERO,BodyMotion.clip(body,new Vec3d(-.2,0,0),List.of(obstacle)));
    }
    @Test void canRetreatSidewaysAroundMobBlockingDirectRoute() {
        Vec3d position = new Vec3d(2,1.1,3), target = new Vec3d(0,1.1,-3.5), velocity = Vec3d.ZERO;
        List<Box> living = List.of(new Box(-.3,0,-.3,.3,1.8,.3),new Box(.3,0,-1,1.5,2.5,1));
        for (int tick = 0; tick < 500 && position.distanceTo(target) > .35; tick++) {
            Vec3d start = position;
            Vec3d waypoint = BodyMotion.aroundFront(position,target,Vec3d.ZERO,0);
            Vec3d step = BodyMotion.steer(position,waypoint,.25,velocity,living,d -> BodyMotion.frontClear(start,d,Vec3d.ZERO,0));
            for (Box obstacle : living) {
                assertEquals(1,BodyMotion.contact(BodyMotion.box(position),step,obstacle),1e-6);
                assertFalse(BodyMotion.box(position.add(step)).intersects(obstacle));
            }
            velocity = step; position = position.add(step);
        }
        assertTrue(position.distanceTo(target) < .5,"blocked at "+position);
    }
    @Test void hazardInFlightPathIsAvoidedEvenWhenDestinationIsClear() {
        Vec3d position = new Vec3d(0,1,0), velocity = Vec3d.ZERO, target = new Vec3d(0,1,5);
        Box lava = new Box(-.5,0,2,.5,3,3);
        for (int tick = 0; tick < 500 && position.distanceTo(target) > .35; tick++) {
            Vec3d step = BodyMotion.steer(position,target,.2,velocity,List.of(lava),d -> true);
            assertFalse(BodyMotion.box(position.add(step)).intersects(lava));
            velocity = step; position = position.add(step);
        }
        assertTrue(position.distanceTo(target) < .5,"blocked at "+position);
    }
    @Test void unavailableSpaceStopsMovementWithoutInvalidCoordinates() {
        Vec3d step = BodyMotion.steer(Vec3d.ZERO,new Vec3d(3,1,0),.2,Vec3d.ZERO,List.of(),d -> false);
        assertEquals(Vec3d.ZERO,step);
        assertThrows(IllegalArgumentException.class,() -> tuning.set("speed",Double.NaN));
        assertThrows(IllegalArgumentException.class,() -> tuning.set("explore_min",4));
    }
    @Test void followCanKeepUpWithWalkingInsteadOfRepeatedlyFallingBehind() {
        BodyController controller = new BodyController(5);
        Vec3d position = new Vec3d(-2.12,1.1,2.12), velocity = Vec3d.ZERO;
        for (int tick = 0; tick < 400; tick++) {
            var walking = new BodyController.Owner(new Vec3d(0,0,tick*.215),0,1.1);
            var plan = controller.plan(position,walking,tick,true,Mode.FOLLOW,false,tuning,open);
            double speed = tuning.speedFor(plan.activity(),position.distanceTo(plan.target()),position.distanceTo(walking.feet()));
            Vec3d step = BodyMotion.steer(position,plan.target(),speed,velocity,List.of(),d -> true);
            velocity = step; position = position.add(step);
            assertTrue(position.distanceTo(walking.feet()) < 5,"falling behind at tick "+tick+": "+position);
        }
    }
}
