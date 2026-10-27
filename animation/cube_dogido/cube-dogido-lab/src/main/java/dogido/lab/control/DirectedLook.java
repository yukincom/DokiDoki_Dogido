package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;

/**
 * 明示注視と呼び寄せの視線所有者を受理順に保持する。移動・到着通知の状態は所有しない。
 * LOOK_ATは適用tickで成功し、CALLの到着後は既存の回頭が終わるまで視線だけを引き継ぐ。
 */
public final class DirectedLook {
    public static final int HOLD_TICKS = 100;
    private enum Owner { NONE, EXPLICIT, CALL, CALL_ARRIVAL }
    private Owner owner = Owner.NONE;
    private UUID id;
    private Vec3d target;
    private long expires;
    private boolean completed;
    public boolean active() { return owner == Owner.EXPLICIT; }
    public boolean arriving() { return owner == Owner.CALL_ARRIVAL; }
    public Vec3d target() { return target; }
    public void clear() { owner=Owner.NONE;id=null;target=null;completed=false;expires=0; }
    /** 移動のmode・速度・追従先を変更せず、新しい注視点へ所有者を置き換える。 */
    public void begin(UUID actionId,Position point,long now) {
        clear();owner=Owner.EXPLICIT;id=actionId;target=new Vec3d(point.x(),point.y(),point.z());expires=now+HOLD_TICKS;
    }
    /** 新規受理したCALLだけが古い注視を解除する。未適用のIDは既存結果台帳で中断へ確定する。 */
    public UUID beginCall() {
        UUID pending=active() && !completed?id:null;
        clear();owner=Owner.CALL;return pending;
    }
    /** 到着時点でもCALLが視線を持つ場合だけ回頭へ進む。後のLOOKが失効しても旧CALLへ戻らない。 */
    public void arrived(Vec3d ownerEyes) {
        if(owner==Owner.CALL) { owner=Owner.CALL_ARRIVAL;target=ownerEyes; }
    }
    /** CALLの取消・新しい移動はその視線だけを解放し、後からの明示注視を残す。 */
    public void cancelCall() { if(owner==Owner.CALL || arriving())clear(); }
    /** 既存補間が向き終わったら通常注視へ戻す。移動成功や画像の成否には使わない。 */
    public void arrivalTurnFinished() { if(arriving())clear(); }
    /** 明示注視の保持時間を管理する。浮遊・その場の回頭・通常追従では取り消さない。 */
    public void tick(long now) { if(active() && completed && now>=expires)clear(); }
    /** 身体のyaw/pitchへ適用した直後に呼ぶ。角度差や停止状態を成功の追加条件にしない。 */
    public UUID applied(long now) {
        if(!active() || completed)return null;
        completed=true;expires=now+HOLD_TICKS;return id;
    }
}
