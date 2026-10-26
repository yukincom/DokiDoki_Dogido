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
        }, JsonObject::new, WorldObservationFrameTest::materials, bounds -> java.util.List.of());
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
        WorldObservationFrame first = new WorldObservationFrame(capture, JsonObject::new, WorldObservationFrameTest::materials, bounds -> java.util.List.of());
        assertEquals(8, first.copy().get("local_light").getAsInt());
        light.set(7);
        WorldObservationFrame next = new WorldObservationFrame(capture, JsonObject::new, WorldObservationFrameTest::materials, bounds -> java.util.List.of());
        assertEquals(7, next.copy().get("local_light").getAsInt());
        assertEquals(8, first.copy().get("local_light").getAsInt());
    }
    @Test void blockObservationIsCapturedOnceAndCopiedWithinTheCycle() {
        AtomicInteger reads = new AtomicInteger();
        WorldObservationFrame frame = new WorldObservationFrame(JsonObject::new, () -> {
            reads.incrementAndGet(); JsonObject v = new JsonObject(); v.addProperty("types", "stone"); return v;
        }, WorldObservationFrameTest::materials, bounds -> java.util.List.of());
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
        }, bounds -> java.util.List.of());
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
        var first = new WorldObservationFrame(JsonObject::new, JsonObject::new, capture, bounds -> java.util.List.of());
        var next = new WorldObservationFrame(JsonObject::new, JsonObject::new, capture, bounds -> java.util.List.of());
        first.blocks(); first.copy();
        assertEquals(0, reads.get());
        first.materials(); next.materials();
        assertEquals(2, reads.get());
    }

    @Test void entityQueriesShareTypesButKeepBoxSphereAndCycleBoundaries() {
        var bounds = new net.minecraft.util.math.Box(-28, -28, -28, 28, 28, 28);
        var point = net.minecraft.util.math.Vec3d.ZERO;
        AtomicInteger reads = new AtomicInteger();
        java.util.function.Function<net.minecraft.util.math.Box, java.util.List<WorldObservationFrame.EntitySample>> capture = area -> {
            reads.incrementAndGet();
            return java.util.List.of(
                new WorldObservationFrame.EntitySample("tnt", new net.minecraft.util.math.Vec3d(28, 0, 0), null),
                new WorldObservationFrame.EntitySample("tnt", new net.minecraft.util.math.Vec3d(28, 28, 0), null),
                new WorldObservationFrame.EntitySample("tnt_minecart", point, null));
        };
        var frame = new WorldObservationFrame(JsonObject::new, JsonObject::new, WorldObservationFrameTest::materials, capture);
        assertEquals(0, reads.get());
        assertEquals(2, frame.countEntities(bounds, null, 0, "tnt"));
        assertEquals(1, frame.countEntities(bounds, null, 0, "tnt_minecart"));
        assertEquals(0, frame.countEntities(bounds, null, 0, "end_crystal"));
        assertEquals(1, frame.countEntities(bounds, point, 28, "tnt"));
        assertEquals(1, frame.countEntities(bounds, point, 28, "tnt_minecart"));
        frame.entities(new net.minecraft.util.math.Box(-28, -28, -28, 28, 28, 28));
        assertEquals(1, reads.get(), "equal bounds, different types and radial filter reuse the same box query");
        assertThrows(UnsupportedOperationException.class, () -> frame.entities(bounds).clear());
        frame.entities(bounds.offset(50, 0, 0));
        assertEquals(2, reads.get(), "a different center is a different observation");
        frame.entities(bounds.expand(1));
        assertEquals(3, reads.get(), "a different radius is a different observation");
        var next = new WorldObservationFrame(JsonObject::new, JsonObject::new, WorldObservationFrameTest::materials, capture);
        next.countEntities(bounds, null, 0, "tnt");
        assertEquals(4, reads.get(), "the next tick reads again");
    }
}
