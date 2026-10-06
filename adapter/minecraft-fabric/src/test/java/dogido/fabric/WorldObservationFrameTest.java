package dogido.fabric;

import com.google.gson.JsonObject;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class WorldObservationFrameTest {
    @Test void eventsInOneCycleReuseOneMeasurementButCannotMutateEachOther() {
        AtomicInteger reads = new AtomicInteger();
        WorldObservationFrame frame = new WorldObservationFrame(() -> {
            reads.incrementAndGet();
            JsonObject value = new JsonObject(); value.addProperty("local_light", 8); return value;
        });
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
        WorldObservationFrame first = new WorldObservationFrame(capture);
        assertEquals(8, first.copy().get("local_light").getAsInt());
        light.set(7);
        WorldObservationFrame next = new WorldObservationFrame(capture);
        assertEquals(7, next.copy().get("local_light").getAsInt());
        assertEquals(8, first.copy().get("local_light").getAsInt());
    }
}
