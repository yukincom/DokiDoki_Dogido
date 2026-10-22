package dogido.fabric.body;

import dogido.lab.api.BodyBridge;
import dogido.lab.api.BodyProtocol;
import dogido.lab.api.BodyProtocol.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** adapterの送信境界を模擬bridgeで確認する。現在値true/falseは同じ身体世代に送り、行動やモードを変更しない。 */
class BodyOwnerWorkTest {
    private static final class Bridge implements BodyBridge {
        public Submission observeSpeechGaze(SpeechGazeObservation value) { return Submission.QUEUED; }
        Snapshot value = BodyAutonomyJsonTest.snapshot();
        long age; boolean available = true;
        final List<OwnerWorkObservation> sent = new ArrayList<>();
        public int protocolVersion() { return BodyProtocol.VERSION; }
        public boolean available() { return available; }
        public Optional<Snapshot> snapshot() { return Optional.ofNullable(value); }
        public long snapshotAgeMillis() { return age; }
        public Submission observeOwnerWork(OwnerWorkObservation observation) { sent.add(observation); return Submission.QUEUED; }
        public Submission applyState(StateRequest request) { throw new AssertionError("観測でモードを変更しない"); }
        public Submission requestAction(ActionRequest request) { throw new AssertionError("観測で行動を開始しない"); }
    }
    @Test void workStartAndEndUseExistingSnapshotContextAndTime() {
        Bridge bridge = new Bridge();
        BodySnapshotReader.observeOwnerWork(bridge,true);
        BodySnapshotReader.observeOwnerWork(bridge,false);
        assertEquals(List.of(new OwnerWorkObservation(bridge.value.context(),bridge.value.serverTick(),true),
            new OwnerWorkObservation(bridge.value.context(),bridge.value.serverTick(),false)),bridge.sent);
    }
    @Test void unavailableOrOldFramesDoNotBecomeFreshOwnerWork() {
        Bridge bridge = new Bridge(); bridge.age = 751;
        BodySnapshotReader.observeOwnerWork(bridge,false); assertTrue(bridge.sent.isEmpty());
        bridge.age = 0; bridge.available = false;
        BodySnapshotReader.observeOwnerWork(bridge,false); assertTrue(bridge.sent.isEmpty());
        bridge.available = true;
        bridge.value = BodyObservationJsonTest.snapshot(Presence.UNLOADED,null,0);
        BodySnapshotReader.observeOwnerWork(bridge,false); assertTrue(bridge.sent.isEmpty());
        bridge.value = null;
        BodySnapshotReader.observeOwnerWork(bridge,false); assertTrue(bridge.sent.isEmpty());
    }
}
