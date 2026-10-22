package dogido.fabric.screenshot;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class BodyViewMotionTest {
    private BodyViewMotion.Pose pose(double x,double yaw) {return new BodyViewMotion.Pose(x,64,0,yaw,0);}
    @Test void initialStopIsOnePhotoAndMotionNeverBecomesAnInterval() {
        var motion=new BodyViewMotion();motion.observe("world",pose(0,0));motion.observe("world",pose(0,0));assertEquals(0,motion.sequence());
        motion.observe("world",pose(0,0));assertEquals(1,motion.sequence());
        for(int i=0;i<100;i++)motion.observe("world",pose(0,0));assertEquals(1,motion.sequence());
        for(int i=1;i<20;i++){motion.observe("world",pose(i*.1,0));assertFalse(motion.still(pose(i*.1,0)));}
        assertEquals(1,motion.sequence());motion.observe("world",pose(1.9,0));motion.observe("world",pose(1.9,0));assertEquals(2,motion.sequence());
    }
    @Test void turningStopsOnceAndWrappedAnglesDoNotCreateFalseMotion() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,179));
        long translation=motion.translation();motion.observe("world",pose(0,-179));assertFalse(motion.still(pose(0,-179)));
        motion.observe("world",pose(0,-179));motion.observe("world",pose(0,-179));assertEquals(2,motion.sequence());assertEquals(translation,motion.translation());
        motion.observe("world",pose(0,181));assertTrue(motion.still(pose(0,181)));assertEquals(2,motion.sequence());
    }
    @Test void smallButContinuingMotionIsNotMistakenForAStopAndRoundTripCountsAsTranslation() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("world",pose(0,0));long before=motion.translation();
        for(int i=1;i<15;i++)motion.observe("world",pose(i*.002,i*.1));assertEquals(1,motion.sequence());
        motion.observe("world",pose(0,0));motion.observe("world",pose(0,0));motion.observe("world",pose(0,0));
        assertEquals(2,motion.sequence());assertTrue(motion.translation()>before);
    }
    @Test void newWorldResetsStabilityButKeepsSequenceMonotonic() {
        var motion=new BodyViewMotion();for(int i=0;i<3;i++)motion.observe("old",pose(0,0));
        motion.clear();motion.observe("new",pose(0,0));assertFalse(motion.still(pose(0,0)));
        motion.observe("new",pose(0,0));motion.observe("new",pose(0,0));assertEquals(2,motion.sequence());
    }
}
