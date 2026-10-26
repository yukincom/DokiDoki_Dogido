package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;

/**
 * Minecraftの向き指定を移動とは独立して保持する。身体が指定を適用したtickで一度だけ成功を返す。
 * 実ロボットの整定・姿勢誤差の検査は行わず、描画後の一枚はその時点の観測として扱う。
 */
public final class DirectedLook {
    public static final int HOLD_TICKS = 100;
    private UUID id;
    private Vec3d target;
    private long expires;
    private boolean completed;
    public boolean active() { return id != null; }
    public Vec3d target() { return target; }
    public void clear() { id=null;target=null;completed=false;expires=0; }
    /** 移動のmode・速度・追従先を変更せず、新しい注視点だけを置き換える。 */
    public void begin(UUID actionId,Position point,long now) {
        clear();id=actionId;target=new Vec3d(point.x(),point.y(),point.z());expires=now+HOLD_TICKS;
    }
    /** 同じ指定を保持する時間だけを管理する。追従・退避・他の身体操作では取り消さない。 */
    public void tick(long now) { if(completed && now>=expires)clear(); }
    /** 身体のyaw/pitchへ適用した直後に呼ぶ。角度差や停止状態を成功の追加条件にしない。 */
    public UUID applied(long now) {
        if(!active() || completed)return null;
        completed=true;expires=now+HOLD_TICKS;return id;
    }
}
