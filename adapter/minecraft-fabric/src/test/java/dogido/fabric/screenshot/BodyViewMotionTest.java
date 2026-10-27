package dogido.fabric.screenshot;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class BodyViewMotionTest {
    private BodyViewMotion.Pose pose(double x,double yaw) {return new BodyViewMotion.Pose(x,64,0,yaw,0);}
    @Test void initialStopAndMovementStillUseTheSameQuietBoundary() {
        var motion=new BodyViewMotion();motion.observe("world",pose(0,0));motion.observe("world",pose(0,0));assertEquals(0,motion.sequence());
        motion.observe("world",pose(0,0));assertEquals(1,motion.sequence());
        for(int i=0;i<100;i++)motion.observe("world",pose(0,0));assertEquals(1,motion.sequence());
        for(int i=1;i<20;i++){motion.observe("world",pose(i*.1,0));assertFalse(motion.still(pose(i*.1,0)));}
        assertEquals(1,motion.sequence());motion.observe("world",pose(1.9,0));motion.observe("world",pose(1.9,0));assertEquals(2,motion.sequence());
    }
    @Test void stationaryViewRefreshesEveryTenSecondsWithoutInventingTranslation() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,0));
        long translation=motion.translation();
        for(int i=1;i<BodyViewMotion.REFRESH_TICKS;i++)motion.observe("world",pose(0,0));
        assertEquals(1,motion.sequence());
        motion.observe("world",pose(0,0));assertEquals(2,motion.sequence());assertEquals(translation,motion.translation());
        for(int i=0;i<BodyViewMotion.REFRESH_TICKS;i++)motion.observe("world",pose(0,0));
        assertEquals(3,motion.sequence());assertTrue(motion.still(pose(0,0)));
    }
    @Test void periodicRefreshStillWaitsUntilMotionStops() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,0));
        for(int i=1;i<=250;i++)motion.observe("world",pose(i*.1,0));
        assertEquals(1,motion.sequence());
        motion.observe("world",pose(25,0));assertEquals(1,motion.sequence());
        motion.observe("world",pose(25,0));assertEquals(2,motion.sequence());
    }
    @Test void turningStopsOnceAndWrappedAnglesDoNotCreateFalseMotion() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,179));
        long translation=motion.translation();motion.observe("world",pose(0,-179));assertFalse(motion.still(pose(0,-179)));
        motion.observe("world",pose(0,-179));motion.observe("world",pose(0,-179));assertEquals(1,motion.sequence());assertEquals(translation,motion.translation());
        motion.observe("world",pose(0,181));assertTrue(motion.still(pose(0,181)));assertEquals(1,motion.sequence());
        stop(motion,pose(0,-170));assertEquals(2,motion.sequence());assertEquals(translation,motion.translation());
    }
    @Test void smallButContinuingMotionIsNotMistakenForAStopAndRoundTripCountsAsTranslation() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,0));long before=motion.translation();
        for(int i=1;i<15;i++)motion.observe("world",pose(i*.002,i*.1));assertEquals(1,motion.sequence());
        motion.observe("world",pose(0,0));motion.observe("world",pose(0,0));motion.observe("world",pose(0,0));
        assertEquals(1,motion.sequence());assertTrue(motion.translation()>before);
    }
    @Test void explicitCaptureConsumesTheCurrentTurnWithoutAnImmediateSecondOrdinaryCapture() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,0));
        motion.observe("world",pose(0,90));
        assertEquals(2,motion.afterLookSequence(1));
        for(int i=0;i<10;i++)motion.observe("world",pose(0,90));
        assertEquals(2,motion.sequence());
        motion.observe("world",pose(1,90));motion.observe("world",pose(1,90));motion.observe("world",pose(1,90));
        assertEquals(3,motion.sequence());
    }
    @Test void newWorldResetsStabilityButKeepsSequenceMonotonic() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("old",pose(0,0));
        motion.clear();motion.observe("new",pose(0,0));assertFalse(motion.still(pose(0,0)));
        motion.observe("new",pose(0,0));motion.observe("new",pose(0,0));assertEquals(2,motion.sequence());
    }
    private void stop(BodyViewMotion motion,BodyViewMotion.Pose pose) {
        for(int i=0;i<3;i++)motion.observe("world",pose);
    }
    @Test void hoverExtremesDoNotTriggerExtraViewsOrResetTheRefreshClock() {
        var motion=new BodyViewMotion();stop(motion,new BodyViewMotion.Pose(0,64.18,0,0,0));
        long translation=motion.translation();
        // 浮遊の上下端は0.36 block差。撮影時点の記録に合わせた停止姿勢列で、実tick列の再現ではない。
        for(int i=0;i<6;i++)stop(motion,new BodyViewMotion.Pose(0,i%2==0?63.82:64.18,0,0,0));
        assertEquals(1,motion.sequence());assertTrue(motion.translation()>translation);
        for(int i=18;i<BodyViewMotion.REFRESH_TICKS-1;i++)motion.observe("world",new BodyViewMotion.Pose(0,64.18,0,0,0));
        assertEquals(1,motion.sequence());
        motion.observe("world",new BodyViewMotion.Pose(0,64.18,0,0,0));assertEquals(2,motion.sequence());
    }
    @Test void smallStepsAccumulateFromTheLastObservationInAllThreeAxes() {
        for(int axis=0;axis<3;axis++) {
            var motion=new BodyViewMotion();stop(motion,new BodyViewMotion.Pose(0,0,0,0,0));
            for(double d:new double[]{.1,.2,.3,.4,.499}) {
                stop(motion,new BodyViewMotion.Pose(axis==0?d:0,axis==1?d:0,axis==2?d:0,0,0));
                assertEquals(1,motion.sequence());
            }
            stop(motion,new BodyViewMotion.Pose(axis==0?.5:0,axis==1?.5:0,axis==2?.5:0,0,0));
            assertEquals(2,motion.sequence());
        }
    }
    @Test void smallTurnsAccumulateUntilYawOrPitchReachesTenDegrees() {
        for(boolean pitch:new boolean[]{false,true}) {
            var motion=new BodyViewMotion();stop(motion,pose(0,0));
            for(double angle:new double[]{2,4,6,8,9.99}) {
                stop(motion,new BodyViewMotion.Pose(0,64,0,pitch?0:angle,pitch?angle:0));
                assertEquals(1,motion.sequence());
            }
            stop(motion,new BodyViewMotion.Pose(0,64,0,pitch?0:10,pitch?10:0));assertEquals(2,motion.sequence());
        }
    }
    @Test void returningBeforeStoppingDoesNotLeaveALatchedCapture() {
        var motion=new BodyViewMotion();stop(motion,pose(0,0));long translation=motion.translation();
        motion.observe("world",pose(1,90));motion.observe("world",pose(0,0));
        stop(motion,pose(0,0));assertEquals(1,motion.sequence());assertTrue(motion.translation()>translation);
    }
    @Test void recordedSubsecondPitchAndYawCorrectionsProduceOnlyTheFirstNewView() {
        var motion=new BodyViewMotion();stop(motion,new BodyViewMotion.Pose(0,64,0,-413.4375,37.856));
        // 2026-10-27 13:03:27.660〜28.806の4枚。位置は同じで、最初の旋回後の微調整だけが続く。
        for(double[] angles:new double[][]{{-432.818,22.5},{-432.818,21.09375},{-432.818,23.90625},{-428.906,23.90625}}) {
            stop(motion,new BodyViewMotion.Pose(0,64,0,angles[0],angles[1]));assertEquals(2,motion.sequence());
        }
        stop(motion,new BodyViewMotion.Pose(0,64,0,-455.156,-7.03125));assertEquals(3,motion.sequence());
    }
    @Test void explicitCaptureResetsTheBaselineAndTimerEvenBelowAutomaticThresholds() {
        var motion=new BodyViewMotion();stop(motion,pose(0,0));
        for(int i=0;i<150;i++)motion.observe("world",pose(0,0));
        motion.observe("world",pose(.1,2));assertFalse(motion.still(pose(.1,2)));
        assertEquals(2,motion.afterLookSequence(1));
        for(int i=1;i<BodyViewMotion.REFRESH_TICKS;i++)motion.observe("world",pose(.1,2));
        assertEquals(2,motion.sequence());
        motion.observe("world",pose(.1,2));assertEquals(3,motion.sequence());
    }
}
