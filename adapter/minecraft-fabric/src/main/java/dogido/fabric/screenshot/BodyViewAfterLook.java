package dogido.fabric.screenshot;

import dogido.fabric.body.BodyConnection;
import dogido.fabric.body.BodyRequestProtocol;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import java.time.Instant;

/**
 * 明示LOOK_ATの成功IDを一枚の撮影へ束縛する。成功結果・入力世代はBodyRequestRuntimeが確認する。
 * 移動中も撮影でき、停止・姿勢安定・位置差・角度差を採否条件にしない。撮影後は過去の一枚として扱う。
 */
public final class BodyViewAfterLook {
    public static final class Proof {
        private final SessionRef session;
        private final BodyRequestProtocol.Command command;
        private final BodyConnection.LookRequest look;
        private final Instant executedAt;
        private String captureId;
        private Proof(SessionRef session,BodyRequestProtocol.Command command,BodyConnection.LookRequest look,Instant executedAt) {
            this.session=session;this.command=command;this.look=look;this.executedAt=executedAt;
        }
        private boolean accepts(BodyViewProtocol.Command capture,SessionRef current,Instant now) {
            var expected=capture.afterLook();
            return expected!=null && session.equals(current) && capture.sessionId().equals(session.sessionId())
                && command.commandId().equals(expected.commandId()) && command.inputGeneration()==expected.inputGeneration()
                && executedAt.equals(expected.executedAt()) && !capture.issuedAt().isBefore(executedAt)
                && !now.isBefore(executedAt) && capture.timeError(now)==null
                && capture.matches(look.context().json()) && (captureId==null || captureId.equals(capture.id()));
        }
    }
    private Proof latest;
    public void clear() { latest=null; }
    /** 同一action/sequenceの成功照合後、結果に使った時刻とidentityだけを記録する。 */
    public void completed(SessionRef session,BodyRequestProtocol.Command command,BodyConnection.LookRequest look,Instant executedAt) {
        clear();
        if(session==null || command.mode()==BodyRequestProtocol.Mode.RECALL || look==null
                || !command.commandId().equals(look.actionId()) || !command.context().equals(look.context())
                || executedAt.isBefore(command.issuedAt()) || !executedAt.isBefore(command.issuedAt().plusSeconds(35)))return;
        latest=new Proof(session,command,look,executedAt);
    }
    /** 受付一回で撮影IDへ束縛する。描画前・保存後の姿勢確認には利用しない。 */
    public Proof capture(BodyViewProtocol.Command command,SessionRef session,Instant now) {
        if(latest==null || !latest.accepts(command,session,now))return null;
        latest.captureId=command.id();return latest;
    }
}
