package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class DirectedMotionTest {
    @Test void waitingAndRestingAreDistinctExplicitChoices() {
        DirectedMotion motion = new DirectedMotion();
        assertEquals(Activity.HOLDING,motion.plan(Vec3d.ZERO).activity());
        motion.begin(UUID.randomUUID(),Action.REST,null);
        assertEquals(Activity.RESTING,motion.plan(Vec3d.ZERO).activity());
        motion.begin(UUID.randomUUID(),Action.WAIT,null);
        assertFalse(motion.resting()); assertEquals(Activity.HOLDING,motion.plan(Vec3d.ZERO).activity());
    }
    @Test void chosenDestinationIsReachedAndReportedOnce() {
        DirectedMotion motion = new DirectedMotion();
        Vec3d destination = new Vec3d(6,1,-3), position = Vec3d.ZERO, velocity = Vec3d.ZERO;
        motion.begin(UUID.randomUUID(),Action.MOVE_TO,destination);
        for (int i=0;i<600 && motion.moving();i++) {
            var plan = motion.plan(position);
            Vec3d step = BodyMotion.steer(position,plan.target(),.14,velocity,List.of(),d -> true);
            position = position.add(step); velocity = step;
        }
        assertTrue(position.distanceTo(destination) <= .35);
        assertTrue(motion.takeArrival()); assertFalse(motion.takeArrival());
        assertEquals(Activity.HOLDING,motion.plan(position).activity());
    }
    @Test void retryCannotChangeDestinationOrRestartCompletedMovement() {
        DirectedMotion motion = new DirectedMotion(); UUID id = UUID.randomUUID();
        motion.begin(id,Action.MOVE_TO,new Vec3d(2,0,0));
        motion.begin(id,Action.MOVE_TO,new Vec3d(9,0,0));
        assertEquals(new Vec3d(2,0,0),motion.plan(Vec3d.ZERO).target());
        motion.plan(new Vec3d(2,0,0));
        motion.begin(id,Action.MOVE_TO,new Vec3d(9,0,0));
        assertFalse(motion.moving());
    }
    @Test void lifeOrConnectionResetClearsRestAndPendingDestination() {
        DirectedMotion motion = new DirectedMotion();
        motion.begin(UUID.randomUUID(),Action.REST,null); motion.clear();
        assertFalse(motion.resting());
        motion.begin(UUID.randomUUID(),Action.MOVE_TO,new Vec3d(4,0,0)); motion.clear();
        assertFalse(motion.moving()); assertFalse(motion.takeArrival());
    }
}
