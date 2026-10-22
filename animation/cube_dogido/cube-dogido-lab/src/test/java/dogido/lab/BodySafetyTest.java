package dogido.lab;

import dogido.lab.control.*;
import java.util.List;
import net.minecraft.entity.mob.GhastEntity;
import net.minecraft.entity.mob.Monster;
import net.minecraft.entity.mob.PhantomEntity;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodySafetyTest {
    private static Vec3d feet(double x, double y, double z) { return new Vec3d(x,y-BodyTuning.BODY_SIZE/2,z); }
    private static BodySafety corridor(Vec3d a, Vec3d b) {
        return new BodySafety(List.of(new BodySafety.Corridor(a,b,.45)));
    }
    @Test void diagonalCorridorUsesFiniteThreeDimensionalSegmentAndWholeBody() {
        BodySafety safety = corridor(new Vec3d(0,1,0),new Vec3d(10,1,10));
        assertFalse(safety.safeTarget(feet(5,1,5)));
        assertFalse(safety.safeTarget(feet(5,1,6)),"body corners also have to clear the corridor");
        assertTrue(safety.safeTarget(feet(0,1,9)),"a diagonal corridor is not its enclosing rectangle");
        assertTrue(safety.safeTarget(feet(12,1,12)),"the finite segment does not extend behind the enemy");
        assertTrue(safety.safeTarget(feet(5,5,5)),"horizontal projection must not forbid a safe vertical separation");
        assertFalse(safety.allowsStep(feet(3,1,7),new Vec3d(4,0,-4)),"safe endpoints cannot cross the middle");
        assertTrue(safety.allowsStep(feet(3,5,7),new Vec3d(4,0,-4)));
    }
    @Test void overlapEscapeCannotEnterAnotherRegionOrCrossTheExistingRegion() {
        BodySafety safety = new BodySafety(List.of(
            new BodySafety.Corridor(new Vec3d(-10,1,0),new Vec3d(10,1,0),.45),
            new BodySafety.Corridor(new Vec3d(-10,1,3),new Vec3d(10,1,3),.45)));
        Vec3d start = feet(0,1,.2);
        assertTrue(safety.allowsStep(start,new Vec3d(0,0,.3)));
        assertFalse(safety.allowsStep(start,new Vec3d(0,0,-1)));
        assertFalse(safety.allowsStep(start,new Vec3d(0,0,4)));
    }
    @Test void escapingOneOverlapCanRemainLevelWithAnotherButUnrelatedImprovementDoesNotCount() {
        BodySafety safety = new BodySafety(List.of(
            new BodySafety.Corridor(new Vec3d(-10,1,0),new Vec3d(10,1,0),.45),
            new BodySafety.Corridor(new Vec3d(0,1,-10),new Vec3d(0,1,10),.45)));
        assertTrue(safety.allowsStep(feet(.2,1,.2),new Vec3d(.3,0,0)),"one overlap decreases while another stays unchanged");
        BodySafety unrelated = new BodySafety(List.of(
            new BodySafety.Corridor(new Vec3d(-10,1,0),new Vec3d(10,1,0),.45),
            new BodySafety.Corridor(new Vec3d(-10,1,8),new Vec3d(-10,1,10),.45)));
        assertFalse(unrelated.allowsStep(feet(0,1,.2),new Vec3d(.3,0,0)));
    }
    @Test void aMovingEnemyPredictionIncludesHorizontalApproachAndKeepsStationaryBodyDanger() {
        Box enemy = new Box(5,3,-.3,5.6,4.8,.3);
        Vec3d owner = new Vec3d(12,1,12);
        BodySafety stationary = BodySafety.capture(owner,List.of(new BodySafety.Enemy(enemy,Vec3d.ZERO)));
        BodySafety approaching = BodySafety.capture(owner,List.of(new BodySafety.Enemy(enemy,new Vec3d(-.5,0,0))));
        assertTrue(stationary.safeTarget(feet(1,3.9,0)));
        assertFalse(approaching.safeTarget(feet(1,3.9,0)));
        assertFalse(stationary.safeTarget(feet(5.3,3.9,0)));
    }
    @Test void descendingEnemyForbidsAnUpwardFlightEvenWhenBothEndpointsAreOutsideItsBody() {
        BodySafety safety = BodySafety.capture(new Vec3d(12,1,12),List.of(
            new BodySafety.Enemy(new Box(-.3,7,-.3,.3,8.8,.3),new Vec3d(0,-.5,0))));
        Vec3d start = feet(0,1,0);
        assertFalse(safety.allowsStep(start,new Vec3d(0,7,0)));
        assertTrue(safety.allowsStep(start,new Vec3d(4,0,0)));
    }
    @Test void safeWarpLandingSharesTheExactTargetCheck() {
        BodySafety safety = corridor(new Vec3d(0,1,0),new Vec3d(12,1,12));
        assertFalse(safety.safeTarget(feet(3,1,3)));
        assertTrue(safety.safeTarget(feet(-3,1,3)));
    }
    @Test void flyingHostilesAreIncludedInTheMonsterContract() {
        assertTrue(Monster.class.isAssignableFrom(PhantomEntity.class));
        assertTrue(Monster.class.isAssignableFrom(GhastEntity.class));
    }
}
