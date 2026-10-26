package dogido.fabric.screenshot;

import com.google.gson.JsonObject;
import dogido.fabric.body.BodyConnection;
import dogido.fabric.body.BodyInspectProtocol.Context;
import dogido.fabric.body.BodyInspectProtocol.Point;
import dogido.fabric.body.BodyRequestProtocol;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import dogido.lab.api.perception.BodyVisionGeometry;
import java.time.Instant;
import net.minecraft.util.math.Vec3d;

/**
 * 検証済みの明示LOOK_AT成功を撮影一件へ束縛する。入力・接続の現在性はBodyRequestRuntimeが所有する。
 * 注視保持中の同じactionと実姿勢だけを根拠にし、通常移動や別の向きの画像を成功後の一枚へ混ぜない。
 */
public final class BodyViewAfterLook {
    /** 描画時または同一snapshotの眼と角度。座標はblock、角度は度。 */
    public record Pose(Point eye,double yaw,double pitch) {
        public Pose {
            if(eye==null || !Double.isFinite(yaw) || !Double.isFinite(pitch) || Math.abs(pitch)>90)
                throw new IllegalArgumentException("look_pose");
        }
    }
    /** 外部から作れない成功根拠。captureIdへの束縛後も同じ不変インスタンスを現在性の照合に使う。 */
    public static final class Proof {
        private final SessionRef session;
        private final BodyRequestProtocol.Command command;
        private final BodyConnection.LookRequest look;
        private final Instant executedAt;
        private final Pose completed;
        private final long serverTick;
        private String captureId;
        private Proof(SessionRef session,BodyRequestProtocol.Command command,BodyConnection.LookRequest look,
                Instant executedAt,Pose completed,long serverTick) {
            this.session=session;this.command=command;this.look=look;this.executedAt=executedAt;
            this.completed=completed;this.serverTick=serverTick;
        }
        private boolean accepts(BodyViewProtocol.Command capture,SessionRef current,Instant now) {
            var expected=capture.afterLook();
            return expected!=null && session.equals(current) && capture.sessionId().equals(session.sessionId())
                && command.commandId().equals(expected.commandId()) && command.inputGeneration()==expected.inputGeneration()
                && executedAt.equals(expected.executedAt()) && !capture.issuedAt().isBefore(executedAt)
                && !now.isBefore(executedAt) && capture.timeError(now)==null
                && capture.matches(look.context().json()) && (captureId==null || captureId.equals(capture.id()));
        }
        /** 同じ成功結果と注視保持中の最新snapshotだけを使う。保持終了・再移動・新しいactionは不成立にする。 */
        public boolean matches(JsonObject sample,JsonObject autonomy) {
            try {
                JsonObject result=autonomy.getAsJsonObject("action_result");
                return sample.get("context").equals(look.context().json())
                    && sample.get("server_tick").getAsLong()>=serverTick
                    && "observed".equals(string(autonomy,"status")) && autonomy.get("enabled").getAsBoolean()
                    && "holding".equals(string(autonomy,"activity")) && "requested_look".equals(string(autonomy,"reason"))
                    && look.actionId().toString().equals(string(result,"action_id"))
                    && look.sequence()==result.get("sequence").getAsLong()
                    && "succeeded".equals(string(result,"outcome")) && "facing_target".equals(string(result,"reason"))
                    && facing(pose(sample));
            } catch(RuntimeException invalid) { return false; }
        }
        /**
         * serverの完了後、client補間が同じ注視点へ1度以内に追いつくのを待つ。
         * HOLDING中の上下浮遊は一周期の高低差0.36blockを許容するが、水平移動・大きな上下移動は許可しない。
         */
        public boolean facing(Pose current) {
            double dx=current.eye().x()-completed.eye().x(),dz=current.eye().z()-completed.eye().z();
            if(dx*dx+dz*dz>.02*.02 || Math.abs(current.eye().y()-completed.eye().y())>.4)return false;
            Point target=look.target();
            var angles=BodyVisionGeometry.facing(new Vec3d(target.x()-current.eye().x(),target.y()-current.eye().y(),
                target.z()-current.eye().z()),current.yaw(),current.pitch());
            return Math.abs(Math.IEEEremainder(angles.yaw()-current.yaw(),360))<=1
                && Math.abs(angles.pitch()-current.pitch())<=1;
        }
    }
    private Proof latest;
    /** 新しい明示操作を受けた時と接続終了時に破棄する。失敗した新操作の前の成功へ戻さない。 */
    public void clear() { latest=null; }
    /** BodyRequestRuntimeが同一action/sequenceの終端を照合した後、結果に使う同じ時刻で一度だけ記録する。 */
    public void completed(SessionRef session,BodyRequestProtocol.Command command,BodyConnection.LookRequest look,
            JsonObject sample,JsonObject autonomy,Instant executedAt) {
        clear();
        try {
            if(session==null || command.mode()==BodyRequestProtocol.Mode.RECALL || look==null
                    || !command.commandId().equals(look.actionId()) || !command.context().equals(look.context())
                    || executedAt.isBefore(command.issuedAt()) || !executedAt.isBefore(command.issuedAt().plusSeconds(35)))return;
            var proof=new Proof(session,command,look,executedAt,pose(sample),sample.get("server_tick").getAsLong());
            if(proof.matches(sample,autonomy))latest=proof;
        } catch(RuntimeException invalid) { }
    }
    /** 別撮影IDで同じ注視結果を再利用しない。発行済み一件の継続検査では同じProofを返す。 */
    public Proof capture(BodyViewProtocol.Command command,SessionRef session,Instant now) {
        if(latest==null || !latest.accepts(command,session,now))return null;
        latest.captureId=command.id();return latest;
    }
    private static Pose pose(JsonObject sample) {
        JsonObject value=sample.getAsJsonObject("self_pose"),eye=value.getAsJsonObject("eye_position");
        return new Pose(new Point(eye.get("x").getAsDouble(),eye.get("y").getAsDouble(),eye.get("z").getAsDouble()),
            value.get("view_yaw").getAsDouble(),value.get("view_pitch").getAsDouble());
    }
    private static String string(JsonObject value,String key) { return value.get(key).getAsString(); }
}
