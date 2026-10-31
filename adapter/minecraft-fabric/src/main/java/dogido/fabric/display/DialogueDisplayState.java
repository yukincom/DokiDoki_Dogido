// パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
package dogido.fabric.display;

import dogido.fabric.protocol.DialoguePayload;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;

/** Seen text and pending acknowledgements survive retries, but never another server session instance. */
public final class DialogueDisplayState {
    private DialoguePayload.Scope scope;
    private final Set<String> seenDisplays = new HashSet<>();
    private final Set<String> seenTranscriptions = new HashSet<>();
    private final Set<String> pending = new LinkedHashSet<>();
    private Boolean voiceEnabled;

    public synchronized void reset() {
        this.scope = null;
        this.seenDisplays.clear();
        this.seenTranscriptions.clear();
        this.pending.clear();
        this.voiceEnabled = null;
    }

    /** Caller inserts into Minecraft chat on the game thread. Record the ID only after insertion returns. */
    public synchronized void receive(DialoguePayload.Snapshot snapshot, Consumer<String> insert) {
        if (!snapshot.scope().equals(this.scope)) {
            reset();
            this.scope = snapshot.scope();
        }
        this.voiceEnabled = snapshot.voiceEnabled();
        // The server retains a bounded snapshot; keep only its IDs so a long session stays bounded.
        Set<String> displays = new HashSet<>();
        Set<String> transcriptions = new HashSet<>();
        for (DialoguePayload.Row row : snapshot.rows()) {
            displays.add(row.displayId());
            transcriptions.add(row.turnId());
        }
        this.seenDisplays.retainAll(displays);
        this.seenTranscriptions.retainAll(transcriptions);
        this.pending.retainAll(displays);
        for (DialoguePayload.Row row : snapshot.rows()) {
            if ("cancelled".equals(row.playbackStatus())) continue;
            if ("voice".equals(row.source()) && !row.playerInput().isBlank() && !row.turnId().isBlank()
                    && !this.seenTranscriptions.contains(row.turnId())) {
                insert.accept("[聞き取り] " + row.playerInput());
                this.seenTranscriptions.add(row.turnId());
            }
            if (snapshot.textEnabled() && !row.displayId().isBlank() && !row.text().isBlank()
                    && !this.seenDisplays.contains(row.displayId())) {
                insert.accept("[ドギド] " + row.text());
                this.seenDisplays.add(row.displayId());
                this.pending.add(row.displayId());
            }
        }
    }

    public synchronized DialoguePayload.Scope scope() { return this.scope; }
    public synchronized Boolean voiceEnabled() { return this.voiceEnabled; }
    public synchronized List<String> pending() { return List.copyOf(this.pending); }

    public synchronized void acknowledge(DialoguePayload.Scope expected, DialoguePayload.Acknowledgement reply) {
        if (expected == null || !expected.equals(this.scope) || reply == null || !reply.accepted()) return;
        this.pending.removeAll(reply.displayed());
        this.pending.removeAll(reply.rejected());
    }

    public synchronized void voiceChanged(DialoguePayload.Reply reply) {
        this.voiceEnabled = reply.voiceEnabled();
    }
}
