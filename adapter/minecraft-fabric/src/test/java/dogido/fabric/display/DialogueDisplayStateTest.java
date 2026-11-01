package dogido.fabric.display;

import dogido.fabric.protocol.DialoguePayload;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class DialogueDisplayStateTest {
    private static DialoguePayload.Snapshot snapshot(String instance, DialoguePayload.Row... rows) {
        return new DialoguePayload.Snapshot(new DialoguePayload.Scope("session", instance), false, "voice-1", true,
            List.of(rows), null, null);
    }

    private static DialoguePayload.Row row(String turn, String display, String text, String status) {
        return new DialoguePayload.Row(turn, "text", "", display, text, status, null);
    }

    private static DialoguePayload.Snapshot combined(long sequence, String state, String rows) {
        return DialoguePayload.snapshot("""
            {"sessions":[{"session_id":"s","session_instance":"instance","voice_generation":"v",
              "voice_enabled":false,"text_enabled":true}],"utterances":[%s],"workshop":{
              "schema_version":1,"session_id":"s","workshop_id":"w","revision":%d,"observed_sequence":%d,
              "state":"%s","workshop_version":0,"workshop_boundary":2,"canonical_lines":["朝","昼","夜"],"pending_lines":[],"editing":false,"selected_line":null}}
            """.formatted(rows, sequence, sequence, state), "s");
    }

    private static String resume(String turn, String display, String text) {
        return """
            {"session_id":"s","session_instance":"instance","turn_id":"%s","display_id":"%s","text":"%s",
            "workshop_resume_boundary":2,"combat_actions":[{"kind":"workshop_resume","scope":{"kind":"workshop","targets":{"id":"w","version":0}}}]}
            """.formatted(turn, display, text);
    }

    // 見込み: 戦闘を挟んだ同じ句は、同じsnapshotの本文と同じフレームで再掲される。
    // 合格: ローカル危険中と古い観測では本文・ACKなし。再開で句が即表示され、一度だけ本文を挿入する。描画状態の模擬試験。
    @Test void combatResumeDisplaysTextAndFullVerseAtTheSameSnapshotBoundary() {
        var chat = new DialogueDisplayState();
        var hud = new WorkshopDisplayState();
        var initial = combined(1, "open", "");
        hud.receive(initial.workshop(), 0);
        hud.danger(true, true, 3, 1000);
        String ready = resume("turn", "d1", "川柳に戻ろか。");
        var stale = combined(2, "open", ready);
        hud.receive(stale.workshop(), 1100);
        chat.receive(stale, hud.showing(), text -> fail("古い復帰文が表示された"));
        hud.danger(false, false, 3, 1200);
        assertFalse(hud.showing());
        assertTrue(chat.pending().isEmpty());
        var current = combined(3, "open", ready);
        List<String> messages = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            hud.receive(current.workshop(), 1300 + i);
            chat.receive(current, hud.showing(), text -> {
                assertEquals(1f, hud.opacity(1300));
                assertEquals(List.of("朝", "昼", "夜"), hud.displayed().shownLines());
                messages.add(text);
            });
        }
        assertEquals(List.of("[ドギド] 川柳に戻ろか。"), messages);
        assertEquals(List.of("d1"), chat.pending());
    }

    // 再交戦後の新しい再開がsilentなら、遅れて届いた前の復帰文を表示・ACKしない。
    @Test void silentResumeSupersedesUnseenTextFromPreviousCombat() {
        var chat = new DialogueDisplayState();
        var hud = new WorkshopDisplayState();
        var current = combined(6, "open", resume("old", "d-old", "前の復帰") + "," + resume("current", "", ""));
        hud.receive(current.workshop(), 3000);
        chat.receive(current, hud.showing(), text -> fail("前の復帰文が表示された"));
        assertTrue(hud.showing());
        assertTrue(chat.pending().isEmpty());
    }

    @Test void onlyReleasedTextIsInsertedAndAcknowledged() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        state.receive(snapshot("instance", row("t", "", "生成中", "generating"),
            row("cancelled", "d-cancelled", "取り消した返答", "cancelled"),
            row("ready", "d-ready", "こんにちは", "text_pending")), true, shown::add);
        assertEquals(List.of("[ドギド] こんにちは"), shown);
        assertEquals(List.of("d-ready"), state.pending());
    }

    // 同じ句でも前の中断や編集前の通知は、次の表示で読み上げ・ACKしない。
    @Test void oldResumeBoundaryOrVersionIsNotInsertedIntoCurrentWorkshop() {
        for (String stale : List.of(
                resume("old", "d", "前の中断").replace("boundary\":2", "boundary\":1"),
                resume("old", "d", "前の版").replace("version\":0", "version\":1"))) {
            var display = new DialogueDisplayState();
            List<String> shown = new ArrayList<>();
            display.receive(combined(8, "open", stale), true, shown::add);
            assertTrue(shown.isEmpty());
            assertTrue(display.pending().isEmpty());
        }
    }

    @Test void failedAcknowledgementRetriesWithoutDuplicatingChatText() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        var snapshot = snapshot("instance", row("t", "d", "返答", "text_pending"));
        state.receive(snapshot, true, shown::add);
        state.acknowledge(snapshot.scope(), DialoguePayload.Acknowledgement.failed());
        state.receive(snapshot, true, shown::add);
        assertEquals(1, shown.size());
        assertEquals(List.of("d"), state.pending());
        state.acknowledge(snapshot.scope(), new DialoguePayload.Acknowledgement(true, List.of("d"), List.of()));
        state.receive(snapshot, true, shown::add);
        assertTrue(state.pending().isEmpty());
        assertEquals(1, shown.size());
    }

    @Test void transcriptionAppearsWhileGeneratingAndOncePerTurnAcrossRows() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        var generating = new DialoguePayload.Row("turn", "voice", "犬がいる", "", "", "generating", null);
        state.receive(snapshot("instance", generating), true, shown::add);
        assertEquals(List.of("[聞き取り] 犬がいる"), shown);
        assertTrue(state.pending().isEmpty());
        var complete = new DialoguePayload.Row("turn", "voice", "犬がいる", "reply", "見えたで", "text_pending", null);
        state.receive(snapshot("instance", generating, complete), true, shown::add);
        assertEquals(List.of("[聞き取り] 犬がいる", "[ドギド] 見えたで"), shown);
    }

    @Test void insertionFailureDoesNotAcknowledgeTextThatNeverReachedChat() {
        DialogueDisplayState state = new DialogueDisplayState();
        var snapshot = snapshot("instance", row("t", "d", "返答", "text_pending"));
        assertThrows(IllegalStateException.class, () -> state.receive(snapshot, true, text -> {
            throw new IllegalStateException("chat unavailable");
        }));
        assertTrue(state.pending().isEmpty());
        List<String> shown = new ArrayList<>();
        state.receive(snapshot, true, shown::add);
        assertEquals(List.of("[ドギド] 返答"), shown);
        assertEquals(List.of("d"), state.pending());
    }

    @Test void anotherServerInstanceResetsIdsAndRejectsLateOldAcknowledgement() {
        DialogueDisplayState state = new DialogueDisplayState();
        List<String> shown = new ArrayList<>();
        var old = snapshot("old", row("t", "d", "古い返答", "text_pending"));
        var current = snapshot("new", row("t", "d", "新しい返答", "text_pending"));
        state.receive(old, true, shown::add);
        state.receive(current, true, shown::add);
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
            state.receive(snapshot("instance", row("t" + n, "d" + n, "返答", "audio_disabled")), true, shown::add);
        }
        assertEquals(300, shown.size());
        assertEquals(List.of("d299"), state.pending());
    }
}
