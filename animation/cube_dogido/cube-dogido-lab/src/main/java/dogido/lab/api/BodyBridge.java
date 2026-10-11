package dogido.lab.api;

import java.util.Optional;

/** Optional client entrypoint. Submit on the Minecraft client thread; snapshots are immutable. */
public interface BodyBridge {
    int protocolVersion();
    boolean available();
    Optional<BodyProtocol.Snapshot> snapshot();
    BodyProtocol.Submission applyState(BodyProtocol.StateRequest request);
    BodyProtocol.Submission requestAction(BodyProtocol.ActionRequest request);
}
