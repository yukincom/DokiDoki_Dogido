package dogido.fabric.body;

import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static dogido.fabric.body.BodyInspectProtocol.Point;
import static dogido.fabric.body.BodyApproachGeometry.*;

/** 大型対象・天井・狭所でも対象座標へ重ならず、実行可能な側方候補だけを返す。 */
class BodyApproachGeometryTest {
    static final Bounds BODY=new Bounds(-.4,0,-.4,.4,.8,.4);
    @Test void largeTargetUsesItsActualBoundsAndNearestSide() {
        Bounds giant=new Bounds(4,60,-5,14,70,5);
        Point source=new Point(1,65,0);
        Point chosen=choose(source,source,1.62,16,giant,BODY,false,box -> true);
        assertNotNull(chosen); assertTrue(chosen.x() < giant.minX());
        assertFalse(BODY.at(chosen).intersects(giant.expand(CLEARANCE/2)));
        assertEquals(giant.minX()-CLEARANCE,BODY.at(chosen).maxX(),1e-8);
        assertEquals(0,chosen.z(),1e-8);
    }
    @Test void ceilingTargetChoosesDiagonalBelowEvenIfSideIsCloser() {
        Bounds ceilingTarget=new Bounds(4,70,0,5,71,1);
        Point source=new Point(2,70,0);
        Point chosen=choose(source,source,1.62,16,ceilingTarget,BODY,true,box -> box.maxY() < 71.2);
        assertNotNull(chosen); assertTrue(BODY.at(chosen).maxY() < ceilingTarget.minY());
        assertFalse(BODY.at(chosen).intersects(ceilingTarget));
        assertTrue(chosen.x() < ceilingTarget.minX() || chosen.x() > ceilingTarget.maxX()
            || chosen.z() < ceilingTarget.minZ() || chosen.z() > ceilingTarget.maxZ());
    }
    @Test void blockedNearestSideUsesAnotherFreeSideAndNeverEntersTarget() {
        Bounds target=new Bounds(4,64,0,5,65,1);
        Point source=new Point(0,64,0);
        Point chosen=choose(source,source,1.62,16,target,BODY,false,box -> box.minZ() > target.maxZ());
        assertNotNull(chosen); assertTrue(BODY.at(chosen).minZ() > target.maxZ());
        assertFalse(BODY.at(chosen).intersects(target));
    }
    @Test void blockedCeilingSidesAndBelowReturnNoDestination() {
        Bounds target=new Bounds(4,64,0,5,65,1);
        AtomicInteger inspected=new AtomicInteger();
        Point chosen=choose(new Point(0,64,0),new Point(0,64,0),1.62,16,target,BODY,true,box -> { inspected.incrementAndGet(); return false; });
        assertNull(chosen); assertEquals(16,inspected.get());
    }
    @Test void ceilingTargetNeverUsesAboveWhenSideAndBelowAreBlocked() {
        Bounds target=new Bounds(4,70,0,5,71,1); Point source=new Point(0,70,0);
        assertNull(choose(source,source,1.62,16,target,BODY,true,box -> box.minY() > target.maxY()));
    }
    @Test void bodySizeAndOwnerRangeAreBothRespected() {
        Bounds target=new Bounds(4,64,0,5,65,1),wideBody=new Bounds(-2,-.5,-3,2,4,3);
        Point source=new Point(0,64,0);
        Point chosen=choose(source,source,1.62,16,target,wideBody,false,box -> true);
        assertNotNull(chosen); assertFalse(wideBody.at(chosen).intersects(target.expand(CLEARANCE/2)));
        Bounds tooFar=new Bounds(20,64,0,21,65,1);
        assertNull(choose(source,source,1.62,16,tooFar,BODY,false,box -> true));
    }
    @Test void nullResultWhenOnlyOutOfRangeLocationsWouldBeFree() {
        Bounds target=new Bounds(15,64,0,16,65,1); Point owner=new Point(0,64,0);
        assertNull(choose(owner,owner,1.62,16,target,BODY,false,box -> box.minX() > target.maxX()));
    }
    @Test void currentEightBlockRangeKeepsNearSideAndRejectsOnlyFarSide() {
        Bounds target=new Bounds(7,64,-.5,8,65,.5); Point owner=new Point(0,64,0);
        Point chosen=choose(owner,owner,1.62,8,target,BODY,false,box -> true);
        assertNotNull(chosen); assertTrue(chosen.distance(owner) <= 8);
        assertTrue(chosen.x() < target.minX());
        assertNull(choose(owner,owner,1.62,8,target,BODY,false,box -> box.minX() > target.maxX()));
        assertNotNull(choose(owner,owner,1.62,12,target,BODY,false,box -> box.minX() > target.maxX()));
        assertNull(choose(owner,owner,1.62,Double.NaN,target,BODY,false,box -> true));
    }
    @Test void highTargetUsesLowerSideWithinFollowHeightAndCannotSelectTheCancelledUpperSide() {
        Bounds target=new Bounds(3,69.2,0,4,70.2,1),body=new Bounds(-.3125,0,-.3125,.3125,.625,.3125);
        Point owner=new Point(0,64,0),source=new Point(3,70.2,0);
        Point goal=choose(source,owner,1.62,8,target,body,false,box -> true);
        assertNotNull(goal); assertTrue(body.at(goal).maxY() < target.minY());
        double followY=owner.y()+dogido.lab.api.BodyProtocol.followBodyHeight(1.62,.625);
        assertTrue(Math.abs(goal.y()-followY)+.4+2*.18 < 4);
        assertNull(choose(source,owner,1.62,8,target,body,false,box -> box.minY() >= target.minY()));
    }
}
