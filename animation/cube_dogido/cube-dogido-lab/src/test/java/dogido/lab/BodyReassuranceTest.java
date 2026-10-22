package dogido.lab;

import dogido.lab.control.BodyReassurance;
import dogido.lab.api.perception.BodyVisionGeometry;
import dogido.lab.api.perception.BodyVisibility;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 安心度は実測の連続時間で変わり、再送・読取・命令受付から報酬を作らないことを検査する。 */
class BodyReassuranceTest {
    private final Vec3d body = new Vec3d(1.75,1.1,0), owner = Vec3d.ZERO;

    @Test void visibleNearOwnerRecoversOnlyToSeventyAndFaceCanReachOneHundred() {
        BodyReassurance state = new BodyReassurance();
        assertEquals(50,state.value());
        for (int tick=0;tick<1000;tick++) state.step(tick,body,owner,true,false,false,true);
        assertEquals(70,state.value(),1e-8);
        for (int tick=1000;tick<1500;tick++) state.step(tick,body,owner,true,true,false,true);
        assertEquals(100,state.value(),1e-8);
        for (int tick=1500;tick<2000;tick++) state.step(tick,body,owner,true,false,false,true);
        assertEquals(100,state.value(),1e-8,"近くにいるだけで既に高い安心度を70へ引き下げない");
        state.reset(); assertEquals(50,state.value());
    }
    @Test void duplicateOldAndMissingTicksCannotInventRecoveryAndReadsArePure() {
        BodyReassurance state = new BodyReassurance();
        state.step(0,body,owner,true,true,false,true);
        state.step(1,body,owner,true,true,false,true);
        double once=state.value();
        for (int i=0;i<50;i++) {
            state.step(1,body,owner,true,true,false,true);
            state.step(0,body,owner,true,true,false,true);
            assertEquals(once,state.reading(2,true,true,false).value());
        }
        state.step(10000,body,owner,true,true,false,true);
        assertEquals(once,state.value());
        state.step(10001,body,owner,true,true,false,true);
        assertEquals(once+.2,state.value(),1e-8);
        assertFalse(state.reading(2,false,true,false).faceVisible());
    }
    @Test void workingOrMovingOrUnseenFacesDoNotEarnTheFullRecovery() {
        for (String condition : new String[]{"working","owner_moving","body_moving","unseen","unsafe"}) {
            BodyReassurance state = new BodyReassurance();
            for (int tick=0;tick<1000;tick++) {
                Vec3d translation = condition.equals("owner_moving") ? new Vec3d(tick*.1,0,0) : Vec3d.ZERO;
                Vec3d at = body.add(translation).add(condition.equals("body_moving") ? new Vec3d((tick%2)*.1,0,0) : Vec3d.ZERO);
                state.step(tick,at,owner.add(translation),!condition.equals("unseen"),true,
                    condition.equals("working"),!condition.equals("unsafe"));
            }
            assertTrue(state.value() <= 70,condition+": "+state.value());
            if (condition.equals("unseen") || condition.equals("unsafe")) assertTrue(state.value() < 50,condition);
        }
    }
    @Test void distanceAndSustainedUnseenTimeDecayWithinBounds() {
        BodyReassurance near = new BodyReassurance(), far = new BodyReassurance();
        for (int tick=0;tick<=40;tick++) near.step(tick,body,owner,false,false,false,true);
        assertEquals(50,near.value());
        near.step(41,body,owner,false,false,false,true); assertTrue(near.value()<50);
        for (int tick=0;tick<2000;tick++) far.step(tick,new Vec3d(15,1,0),owner,false,false,false,true);
        assertEquals(0,far.value());
        for (int tick=2000;tick<3000;tick++) far.step(tick,body,null,false,false,false,false);
        assertEquals(0,far.value());
        near.step(42,new Vec3d(Double.NaN,0,0),owner,true,true,false,true);
        assertTrue(Double.isFinite(near.value()));
    }
    @Test void faceNeedsBothFacingDirectionsAndAnActualUnblockedRay() {
        Vec3d eye = new Vec3d(1,1.6,1), face = new Vec3d(0,1.6,0);
        assertTrue(BodyVisionGeometry.ownerFaceVisible(1,eye,135,0,face,0,0,p->BodyVisibility.Trace.CLEAR));
        assertFalse(BodyVisionGeometry.ownerFaceVisible(1,eye,135,0,face,180,0,p->BodyVisibility.Trace.CLEAR));
        assertFalse(BodyVisionGeometry.ownerFaceVisible(1,eye,-45,0,face,0,0,p->BodyVisibility.Trace.CLEAR));
        assertFalse(BodyVisionGeometry.ownerFaceVisible(1,eye,135,0,face,0,0,p->BodyVisibility.Trace.BLOCKED));
        assertFalse(BodyVisionGeometry.ownerFaceVisible(1,eye,135,0,face,0,0,p->BodyVisibility.Trace.UNAVAILABLE));
    }
}
