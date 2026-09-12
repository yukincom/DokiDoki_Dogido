package dogido.fabric;

import com.google.gson.JsonParser;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class WorkshopDisplayStateTest {
    private static final List<String> VERSE = List.of("くわをもち", "はたけのまえで", "ひとやすみ");
    private static WorkshopDisplayState.Snapshot snapshot(long revision, long sequence, String mode, boolean editing, Integer line, boolean provisional) {
        return new WorkshopDisplayState.Snapshot("ses_a", "verse_a", revision, sequence, mode, VERSE, List.of(), editing, line, provisional);
    }
    private static String json() {
        return """
            {"schema_version":1,"session_id":"ses_a","workshop_id":"verse_a","revision":1,
             "observed_sequence":5,"state":"open","canonical_lines":["くわをもち","はたけのまえで","ひとやすみ"],
             "pending_lines":[],"editing":false,"selected_line":null,"provisional_resume":false}
            """;
    }

    @Test void strictWireContractAndSessionBinding() {
        assertNotNull(WorkshopDisplayState.parse(json(), "ses_a"));
        assertNull(WorkshopDisplayState.parse(json(), "ses_b"));
        assertNull(WorkshopDisplayState.parse("{}", "ses_a"));
        assertNull(WorkshopDisplayState.parse("[", "ses_a"));
        for (String invalid : List.of("\"1\"", "1.5", "-1", "4294967296")) {
            assertNull(WorkshopDisplayState.parse(json().replace("\"selected_line\":null", "\"selected_line\":" + invalid), "ses_a"));
        }
        assertNull(WorkshopDisplayState.parse(json().replace("\"editing\":false", "\"editing\":\"false\""), "ses_a"));
        assertNull(WorkshopDisplayState.parse(json().replace("\"open\"", "\"execute\""), "ses_a"));
        assertNull(WorkshopDisplayState.parse(json().replace("ひとやすみ", "あ".repeat(33)), "ses_a"));
        assertNull(WorkshopDisplayState.parse(json().replace("ひとやすみ", "§c赤"), "ses_a"));
    }

    @Test void keepsPendingLabelAndCanonicalSeparately() {
        var object = JsonParser.parseString(json()).getAsJsonObject();
        object.add("pending_lines", JsonParser.parseString("[\"くわをもち\",\"はたけのまえで\",\"ひとねむり\"]"));
        var parsed = WorkshopDisplayState.parse(object.toString(), "ses_a");
        assertEquals("ひとやすみ", parsed.canonical().get(2));
        assertEquals("ひとねむり", parsed.shownLines().get(2));
        assertThrows(UnsupportedOperationException.class, () -> parsed.canonical().clear());
    }

    @Test void entryAndNormalExitTakeOneSecondWithoutRestartOnPoll() {
        var model = new WorkshopDisplayState();
        model.receive(snapshot(1, 1, "open", false, null, false), 1000);
        assertEquals(0, model.opacity(1000));
        assertEquals(.5f, model.opacity(1500), .001);
        model.receive(snapshot(1, 1, "open", false, null, false), 1500);
        assertEquals(1, model.opacity(2000));
        model.receive(snapshot(2, 2, "closed", false, null, false), 2000);
        assertTrue(model.opacity(2500) > 0 && model.opacity(2500) < 1);
        assertEquals(VERSE, model.displayed().shownLines());
        assertEquals(0, model.opacity(3000));
    }

    @Test void serverAndLocalDangerCancelEntryAndExitImmediately() {
        for (String phase : List.of("entry", "exit")) {
            var model = new WorkshopDisplayState();
            model.receive(snapshot(1, 1, "open", false, null, false), 1000);
            if (phase.equals("exit")) model.receive(snapshot(2, 2, "closed", false, null, false), 2000);
            model.danger(true, true, 3, 2200);
            assertEquals(0, model.opacity(2200));
            model.receive(snapshot(3, 3, "danger", false, null, false), 2300);
            model.danger(false, false, 3, 2400);
            assertEquals(0, model.opacity(2400));
            model.receive(snapshot(4, 4, "open", false, null, false), 2500);
            assertEquals(1, model.opacity(3500));
        }
    }

    @Test void oldSnapshotCannotUndoLocalDangerOrWorldSynchronizationBarrier() {
        var model = new WorkshopDisplayState();
        model.receive(snapshot(1, 1, "open", false, null, false), 1000);
        model.danger(true, true, 5, 2000);
        model.danger(false, false, 5, 2100);
        model.receive(snapshot(2, 4, "open", false, null, false), 2200);
        assertEquals(0, model.opacity(2300));
        model.receive(snapshot(3, 5, "open", false, null, false), 2400);
        assertEquals(1, model.opacity(3400));
        model.reset();
        model.synchronizeAfter(10);
        model.receive(snapshot(4, 5, "open", false, null, false), 3500);
        assertEquals(0, model.opacity(4500));
    }

    @Test void explicitProvisionalResumeRequiresNewEnoughServerDecisionAndNewThreatCancelsIt() {
        var model = new WorkshopDisplayState();
        model.danger(true, true, 10, 1000);
        model.receive(snapshot(1, 9, "open", false, null, true), 1200);
        assertEquals(0, model.opacity(2200));
        model.receive(snapshot(2, 10, "open", false, null, true), 2300);
        model.danger(true, false, 11, 2500);
        assertEquals(1, model.opacity(3300));
        model.danger(true, true, 12, 3400);
        assertEquals(0, model.opacity(3400));
        model.receive(snapshot(3, 11, "open", false, null, true), 3500);
        assertEquals(0, model.opacity(4500));
        model.receive(snapshot(4, 12, "danger", false, null, false), 4600);
        assertEquals(0, model.opacity(4600));
    }

    @Test void staleTransportAndInvalidResponseHideWithoutChangingVerse() {
        var model = new WorkshopDisplayState();
        model.receive(snapshot(1, 1, "open", false, null, false), 1000);
        model.update(4001);
        assertEquals(0, model.opacity(4001));
        model.receive(snapshot(2, 2, "open", false, null, false), 5000);
        model.receive(null, 5500);
        model.update(5600);
        assertEquals(0, model.opacity(5600));
        assertEquals(VERSE, model.displayed().shownLines());
    }

    @Test void editCueIsImmediateAndSoundsOnlyOnNewSelectionNotReconnectOrRepeatedPoll() {
        var model = new WorkshopDisplayState();
        assertFalse(model.receive(snapshot(1, 1, "open", false, null, false), 1000));
        assertTrue(model.receive(snapshot(2, 2, "open", true, 1, false), 1200));
        assertEquals(1, model.opacity(1200));
        assertFalse(model.receive(snapshot(2, 2, "open", true, 1, false), 1300));
        assertTrue(model.receive(snapshot(3, 3, "open", true, 2, false), 1400));
        model.reset();
        assertFalse(model.receive(snapshot(3, 3, "open", true, 2, false), 1500));
    }
}
