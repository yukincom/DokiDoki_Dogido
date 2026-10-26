package dogido.fabric.observation;

import com.google.gson.JsonObject;
import com.google.gson.JsonArray;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class WorldObservationFrameTest {
    @Test void eventsInOneCycleReuseOneMeasurementButCannotMutateEachOther() {
        AtomicInteger reads = new AtomicInteger();
        WorldObservationFrame frame = new WorldObservationFrame(() -> {
            reads.incrementAndGet();
            JsonObject value = new JsonObject(); value.addProperty("local_light", 8); return value;
        }, JsonObject::new, WorldObservationFrameTest::materials);
        assertEquals(0, reads.get());
        JsonObject first = frame.copy();
        first.addProperty("local_light", 1);
        assertEquals(8, frame.copy().get("local_light").getAsInt());
        assertEquals(1, reads.get());
    }
    @Test void newCycleTakesNewMeasurements() {
        AtomicInteger light = new AtomicInteger(8);
        java.util.function.Supplier<JsonObject> capture = () -> {
            JsonObject value = new JsonObject(); value.addProperty("local_light", light.get()); return value;
        };
        WorldObservationFrame first = new WorldObservationFrame(capture, JsonObject::new, WorldObservationFrameTest::materials);
        assertEquals(8, first.copy().get("local_light").getAsInt());
        light.set(7);
        WorldObservationFrame next = new WorldObservationFrame(capture, JsonObject::new, WorldObservationFrameTest::materials);
        assertEquals(7, next.copy().get("local_light").getAsInt());
        assertEquals(8, first.copy().get("local_light").getAsInt());
    }
    @Test void blockObservationIsCapturedOnceAndCopiedWithinTheCycle() {
        AtomicInteger reads = new AtomicInteger();
        WorldObservationFrame frame = new WorldObservationFrame(JsonObject::new, () -> {
            reads.incrementAndGet(); JsonObject v = new JsonObject(); v.addProperty("types", "stone"); return v;
        }, WorldObservationFrameTest::materials);
        frame.blocks().addProperty("types", "changed");
        assertEquals("stone", frame.blocks().get("types").getAsString());
        assertEquals(1, reads.get());
    }
    private static WorldObservationFrame.Materials materials() {
        JsonObject player = new JsonObject(); player.addProperty("health", 20);
        JsonObject inventory = new JsonObject(); inventory.addProperty("torch", 3);
        JsonObject mobs = new JsonObject(); mobs.addProperty("type", "cow");
        JsonArray drops = new JsonArray(); drops.add("stone");
        JsonArray breaks = new JsonArray(); breaks.add("dirt");
        return new WorldObservationFrame.Materials(player, inventory, mobs, drops, breaks, null);
    }

    @Test void eightEventCopiesUseOneReadAndCannotMutateSharedMaterials() {
        AtomicInteger reads = new AtomicInteger();
        WorldObservationFrame frame = new WorldObservationFrame(JsonObject::new, JsonObject::new, () -> {
            reads.incrementAndGet(); return materials();
        });
        assertEquals(0, reads.get());
        for (int i = 0; i < 8; i++) {
            var value = frame.materials();
            assertEquals(20, value.player().get("health").getAsInt());
            assertEquals(3, value.inventory().get("torch").getAsInt());
            assertEquals("cow", value.nearbyMobs().get("type").getAsString());
            assertEquals("stone", value.droppedItems().get(0).getAsString());
            assertEquals("dirt", value.recentBlockBreaks().get(0).getAsString());
            assertNull(value.lookTarget());
            value.player().addProperty("health", 1); value.inventory().addProperty("torch", 0);
            value.nearbyMobs().addProperty("type", "pig"); value.droppedItems().remove(0); value.recentBlockBreaks().remove(0);
        }
        assertEquals(1, reads.get(), "missing look target must not trigger a new read");
    }

    @Test void materialCaptureIsLazyAndASecondCycleReadsAgain() {
        AtomicInteger reads = new AtomicInteger();
        java.util.function.Supplier<WorldObservationFrame.Materials> capture = () -> {
            reads.incrementAndGet(); return materials();
        };
        var first = new WorldObservationFrame(JsonObject::new, JsonObject::new, capture);
        var next = new WorldObservationFrame(JsonObject::new, JsonObject::new, capture);
        first.blocks(); first.copy();
        assertEquals(0, reads.get());
        first.materials(); next.materials();
        assertEquals(2, reads.get());
    }
}
