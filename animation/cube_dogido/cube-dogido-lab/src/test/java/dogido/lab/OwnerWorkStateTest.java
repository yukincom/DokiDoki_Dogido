package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.OwnerWorkState;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class OwnerWorkStateTest {
    private final Context context = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    @Test void currentWorkAndItsEndAreObservedAndMissingDataKeepsFrontFree() {
        OwnerWorkState state = new OwnerWorkState(); assertTrue(state.avoidFront(100));
        assertTrue(state.observe(new OwnerWorkObservation(context,100,false),context,100));
        assertFalse(state.avoidFront(100));
        assertTrue(state.observe(new OwnerWorkObservation(context,105,true),context,105));
        assertTrue(state.avoidFront(105));
        assertTrue(state.observe(new OwnerWorkObservation(context,110,false),context,110));
        assertFalse(state.avoidFront(130)); assertTrue(state.avoidFront(131));
        state.clear(); assertTrue(state.avoidFront(110));
    }
    @Test void oldDuplicateFutureAndForeignObservationsCannotExtendOrReverseTheState() {
        OwnerWorkState state = new OwnerWorkState();
        assertTrue(state.observe(new OwnerWorkObservation(context,100,false),context,100));
        for (long tick : new long[]{99,100,121})
            assertFalse(state.observe(new OwnerWorkObservation(context,tick,true),context,120));
        Context foreign = new Context(context.ownerId(),UUID.randomUUID(),context.epoch(),context.dimension());
        assertFalse(state.observe(new OwnerWorkObservation(foreign,120,true),context,120));
        assertFalse(state.avoidFront(120)); assertTrue(state.avoidFront(121));
        assertFalse(state.observe(new OwnerWorkObservation(context,101,false),context,122));
    }
    @Test void packetRoundTripPreservesWorkEndAndMissingFlagIsNotIdle() {
        var json = new com.google.gson.Gson();
        OwnerWorkState state = new OwnerWorkState();
        for (boolean working : new boolean[]{true,false}) {
            long tick = working ? 100 : 110;
            var packet = new BodyPackets.Message(dogido.lab.api.BodyProtocol.VERSION,"owner_work",null,null,new OwnerWorkObservation(context,tick,working),null);
            var decoded = json.fromJson(json.toJson(packet),BodyPackets.Message.class);
            assertEquals(packet,decoded); assertTrue(state.observe(decoded.ownerWork(),context,tick));
            assertEquals(working,state.avoidFront(tick));
        }
        var missing = json.toJsonTree(new OwnerWorkObservation(context,120,false)).getAsJsonObject(); missing.remove("working");
        assertFalse(state.observe(json.fromJson(missing,OwnerWorkObservation.class),context,120));
    }
}
