package dogido.lab.api;

import java.util.Optional;

/** Optional client entrypoint. Submit on the Minecraft client thread; snapshots are immutable. */
public interface BodyBridge {
    int protocolVersion();
    boolean available();
    Optional<BodyProtocol.Snapshot> snapshot();
    /** snapshotの受信からの経過ms。client threadでsnapshotと続けて読み、再送で鮮度を更新しない。 */
    long snapshotAgeMillis();
    BodyProtocol.Submission applyState(BodyProtocol.StateRequest request);
    BodyProtocol.Submission requestAction(BodyProtocol.ActionRequest request);
}
