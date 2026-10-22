package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** owner接近の実測更新と安全な到着を、モデル・Minecraft描画から独立した幾何で検査する。 */
class BodyOwnerApproachTest {
    private final BodyTuning tuning = new BodyTuning();
    private final BodyController.Owner owner = new BodyController.Owner(Vec3d.ZERO,0,1.1);
    private final BodyController.TargetSpace open = (p,w)->true;
    private Vec3d destination(Vec3d body, MovePurpose purpose) {
        return BodyController.ownerDestination(owner,body,purpose,open,BodySafety.EMPTY);
    }
    private BodyController.Plan plan(BodyController c, Vec3d body, BodyController.Owner player, long tick) {
        return c.plan(body,player,tick,true,Mode.FOLLOW,false,tuning,open);
    }
    @Test void faceUsesDiagonalFrontAndNearUsesSideOrBackAndNeitherMakesAnUnsafeTarget() {
        Vec3d body = new Vec3d(10,1,0);
        Vec3d face=destination(body,MovePurpose.OWNER_FACE), near=destination(body,MovePurpose.OWNER_NEAR);
        assertTrue(face.z>1); assertTrue(Math.abs(face.x)>1);
        assertEquals(1.75,face.horizontalLength(),1e-8);
        assertFalse(BodyMotion.nearFront(near,owner.feet(),owner.yaw()));
        var working=new BodyController.Owner(owner.feet(),owner.yaw(),owner.bodyHeight(),true);
        assertNull(BodyController.ownerDestination(working,body,MovePurpose.OWNER_FACE,open,BodySafety.EMPTY));
        for (MovePurpose purpose : new MovePurpose[]{MovePurpose.OWNER_NEAR,MovePurpose.OWNER_FACE}) {
            assertNull(BodyController.ownerDestination(owner,body,purpose,(p,w)->false,BodySafety.EMPTY));
            assertNull(BodyController.ownerDestination(owner,body,purpose,open,
                new BodySafety(List.of(new BodySafety.Corridor(Vec3d.ZERO,Vec3d.ZERO,100)))));
        }
    }
    @Test void ownerTranslationAndHeightUpdateTheTargetButCameraRotationAloneDoesNotOrbit() {
        for (MovePurpose purpose : new MovePurpose[]{MovePurpose.OWNER_NEAR,MovePurpose.OWNER_FACE}) {
            BodyController c=new BodyController(1); UUID id=UUID.randomUUID();
            Vec3d body=new Vec3d(10,1,0), first=destination(body,purpose);
            c.inspect(id,first,purpose,owner,600);
            assertEquals(Activity.MOVING,plan(c,body,owner,0).activity());
            var turned=new BodyController.Owner(owner.feet(),180,owner.bodyHeight());
            assertEquals(first,plan(c,body,turned,1).target());
            var moved=new BodyController.Owner(new Vec3d(1,1,1),180,.7);
            Vec3d shifted=first.add(1,.6,1);
            assertTrue(shifted.distanceTo(plan(c,body,moved,2).target())<1e-9);
            assertEquals(Activity.MOVING,plan(c,first,moved,3).activity());
            assertNull(c.takeInspectionResult());
            assertEquals(Activity.HOLDING,plan(c,shifted,moved,4).activity());
            var result=c.takeInspectionResult(); assertEquals(id,result.id()); assertEquals(Outcome.SUCCEEDED,result.outcome());
            assertNull(c.takeInspectionResult());
            assertEquals(purpose==MovePurpose.OWNER_FACE,c.lookingAtOwner(4));
            plan(c,shifted,moved,104); assertFalse(c.lookingAtOwner(104));
        }
    }
    @Test void faceWorkStartCancelsAndNearReplansAwayFromNewWorkFront() {
        Vec3d body=new Vec3d(10,1,0);
        BodyController face=new BodyController(2);
        face.inspect(UUID.randomUUID(),destination(body,MovePurpose.OWNER_FACE),MovePurpose.OWNER_FACE,owner,600);
        plan(face,body,owner,0);
        var working=new BodyController.Owner(owner.feet(),0,1.1,true);
        plan(face,body,working,1);
        assertEquals("owner_working",face.takeInspectionResult().reason()); assertFalse(face.lookingAtOwner(1));
        BodyController near=new BodyController(3);
        Vec3d first=destination(body,MovePurpose.OWNER_NEAR);
        near.inspect(UUID.randomUUID(),first,MovePurpose.OWNER_NEAR,owner,600);
        float facingFirst=(float)Math.toDegrees(Math.atan2(-first.x,first.z));
        var mining=new BodyController.Owner(owner.feet(),facingFirst,1.1,true);
        Vec3d revised=plan(near,body,mining,0).target();
        assertFalse(BodyMotion.nearFront(revised,owner.feet(),facingFirst));
        assertNotEquals(first,revised); assertNull(near.takeInspectionResult());
    }
    @Test void emergenciesAndUnavailableTargetsInterruptWithoutSuccessAndDoNotExtendDeadline() {
        Vec3d body=new Vec3d(10,1,0);
        for (String change : List.of("distance","height","danger","call","owner","disabled","mode","work","blocked","expired")) {
            BodyController c=new BodyController(4); UUID id=UUID.randomUUID();
            Vec3d goal=destination(body,MovePurpose.OWNER_FACE);
            c.inspect(id,goal,MovePurpose.OWNER_FACE,owner,10);
            plan(c,body,owner,0);
            c.inspect(id,goal,MovePurpose.OWNER_FACE,owner,500);
            if (change.equals("call")) c.call(UUID.randomUUID(),owner,1);
            var safety=change.equals("danger") ? new BodySafety(List.of(new BodySafety.Corridor(body,body,1))) : BodySafety.EMPTY;
            var player=change.equals("owner") ? null : change.equals("work") ? new BodyController.Owner(Vec3d.ZERO,0,1.1,true) : owner;
            c.plan(change.equals("distance") ? new Vec3d(13,1,0) : change.equals("height") ? new Vec3d(4,7,0) : body,
                player,change.equals("expired") ? 10 : 1,!change.equals("disabled"),change.equals("mode") ? Mode.DIRECT : Mode.FOLLOW,
                false,tuning,change.equals("blocked") ? (p,w)->false : open,safety);
            var result=c.takeInspectionResult(); assertNotNull(result,change); assertEquals(id,result.id());
            assertEquals(change.equals("expired") ? Outcome.EXPIRED : Outcome.INTERRUPTED,result.outcome(),change);
            assertFalse(c.approachingOwner()); assertFalse(c.lookingAtOwner(1));
        }
    }
    @Test void inspectionRetainsNormalRadiusEvenThoughRoutineDistanceNoLongerStartsFollowing() {
        BodyController c=new BodyController(5);
        Vec3d body=new Vec3d(9,1,0);
        assertEquals(Activity.HOLDING,plan(c,body,owner,0).activity());
        c.inspect(UUID.randomUUID(),new Vec3d(7,1,0),600);
        plan(c,body,owner,1);
        assertEquals("inspection_outside_follow_distance",c.takeInspectionResult().reason());
        c.inspect(UUID.randomUUID(),new Vec3d(7,1,0),600);
        plan(c,new Vec3d(2,1,0),new BodyController.Owner(new Vec3d(-2,0,0),0,1.1),2);
        assertEquals("inspection_outside_follow_distance",c.takeInspectionResult().reason());
        assertFalse(c.following());
    }
    @Test void anOverlappingActualPositionIsNotArrivalAndFaceDwellStopsForPriority() {
        Vec3d body=new Vec3d(10,1,0), goal=destination(body,MovePurpose.OWNER_FACE);
        BodyController c=new BodyController(6);
        c.inspect(UUID.randomUUID(),goal,MovePurpose.OWNER_FACE,owner,600);
        Vec3d notClear=goal.add(.1,0,0);
        c.plan(notClear,owner,0,true,Mode.FOLLOW,false,tuning,(p,w)->!p.equals(notClear));
        assertNull(c.takeInspectionResult());
        plan(c,goal,owner,1); assertTrue(c.lookingAtOwner(1));
        c.call(UUID.randomUUID(),owner,2); assertFalse(c.lookingAtOwner(2));
        assertFalse(c.inspectionDwelling(2));
    }
    @Test void actualNearArrivalAllowsOnlyTheFaceContinuationDuringDwell() {
        for (MovePurpose purpose : MovePurpose.values()) {
            BodyController c=new BodyController(7);
            Vec3d body=new Vec3d(10,1,0);
            Vec3d goal=purpose==MovePurpose.INSPECT ? new Vec3d(2,1,0) : destination(body,purpose);
            c.inspect(UUID.randomUUID(),goal,purpose,purpose==MovePurpose.INSPECT ? null : owner,600);
            plan(c,goal,owner,1);
            assertEquals(Outcome.SUCCEEDED,c.takeInspectionResult().outcome());
            assertEquals("inspection_dwell",c.inspectionPauseReason(MovePurpose.INSPECT,2));
            assertEquals("inspection_dwell",c.inspectionPauseReason(MovePurpose.OWNER_NEAR,2));
            assertEquals(purpose==MovePurpose.OWNER_NEAR ? null : "inspection_dwell",c.inspectionPauseReason(MovePurpose.OWNER_FACE,2));
            if (purpose==MovePurpose.OWNER_NEAR) {
                // 普通の歩行中も帰還直後の二択は受け付け、顔へ着いたらもう一度の到着判断を作らない。
                var walking=new BodyController.Owner(new Vec3d(.2,0,0),0,1.1);
                plan(c,goal,walking,2);
                assertNull(c.inspectionPauseReason(MovePurpose.OWNER_FACE,2));
                Vec3d face=BodyController.ownerDestination(walking,goal,MovePurpose.OWNER_FACE,open,BodySafety.EMPTY);
                c.inspect(UUID.randomUUID(),face,MovePurpose.OWNER_FACE,walking,600);
                plan(c,face,walking,3);
                assertEquals("inspection_dwell",c.inspectionPauseReason(MovePurpose.OWNER_FACE,3));
            }
        }
    }
    @Test void onlyActualFollowArrivalCanContinueThroughSettling() {
        BodyController c=new BodyController(8);
        Vec3d body=new Vec3d(2,1.1,0);
        plan(c,body,owner,0);
        assertEquals("follow_settling",c.inspectionPauseReason(MovePurpose.OWNER_FACE,0));
        var moving=plan(c,new Vec3d(14,1.1,0),owner,1);
        assertEquals(Activity.FOLLOWING,moving.activity());
        assertEquals("follow_settling",plan(c,moving.target(),owner,2).reason());
        assertNull(c.inspectionPauseReason(MovePurpose.OWNER_FACE,2));
        assertEquals("follow_settling",c.inspectionPauseReason(MovePurpose.INSPECT,2));
        assertNull(c.inspectionPauseReason(MovePurpose.OWNER_FACE,61));
        // 高速移動でsettlingだけが延びても、前の帰還許可の期限は延ばさない。
        plan(c,moving.target(),new BodyController.Owner(new Vec3d(1,0,0),0,1.1),61);
        plan(c,moving.target(),new BodyController.Owner(new Vec3d(2,0,0),0,1.1),62);
        assertEquals("follow_settling",c.inspectionPauseReason(MovePurpose.OWNER_FACE,62));
    }
    @Test void priorityTransitionsClearTheReturnContinuation() {
        for (String change : List.of("call","retreat","danger","mode","disabled","owner","reset")) {
            BodyController c=new BodyController(9); Vec3d goal=destination(new Vec3d(10,1,0),MovePurpose.OWNER_NEAR);
            c.inspect(UUID.randomUUID(),goal,MovePurpose.OWNER_NEAR,owner,600);
            plan(c,goal,owner,1); assertNull(c.inspectionPauseReason(MovePurpose.OWNER_FACE,1));
            if (change.equals("call")) c.call(UUID.randomUUID(),owner,2);
            if (change.equals("reset")) c.reset();
            var safety=change.equals("danger") ? new BodySafety(List.of(new BodySafety.Corridor(goal,goal,1))) : BodySafety.EMPTY;
            c.plan(goal,change.equals("owner") ? null : owner,2,!change.equals("disabled"),
                change.equals("mode") ? Mode.HOLD : Mode.FOLLOW,change.equals("retreat"),tuning,open,safety);
            // 初回待機をもう一度作り、残った帰還許可でその間を飛び越えないことを確認する。
            c.cancelCall("fixture_finished");
            plan(c,goal,owner,3);
            assertEquals("follow_settling",c.inspectionPauseReason(MovePurpose.OWNER_FACE,3),change);
        }
    }
}
