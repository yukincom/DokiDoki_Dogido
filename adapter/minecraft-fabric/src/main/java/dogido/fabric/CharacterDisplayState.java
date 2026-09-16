package dogido.fabric;

/** Presentation only. A failed/stale connection never leaves Dogido thinking. */
final class CharacterDisplayState {
    private WorkshopDisplayState.Snapshot latest;
    private long lastReceivedMs;
    private long barrierSequence;
    private boolean localDanger;

    void reset() { latest = null; lastReceivedMs = 0; barrierSequence = 0; localDanger = false; }
    void synchronizeAfter(long sequence) { barrierSequence = Math.max(barrierSequence, sequence); }
    void danger(boolean danger, boolean changed, long sequence) {
        if (danger && (!localDanger || changed)) synchronizeAfter(sequence);
        localDanger = danger;
    }
    void receive(WorkshopDisplayState.Snapshot snapshot, long now) {
        if (snapshot == null) { latest = null; return; }
        if (latest != null && snapshot.sessionId().equals(latest.sessionId())
                && snapshot.revision() < latest.revision()) return;
        latest = snapshot;
        lastReceivedMs = now;
    }
    boolean thinking(long now) {
        return latest != null && now - lastReceivedMs <= 3000 && !localDanger
            && latest.observedSequence() >= barrierSequence && !latest.state().equals("danger")
            && latest.characterState().equals("thinking");
    }
}
