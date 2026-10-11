package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.BodyTuning;
import dogido.lab.control.BodyVitals;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** HPの低さだけでは休息を選ばず、選んだ休息と実接触にだけ身体ルールが働くことを検査する。 */
class BodyVitalsTest {
    private final BodyTuning rules = new BodyTuning();
    @Test void lowHealthAloneDoesNotAutomaticallyRestOrHeal() {
        BodyVitals body = new BodyVitals();
        for (int i=0;i<600;i++) assertEquals(0,body.step(true,true,false,false,1,20,rules).recovery());
        assertEquals(0,body.snapshot(1,20,true,"survival").restTicks());
    }
    @Test void actualRestRecoversOverTimeAndMovementInterruptsTheRestPeriod() {
        BodyVitals body = new BodyVitals();
        for (int i=0;i<100;i++) assertEquals(0,body.step(true,true,false,true,8,20,rules).recovery());
        assertEquals(1,body.step(true,true,false,true,8,20,rules).recovery());
        for (int i=0;i<59;i++) assertEquals(0,body.step(true,true,false,true,9,20,rules).recovery());
        assertEquals(1,body.step(true,true,false,true,9,20,rules).recovery());
        body.step(true,true,false,false,10,20,rules);
        for (int i=0;i<100;i++) assertEquals(0,body.step(true,true,false,true,10,20,rules).recovery());
    }
    @Test void shortWallPassageIsFreeButLongStayTakesRepeatedDamageWithoutForcedExit() {
        BodyVitals body = new BodyVitals();
        for (int i=0;i<100;i++) assertEquals(0,body.step(true,true,true,false,20,20,rules).wallDamage());
        assertEquals(1,body.step(true,true,true,false,20,20,rules).wallDamage());
        for (int i=0;i<19;i++) assertEquals(0,body.step(true,true,true,false,19,20,rules).wallDamage());
        assertEquals(1,body.step(true,true,true,false,19,20,rules).wallDamage());
        assertEquals(121,body.wallTicks());
        body.step(true,true,false,false,18,20,rules);
        assertEquals(0,body.wallTicks());
    }
    @Test void damageDisabledModeAlsoDisablesWallDamageButStillMeasuresExposure() {
        BodyVitals body = new BodyVitals();
        for (int i=0;i<200;i++) assertEquals(0,body.step(true,false,true,false,20,20,rules).wallDamage());
        assertEquals(200,body.wallTicks());
        assertEquals(1,body.step(true,true,true,false,20,20,rules).wallDamage());
    }
    @Test void damageRestartsRecoveryDelayAndReportsOnlyActualHealthLoss() {
        BodyVitals body = new BodyVitals();
        for (int i=0;i<99;i++) body.step(true,true,false,true,15,20,rules);
        body.recordDamage(100,4,"arrow");
        assertEquals(0,body.step(true,true,false,true,11,20,rules).recovery());
        assertEquals(new DamageEvent(1,100,4,"arrow"),body.snapshot(11,20,true,"survival").lastDamage());
        body.recordDamage(101,0,"blocked");
        assertEquals(1,body.damageSequence());
    }
    @Test void collapseWaitsForOwnerAndOnlySuccessfulRespawnIncrementsTheCounter() {
        BodyVitals body = new BodyVitals(); body.collapse(rules);
        for (int i=0;i<400;i++) assertFalse(body.step(false,true,false,true,0,20,rules).respawnReady());
        assertEquals(100,body.remaining());
        for (int i=0;i<99;i++) assertFalse(body.step(true,true,false,true,0,20,rules).respawnReady());
        assertTrue(body.step(true,true,false,true,0,20,rules).respawnReady());
        assertEquals(0,body.respawnCount()); assertTrue(body.downed());
        assertTrue(body.step(true,true,false,true,0,20,rules).respawnReady());
        body.revived();
        assertFalse(body.downed()); assertEquals(1,body.respawnCount());
    }
    @Test void savedWallExposureAndDownedDelaySurviveReloadButRestMustBeRequestedAgain() {
        BodyVitals body = new BodyVitals(); body.restore(true,120,50,2,7);
        assertTrue(body.downed()); assertEquals(120,body.wallTicks()); assertEquals(2,body.respawnCount());
        assertFalse(body.step(true,true,false,false,0,20,rules).respawnReady());
        assertEquals(49,body.remaining());
        assertNull(body.snapshot(0,20,true,"survival").lastDamage());
        body.revived(); body.recordDamage(4,2,"inWall");
        assertEquals(8,body.damageSequence());
    }
    @Test void foodHealsOnlyMissingHealthAndCannotReviveTheDownedBody() {
        assertEquals(4,BodyVitals.foodRecovery(6,16,20,false));
        assertEquals(0,BodyVitals.foodRecovery(6,20,20,false));
        assertEquals(0,BodyVitals.foodRecovery(6,0,20,true));
        assertEquals(0,BodyVitals.foodRecovery(0,10,20,false));
    }
}
