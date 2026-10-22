package dogido.fabric.display;

import dogido.fabric.protocol.DialoguePayload;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class DialogueDisplayStateTest {
    private static DialoguePayload.Snapshot snapshot(String instance, DialoguePayload.Row... rows) {
        return new DialoguePayload.Snapshot(new DialoguePayload.Scope("session", instance), false, "voice-1", true,
            List.of(rows),null);
    }

    private static DialoguePayload.Row row(String turn, String display, String text, String status) {
        return new DialoguePayload.Row(turn, "text", "", display, text, status);
    }

    @Test void onlyReleasedTextIsInsertedAndAcknowledged() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        state.receive(snapshot("instance", row("t", "", "生成中", "generating"),
            row("cancelled", "d-cancelled", "取り消した返答", "cancelled"),
            row("ready", "d-ready", "こんにちは", "text_pending")), shown::add);
        assertEquals(List.of("[ドギド] こんにちは"), shown);
        assertEquals(List.of("d-ready"), state.pending());
    }

    @Test void failedAcknowledgementRetriesWithoutDuplicatingChatText() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        var snapshot = snapshot("instance", row("t", "d", "返答", "text_pending"));
        state.receive(snapshot, shown::add);
        state.acknowledge(snapshot.scope(), DialoguePayload.Acknowledgement.failed());
        state.receive(snapshot, shown::add);
        assertEquals(1, shown.size());
        assertEquals(List.of("d"), state.pending());
        state.acknowledge(snapshot.scope(), new DialoguePayload.Acknowledgement(true, List.of("d"), List.of()));
        state.receive(snapshot, shown::add);
        assertTrue(state.pending().isEmpty());
        assertEquals(1, shown.size());
    }

    @Test void transcriptionAppearsWhileGeneratingAndOncePerTurnAcrossRows() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        var generating = new DialoguePayload.Row("turn", "voice", "犬がいる", "", "", "generating");
        state.receive(snapshot("instance", generating), shown::add);
        assertEquals(List.of("[聞き取り] 犬がいる"), shown);
        assertTrue(state.pending().isEmpty());
        var complete = new DialoguePayload.Row("turn", "voice", "犬がいる", "reply", "見えたで", "text_pending");
        state.receive(snapshot("instance", generating, complete), shown::add);
        assertEquals(List.of("[聞き取り] 犬がいる", "[ドギド] 見えたで"), shown);
    }

    @Test void insertionFailureDoesNotAcknowledgeTextThatNeverReachedChat() {
        DialogueDisplayState state = new DialogueDisplayState();
        var snapshot = snapshot("instance", row("t", "d", "返答", "text_pending"));
        assertThrows(IllegalStateException.class, () -> state.receive(snapshot, text -> {
            throw new IllegalStateException("chat unavailable");
        }));
        assertTrue(state.pending().isEmpty());
        List<String> shown = new ArrayList<>();
        state.receive(snapshot, shown::add);
        assertEquals(List.of("[ドギド] 返答"), shown);
        assertEquals(List.of("d"), state.pending());
    }

    @Test void anotherServerInstanceResetsIdsAndRejectsLateOldAcknowledgement() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        var old = snapshot("old", row("t", "d", "古い返答", "text_pending"));
        var current = snapshot("new", row("t", "d", "新しい返答", "text_pending"));
        state.receive(old, shown::add);
        state.receive(current, shown::add);
        state.acknowledge(old.scope(), new DialoguePayload.Acknowledgement(true, List.of("d"), List.of()));
        assertEquals(List.of("[ドギド] 古い返答", "[ドギド] 新しい返答"), shown);
        assertEquals(List.of("d"), state.pending());
        state.acknowledge(current.scope(), new DialoguePayload.Acknowledgement(true, List.of(), List.of("d")));
        assertTrue(state.pending().isEmpty());
    }

    @Test void expiredSnapshotIdsDoNotAccumulateInTheAcknowledgementQueue() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        for (int n = 0; n < 300; n++) {
            state.receive(snapshot("instance", row("t" + n, "d" + n, "返答", "audio_disabled")), shown::add);
        }
        assertEquals(300, shown.size());
        assertEquals(List.of("d299"), state.pending());
    }
}
