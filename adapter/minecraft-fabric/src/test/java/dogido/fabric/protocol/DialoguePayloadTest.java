package dogido.fabric.protocol;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class DialoguePayloadTest {
    private static final String SNAPSHOT = """
        {"sessions":[{"session_id":"mine","session_instance":"new","voice_enabled":false,
          "voice_generation":"generation","text_enabled":true}],"utterances":[
          {"session_id":"other","display_id":"private","text":"別プレイヤー"},
          {"session_id":"mine","session_instance":"old","display_id":"old","text":"前回"},
          {"session_id":"mine","session_instance":"new","turn_id":"turn","source":"voice",
           "player_input_text":"今なんて言った？","display_id":null,"text":"","playback_status":"generating"},
          {"session_id":"mine","display_id":"reply","text":"こんにちは","playback_status":"text_pending"}]}
        """;

    @Test void snapshotFiltersOtherSessionsAndOldInstancesButKeepsGeneratingTranscription() {
        var result = DialoguePayload.snapshot(SNAPSHOT, "mine");
        assertNotNull(result);
        assertEquals("new", result.scope().instance());
        assertFalse(result.voiceEnabled());
        assertEquals(2, result.rows().size());
        assertEquals("今なんて言った？", result.rows().getFirst().playerInput());
        assertEquals("", result.rows().getFirst().displayId());
        assertEquals("reply", result.rows().getLast().displayId());
        assertNull(DialoguePayload.snapshot(SNAPSHOT, "missing"));
    }

    @Test void unverifiedSessionOrMalformedBooleanCannotAuthorizeDisplay() {
        assertNull(DialoguePayload.snapshot(SNAPSHOT.replace("\"new\"", "null"), "mine"));
        assertNull(DialoguePayload.snapshot(SNAPSHOT.replace("\"voice_enabled\":false", "\"voice_enabled\":\"false\""), "mine"));
        assertNull(DialoguePayload.snapshot("not json", "mine"));
    }

    @Test void originalRecognitionIsShownOnceEvenWhenRoutingCreatesAnotherVoiceRow() {
        String body = SNAPSHOT.replace("\"text_enabled\":true", "\"text_enabled\":true,\"voice_inputs\":[{\"input_id\":\"i\",\"text\":\"今なんて言った？\"}]");
        var result = DialoguePayload.snapshot(body, "mine");
        assertNotNull(result);
        assertEquals(1, result.rows().stream().filter(row -> "voice".equals(row.source())).count());
        assertEquals("i", result.rows().getFirst().turnId());
        assertEquals("今なんて言った？", result.rows().getFirst().playerInput());
    }

    @Test void inputLimitCountsUnicodeCharactersWithoutTruncating() {
        String astral = "🐕".repeat(1000);
        assertNull(DialoguePayload.inputError(astral));
        assertEquals("text_too_long", DialoguePayload.inputError(astral + "犬"));
        assertEquals("empty_text", DialoguePayload.inputError("  "));
        assertNull(DialoguePayload.inputError("犬".repeat(500)));
    }

    @Test void acknowledgementMustAccountForEveryShownIdBeforeTheNextInput() {
        var partial = DialoguePayload.acknowledgement("{\"accepted\":true,\"displayed\":[\"one\"],\"rejected\":[]}");
        assertFalse(partial.covers(java.util.List.of("one", "two")));
        var terminal = DialoguePayload.acknowledgement("{\"accepted\":true,\"displayed\":[\"one\"],\"rejected\":[\"two\"]}");
        assertTrue(terminal.covers(java.util.List.of("one", "two")));
        assertFalse(DialoguePayload.acknowledgement("{}").accepted());
    }
}
