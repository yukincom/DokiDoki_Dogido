package dogido.fabric;

import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class CharacterDisplayStateTest {
    private static WorkshopDisplayState.Snapshot snapshot(long rev, long seq, String scroll, String face) {
        return new WorkshopDisplayState.Snapshot("ses_a", null, rev, seq, scroll,
            List.of(), List.of(), false, null, false, face);
    }
    @Test void onlyActualGenerationThinksAndNormalReturnsWithoutFade() {
        var model = new CharacterDisplayState();
        assertFalse(model.thinking(0));
        model.receive(snapshot(1, 3, "closed", "thinking"), 100);
        assertTrue(model.thinking(100)); // no verse/workshop is needed yet
        model.receive(snapshot(2, 3, "open", "normal"), 200);
        assertFalse(model.thinking(200)); // reading or workshop lifetime is NOT thinking
        model.receive(snapshot(1, 3, "closed", "thinking"), 250);
        assertFalse(model.thinking(250)); // late response cannot revert the face
    }
    @Test void missingStaleFailedAndWorldResetUseNormalFace() {
        var model = new CharacterDisplayState();
        model.receive(snapshot(1, 1, "closed", "thinking"), 100);
        assertFalse(model.thinking(3101));
        model.receive(snapshot(2, 2, "closed", "thinking"), 3200);
        assertTrue(model.thinking(3200));
        model.receive(null, 3250);
        assertFalse(model.thinking(3250));
        model.receive(snapshot(3, 3, "closed", "thinking"), 3300);
        model.reset();
        assertFalse(model.thinking(3300));
        model.synchronizeAfter(6);
        model.receive(snapshot(4, 5, "closed", "thinking"), 3400);
        assertFalse(model.thinking(3400));
    }
    @Test void dangerStopsThinkingAndOlderSafeObservationCannotRestoreIt() {
        var model = new CharacterDisplayState();
        model.receive(snapshot(1, 1, "closed", "thinking"), 100);
        model.danger(true, true, 3);
        assertFalse(model.thinking(100));
        model.danger(false, false, 3);
        model.receive(snapshot(2, 2, "closed", "thinking"), 200);
        assertFalse(model.thinking(200));
        model.receive(snapshot(3, 3, "closed", "thinking"), 300);
        assertTrue(model.thinking(300));
        model.receive(snapshot(4, 4, "danger", "thinking"), 400);
        assertFalse(model.thinking(400));
    }
    @Test void additiveWireFieldDefaultsNormalAndRejectsInvalidValues() {
        String json = """
            {"schema_version":1,"session_id":"ses_a","workshop_id":null,"revision":1,
             "observed_sequence":5,"state":"closed","canonical_lines":[],
             "pending_lines":[],"editing":false,"selected_line":null,"provisional_resume":false}
            """;
        assertEquals("normal", WorkshopDisplayState.parse(json, "ses_a").characterState());
        String thinking = json.replace("\"state\":", "\"character_state\":\"thinking\",\"state\":");
        assertEquals("thinking", WorkshopDisplayState.parse(thinking, "ses_a").characterState());
        for (String invalid : List.of("\"speaking\"", "true", "null", "1"))
            assertNull(WorkshopDisplayState.parse(thinking.replace("\"thinking\"", invalid), "ses_a"));
    }
}
