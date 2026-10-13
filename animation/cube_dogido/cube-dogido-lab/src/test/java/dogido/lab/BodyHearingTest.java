package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 発音時の身体位置、元時刻、保持上限と失効を純粋なbufferで検査する。playerの受信や音再生は代用しない。 */
class BodyHearingTest {
    private static final Position BODY = new Position(100,70,100);
    private static final Position NEAR_BODY = new Position(101,70,100);
    private static final Context CONTEXT = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private static boolean hear(BodyHearing hearing, long tick, Position receiver, Position source, double range) {
        return hearing.hear(CONTEXT,tick,receiver,source,"minecraft:entity.cow.ambient","neutral",1,1f,range,null);
    }

    @Test void arrivalUsesBodyAtEmissionAndNeverAPlayerOrLaterPosition() {
        BodyHearing hearing = new BodyHearing();
        assertTrue(hear(hearing,10,BODY,NEAR_BODY,16));
        Position byPlayer = new Position(0,70,0);
        assertFalse(hear(hearing,11,BODY,byPlayer,16));
        var later = hearing.observe(CONTEXT,12,new Position(110,70,100),true);
        assertEquals(1,later.sounds().size());
        assertEquals(NEAR_BODY,later.sounds().getFirst().position());
        assertEquals(10,later.sounds().getFirst().serverTick());
        assertTrue(hearing.observe(CONTEXT,13,byPlayer,true).sounds().isEmpty(),"a large receiver jump invalidates old sounds");
    }

    @Test void rangeIsSuppliedFromSoundEventAndIncludesItsBoundary() {
        BodyHearing hearing = new BodyHearing();
        Position source = new Position(100,70,116);
        assertTrue(hear(hearing,10,BODY,source,16));
        assertFalse(hear(hearing,11,BODY,new Position(100,70,116.001),16));
        assertTrue(hear(hearing,12,BODY,new Position(100,70,150),64));
        assertFalse(hear(hearing,13,BODY,new Position(100,70,108),4),"fixed ranges may be smaller than sixteen");
    }

    @Test void repeatedSnapshotsPreserveAgeAndSameTickEventsHaveDistinctSequences() {
        BodyHearing hearing = new BodyHearing();
        assertTrue(hear(hearing,20,BODY,NEAR_BODY,16));
        assertTrue(hear(hearing,20,BODY,NEAR_BODY,16));
        var first = hearing.observe(CONTEXT,20,BODY,true);
        var repeated = hearing.observe(CONTEXT,80,BODY,true);
        assertEquals(first.sounds(),repeated.sounds());
        assertEquals(20,first.sounds().getFirst().serverTick());
        assertEquals(first.sounds().getFirst().sequence()+1,first.sounds().getLast().sequence());
        assertThrows(UnsupportedOperationException.class,() -> first.sounds().clear());
        assertEquals(2,hearing.observe(CONTEXT,320,BODY,true).sounds().size());
        assertTrue(hearing.observe(CONTEXT,321,BODY,true).sounds().isEmpty(),"reads must not renew TTL");
    }

    @Test void contextsLossAndUnavailableInvalidateHearingWithoutReusingSequence() {
        BodyHearing hearing = new BodyHearing();
        hear(hearing,20,BODY,NEAR_BODY,16);
        long previous = hearing.observe(CONTEXT,20,BODY,true).sounds().getFirst().sequence();
        var otherDimension = new Context(CONTEXT.ownerId(),CONTEXT.bodyId(),CONTEXT.epoch(),"minecraft:the_nether");
        assertTrue(hearing.observe(otherDimension,21,BODY,true).sounds().isEmpty());
        hear(hearing,22,BODY,NEAR_BODY,16);
        assertTrue(hearing.observe(CONTEXT,22,BODY,true).sounds().getFirst().sequence() > previous);
        var nextEpoch = new Context(CONTEXT.ownerId(),CONTEXT.bodyId(),UUID.randomUUID(),CONTEXT.dimension());
        assertTrue(hearing.observe(nextEpoch,23,BODY,true).sounds().isEmpty());
        hear(hearing,24,BODY,NEAR_BODY,16);
        assertEquals(ObservationStatus.UNAVAILABLE,hearing.observe(CONTEXT,25,BODY,false).status());
        assertTrue(hearing.observe(CONTEXT,26,BODY,true).sounds().isEmpty());
        hear(hearing,27,BODY,NEAR_BODY,16);
        assertEquals(ObservationStatus.UNAVAILABLE,hearing.observe(null,28,BODY,true).status());
        assertTrue(hearing.observe(CONTEXT,29,BODY,true).sounds().isEmpty());
        hear(hearing,30,BODY,NEAR_BODY,16);
        assertTrue(hearing.observe(CONTEXT,1,BODY,true).sounds().isEmpty(),"server time cannot renew future sounds");
    }

    @Test void bufferIsBoundedAndTruncationExpiresWithTheOmittedSound() {
        BodyHearing hearing = new BodyHearing();
        for (int i=0;i<BodyHearing.MAX_SOUNDS+3;i++) assertTrue(hear(hearing,10+i,BODY,NEAR_BODY,16));
        var observation = hearing.observe(CONTEXT,30,BODY,true);
        assertEquals(BodyHearing.MAX_SOUNDS,observation.sounds().size());
        assertTrue(observation.truncated());
        assertEquals(13,observation.sounds().getFirst().serverTick());
        assertFalse(hearing.observe(CONTEXT,313,BODY,true).truncated());
    }

    @Test void invalidEventsAreNotRecordedAndUnknownExplosionPitchStaysAbsent() {
        BodyHearing hearing = new BodyHearing();
        assertFalse(hear(hearing,10,BODY,new Position(Double.NaN,0,0),16));
        assertFalse(hear(hearing,10,BODY,NEAR_BODY,Double.NaN));
        assertFalse(hearing.hear(CONTEXT,10,BODY,NEAR_BODY,"x".repeat(BodyHearing.MAX_SOUND_ID_LENGTH+1),"blocks",1,1f,16,null));
        assertFalse(hearing.hear(CONTEXT,10,BODY,NEAR_BODY,"minecraft:bad","blocks",0,1f,16,null));
        assertFalse(hearing.hear(CONTEXT,10,BODY,NEAR_BODY,"minecraft:bad","blocks",1,Float.NaN,16,null));
        UUID emitter = UUID.randomUUID();
        assertTrue(hearing.hear(CONTEXT,11,BODY,NEAR_BODY,"minecraft:entity.generic.explode","blocks",4,null,64,emitter));
        var sound = hearing.observe(CONTEXT,11,BODY,true).sounds().getFirst();
        assertNull(sound.pitch());
        assertEquals(emitter,sound.sourceEntityId());
        assertEquals(1,sound.sequence());
    }
}
