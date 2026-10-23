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
    /** 明示呼び寄せ専用。既存ActionRequestのcontext検証は緩めない。 */
    BodyProtocol.Submission requestRecall(BodyProtocol.RecallRequest request);
    Optional<BodyProtocol.RecallReceipt> recallReceipt(java.util.UUID actionId);
    /** 既存player観測の現在作業値を渡す。制御modeや行動を変更する要求ではない。 */
    BodyProtocol.Submission observeOwnerWork(BodyProtocol.OwnerWorkObservation observation);
    /** 再生中の対象へ向くための短期観測。移動命令と制御modeを変更しない。 */
    BodyProtocol.Submission observeSpeechGaze(BodyProtocol.SpeechGazeObservation observation);
}
