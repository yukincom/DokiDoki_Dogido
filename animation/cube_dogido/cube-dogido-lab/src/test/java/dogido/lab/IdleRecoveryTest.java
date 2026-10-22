package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Predicate;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class IdleRecoveryTest {
    private static final Box GROUND = new Box(-10,-10,-10,10,0,10);
    private static final Vec3d BURIED = new Vec3d(0,-.349,0);

    @Test void onlyEnabledDirectWaitingOrRestingCanRecover() {
        for (Mode mode : Mode.values()) for (Activity activity : Activity.values()) {
            ControlLease control = new ControlLease(); control.local(mode,false);
            assertEquals(mode == Mode.DIRECT && (activity == Activity.HOLDING || activity == Activity.RESTING),
                IdleRecovery.eligible(control,true,activity));
            control.pause();
            assertFalse(IdleRecovery.eligible(control,true,activity));
        }
    }

    @Test void reloadedUncommandedBodyRecoversWithoutEnablingRemoteActions() {
        ControlLease control = new ControlLease();
        Context context = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        control.reset(context); // readCustomData/newEpochと同じ、owner保存済み・指示未受領の初期状態。
        assertFalse(control.enabled()); assertEquals(-1,control.revision()); assertFalse(control.expired());
        for (Activity activity : Activity.values())
            assertEquals(activity == Activity.HOLDING || activity == Activity.RESTING,IdleRecovery.eligible(control,false,activity));
        simulateRecoveryAndHover(BURIED,List.of(),new DirectedMotion(),control,false);
        assertFalse(control.enabled());
        assertEquals("control_inactive",control.request(new ActionRequest(context,0,UUID.randomUUID(),100,20,Action.MOVE_TO),100).reason());
    }

    @Test void explicitRemoteStopExpiredLeaseRetreatAndLocalHoldRemainExcluded() {
        ControlLease control = new ControlLease();
        Context context = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        control.reset(context);
        assertEquals(Outcome.ACCEPTED,control.apply(new StateRequest(context,0,0,100,20,false,Mode.DIRECT,false),100).outcome());
        assertFalse(IdleRecovery.eligible(control,false,Activity.HOLDING));
        assertEquals(Outcome.ACCEPTED,control.apply(new StateRequest(context,1,0,100,20,true,Mode.DIRECT,false),100).outcome());
        assertTrue(IdleRecovery.eligible(control,false,Activity.HOLDING));
        control.expire(120);
        assertTrue(control.expired()); assertFalse(IdleRecovery.eligible(control,false,Activity.HOLDING));
        control.local(Mode.DIRECT,true);
        assertFalse(IdleRecovery.eligible(control,true,Activity.HOLDING));
        control.local(Mode.HOLD,false);
        assertFalse(IdleRecovery.eligible(control,true,Activity.HOLDING));
    }

    @Test void normalIdleAndIneligibleBurialDoNotProbeWorldOrPickTargets() {
        IdleRecovery recovery = new IdleRecovery();
        Predicate<Box> unexpectedProbe = box -> { fail("normal idle must reuse the existing wall sample only"); return false; };
        for (int tick = 0; tick < 100; tick++) {
            recovery.update(Vec3d.ZERO,true,false,tick,unexpectedProbe);
            recovery.update(BURIED,false,true,tick,unexpectedProbe);
            assertNull(recovery.target(BURIED));
        }
    }

    @Test void waitAndRestEscapeGroundAndStayClearDuringSubsequentHover() {
        for (Action action : new Action[]{Action.WAIT,Action.REST}) {
            DirectedMotion directed = new DirectedMotion();
            directed.begin(UUID.randomUUID(),action,null);
            simulateRecoveryAndHover(BURIED,List.of(),directed);
        }
        // bind直後など、まだ行動を指定していないHOLDINGも同じ復帰を使う。
        simulateRecoveryAndHover(BURIED,List.of(),new DirectedMotion());
    }

    @Test void pigOverlapUsesExistingCollisionEscapeAndRecoversBesidePig() {
        Box pig = new Box(-.5,0,-.5,.5,.9,.5).expand(BodyTuning.LIVING_MARGIN);
        simulateRecoveryAndHover(new Vec3d(0,-.5,0),List.of(pig),new DirectedMotion());
    }

    private static void simulateRecoveryAndHover(Vec3d position, List<Box> living, DirectedMotion directed) {
        ControlLease control = new ControlLease(); control.local(Mode.DIRECT,false);
        simulateRecoveryAndHover(position,living,directed,control,true);
    }
    private static void simulateRecoveryAndHover(Vec3d position, List<Box> living, DirectedMotion directed,
            ControlLease control, boolean localControl) {
        IdleRecovery recovery = new IdleRecovery();
        Predicate<Box> clear = box -> !box.intersects(GROUND) && living.stream().noneMatch(box::intersects);
        Vec3d motion = Vec3d.ZERO;
        IdleMotion.FloatCycle wave = new IdleMotion.FloatCycle();
        boolean started = false, completed = false;
        int hoverTicks = 0;
        for (int tick = 0; tick < 600 && hoverTicks < 240; tick++) {
            var plan = directed.plan(position);
            recovery.update(position,IdleRecovery.eligible(control,localControl,plan.activity()),
                BodyMotion.box(position).intersects(GROUND),tick,clear);
            Vec3d destination = recovery.target(position);
            if (destination != null) started = true;
            if (started && destination == null) completed = true;
            double beforeOverlap = BodyMotion.overlapDepth(BodyMotion.box(position),living);
            Vec3d target = destination == null ? position : destination;
            motion = BodyMotion.steer(position,target,.14,motion,living,delta -> true);
            Vec3d step = motion;
            if (destination == null) {
                Vec3d start = position;
                var floated = BodyMotion.withHover(position,motion,wave.nextStep(),living,
                    delta -> clear.test(BodyMotion.box(start.add(delta))));
                step = floated.movement();
                if (floated.applied()) wave.advance();
            }
            position = position.add(step);
            double afterOverlap = BodyMotion.overlapDepth(BodyMotion.box(position),living);
            assertTrue(afterOverlap <= beforeOverlap + 1e-7,"recovery must preserve living collision constraints");
            if (completed) {
                assertNull(destination,"hover must not re-start recovery");
                assertFalse(BodyMotion.box(position).intersects(GROUND));
                assertEquals(0,afterOverlap,1e-7);
                hoverTicks++;
            }
        }
        assertTrue(started); assertTrue(completed); assertEquals(240,hoverTicks);
        assertEquals(directed.resting() ? Activity.RESTING : Activity.HOLDING,directed.plan(position).activity());
    }

    @Test void holdsDestinationAfterLeavingSolidUntilHoverClearanceIsReached() {
        IdleRecovery recovery = new IdleRecovery();
        Predicate<Box> clear = box -> !box.intersects(GROUND);
        Vec3d buried = new Vec3d(0,2*IdleMotion.HOVER_AMPLITUDE+.03-.5,0);
        recovery.update(buried,true,true,0,clear);
        Vec3d destination = recovery.target(buried);
        assertNotNull(destination);
        Vec3d barelyClear = new Vec3d(0,.01,0);
        recovery.update(barelyClear,true,false,1,clear);
        assertEquals(destination,recovery.target(barelyClear));
        Vec3d nearDestination = destination.add(0,-.02,0);
        recovery.update(nearDestination,true,false,2,clear);
        assertEquals(destination,recovery.target(nearDestination),"arrival distance alone must not lose hover clearance");
        recovery.update(destination,true,false,3,clear);
        assertNull(recovery.target(destination));
    }

    @Test void invalidDestinationIsDroppedAndWaitsBeforeTryingAnother() {
        IdleRecovery recovery = new IdleRecovery();
        recovery.update(BURIED,true,true,0,box -> !box.intersects(GROUND));
        assertNotNull(recovery.target(BURIED));
        recovery.update(BURIED,true,true,1,box -> false);
        assertNull(recovery.target(BURIED));
        AtomicInteger probes = new AtomicInteger();
        for (int tick = 2; tick < 21; tick++) recovery.update(BURIED,true,true,tick,box -> { probes.incrementAndGet(); return true; });
        assertEquals(0,probes.get());
        recovery.update(BURIED,true,true,21,box -> { probes.incrementAndGet(); return true; });
        assertEquals(1,probes.get()); assertNotNull(recovery.target(BURIED));
    }

    @Test void blockedOrUnloadedAreaHasBoundedLocalSearchAndNoForcedTarget() {
        IdleRecovery recovery = new IdleRecovery();
        AtomicInteger probes = new AtomicInteger();
        Predicate<Box> unavailable = box -> {
            probes.incrementAndGet();
            Vec3d candidate = new Vec3d((box.minX+box.maxX)/2,(box.minY+box.maxY-BodyTuning.BODY_SIZE)/2,(box.minZ+box.maxZ)/2);
            assertTrue(candidate.distanceTo(BURIED) <= IdleRecovery.SEARCH_RADIUS + 1e-9);
            return false;
        };
        recovery.update(BURIED,true,true,0,unavailable);
        assertEquals(156,probes.get()); assertNull(recovery.target(BURIED));
        for (int tick = 1; tick < 20; tick++) recovery.update(BURIED,true,true,tick,unavailable);
        assertEquals(156,probes.get());
        recovery.update(BURIED,true,true,20,unavailable);
        assertEquals(312,probes.get()); assertNull(recovery.target(BURIED));
    }

    @Test void stateChangesAndExternalRelocationDiscardOldDestinations() {
        IdleRecovery recovery = new IdleRecovery();
        recovery.update(BURIED,true,true,0,box -> true);
        recovery.update(BURIED,false,true,1,box -> { fail("ineligible state must not probe"); return true; });
        assertNull(recovery.target(BURIED));
        recovery.update(BURIED,true,true,2,box -> true);
        recovery.clear(); // 新しい行動またはowner/epochの変更。
        assertNull(recovery.target(BURIED));
        recovery.update(BURIED,true,true,3,box -> true);
        assertNull(recovery.target(BURIED.add(20,0,0)));
    }
}
