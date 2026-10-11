package dogido.fabric.protocol;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.observation.mobs.HostileOutcomeObservation;

public final class EventObservationJson {
    private EventObservationJson() {}

    public static JsonArray buildHostileOutcomes(
        java.util.Collection<HostileOutcomeObservation> outcomes
    ) {
        JsonArray hostileOutcomeArray = new JsonArray();
        for (HostileOutcomeObservation outcome : outcomes) {
            JsonObject entry = new JsonObject();
            entry.addProperty("entity_id", outcome.entityUuid().toString());
            entry.addProperty("type", outcome.type());
            entry.addProperty("outcome", outcome.outcome());
            entry.addProperty("evidence", outcome.evidence());
            hostileOutcomeArray.add(entry);
        }
        return hostileOutcomeArray;
    }
}
