package dogido.fabric.display;

import dogido.fabric.protocol.WorkshopDisplayPayload.Snapshot;
import java.util.Objects;

/** Pure presentation timeline for already parsed display snapshots. */
final class WorkshopDisplayState {

    private Snapshot latest;
    private Snapshot displayed;
    private long lastReceivedMs;
    private long barrierSequence;
    private boolean localDanger;
    private float from;
    private float target;
    private long transitionMs;

    void reset() {
        latest = null; displayed = null; lastReceivedMs = 0; barrierSequence = 0;
        localDanger = false; from = 0; target = 0; transitionMs = 0;
    }

    void synchronizeAfter(long sequence) { barrierSequence = Math.max(barrierSequence, sequence); hideNow(); }

    /** Returns a single edit-cue change, not a sound on each poll or reconnect. */
    boolean receive(Snapshot snapshot, long now) {
        if (snapshot == null) { unavailable(); return false; }
        if (latest != null && snapshot.sessionId().equals(latest.sessionId())
                && snapshot.revision() < latest.revision()) return false;
        boolean cue = latest != null && Objects.equals(latest.workshopId(), snapshot.workshopId())
            && snapshot.editing() && (!latest.editing() || !Objects.equals(latest.selectedLine(), snapshot.selectedLine()));
        latest = snapshot;
        lastReceivedMs = now;
        update(now);
        if (cue && target == 1) { from = 1; transitionMs = now; }
        return cue && target == 1;
    }

    void danger(boolean danger, boolean changed, long sequence, long now) {
        boolean entering = danger && !localDanger;
        localDanger = danger;
        if (danger && (entering || changed)) {
            // A new observation invalidates any older server permission to resume.
            barrierSequence = Math.max(barrierSequence, sequence); hideNow();
        }
        update(now);
    }

    void unavailable() { latest = null; hideNow(); }

    void update(long now) {
        if (latest == null || now - lastReceivedMs > 3000 || localDanger
                || latest.observedSequence() < barrierSequence || latest.state().equals("danger")) {
            hideNow();
            return;
        }
        boolean show = latest.state().equals("open");
        boolean newWorkshop = show && displayed != null
            && !Objects.equals(latest.workshopId(), displayed.workshopId());
        if (newWorkshop) hideNow();
        if (show) displayed = latest;
        float next = show ? 1 : 0;
        if (target != next) {
            from = opacity(now); target = next; transitionMs = now;
        }
    }

    float opacity(long now) {
        float progress = Math.clamp((now - transitionMs) / 1000f, 0, 1);
        float ease = target == 1 ? progress * progress * (3 - 2 * progress) : 1 - (1 - progress) * (1 - progress);
        return from + (target - from) * ease;
    }

    Snapshot displayed() { return displayed; }
    boolean showing() { return target == 1; }
    private void hideNow() { from = 0; target = 0; }
}
