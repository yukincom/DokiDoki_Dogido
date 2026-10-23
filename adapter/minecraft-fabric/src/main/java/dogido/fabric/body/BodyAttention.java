package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.lab.api.perception.BodyVisibility;
import java.util.List;
import java.util.UUID;
import java.util.function.Function;
import java.util.function.BooleanSupplier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import static dogido.fabric.body.BodyInspectProtocol.*;

/**
 * FOLLOWの注意範囲と、受理した観察対象への集中を同じ身体frameから決める。
 * 世界の視認・遮蔽はBodyOriginVision、移動と実到着後5秒の時計は身体Modが所有する。
 * この接続内の対象だけを保持し、到着通知の受信時刻から待機時間を積み増さない。
 */
final class BodyAttention {
    private Command focus;
    private long startedTick, sequence;
    private boolean acknowledged;
    private BooleanSupplier currentSession;

    /** 既存MOVE_TOが配送受付された時だけ呼ぶ。受付を到着済みとは扱わない。 */
    void focus(Command command, long tick, long sequence, BooleanSupplier currentSession) {
        focus=command; startedTick=tick; this.sequence=sequence; this.currentSession=currentSession; acknowledged=false;
    }
    void clear() { focus=null; currentSession=null; acknowledged=false; }
    /** adapter側の失効・配送失敗も同じ命令IDだけへ適用する。成功では実体側のdwell終了を待つ。 */
    boolean result(JsonObject result) {
        if (focus == null || !focus.commandId().toString().equals(string(result,"command_id"))
                || "succeeded".equals(string(result,"status"))) return false;
        clear(); return true;
    }

    /**
     * captureのキャッシュ参照より前に、同じframeのcontext・操作結果・優先行動へ照合する。
     * arrived後は身体自身のinspection_dwellだけを期限とし、失敗や別世代では即座に解除する。
     */
    boolean observe(JsonObject autonomy) {
        Command before=focus;
        update(autonomy);
        return before != focus;
    }
    private void update(JsonObject autonomy) {
        if (focus == null) return;
        if (!currentSession.getAsBoolean() || !following(autonomy)
                || !focus.context().equals(context(autonomy.getAsJsonObject("context")))) {
            clear(); return;
        }
        long tick=autonomy.get("server_tick").getAsLong();
        String activity=string(autonomy,"activity");
        // 配送上限5秒＋移動上限30秒＋実到着後5秒。通常は身体のreason変化で先に解除する。
        if (tick < startedTick || tick-startedTick >= 800 || autonomy.get("age_ms").getAsLong() >= 3_000
                || "follow_settling".equals(string(autonomy,"reason"))
                || !("holding".equals(activity) || "moving".equals(activity) || "exploring".equals(activity))) {
            clear(); return;
        }
        JsonObject result=autonomy.getAsJsonObject("action_result");
        if (result == null) return;
        if (!focus.commandId().toString().equals(string(result,"action_id"))) {
            if (acknowledged) clear();
            return;
        }
        if (result.get("sequence").getAsLong() != sequence) { clear(); return; }
        acknowledged=true;
        String outcome=string(result,"outcome");
        if ("accepted".equals(outcome)) return;
        if (!("succeeded".equals(outcome) && "arrived".equals(string(result,"reason"))
                && java.util.Set.of("inspection_dwell","owner_near_dwell","owner_face_dwell").contains(string(autonomy,"reason")))) clear();
    }

    /** FOLLOW以外は既存の視野を使う。追従・到着直後は前5・後1・左右1・上下2block。待機時は実測安心度から整数block単位で広げる。 */
    View view(Vec3d eye, double yaw, JsonObject autonomy) {
        if (!following(autonomy)) return null;
        if (focus != null) return new View(eye,yaw,focus.purpose() != Purpose.INSPECT,focus.target());
        boolean searching="holding".equals(string(autonomy,"activity"))
            && !"follow_settling".equals(string(autonomy,"reason"))
            && !java.util.Set.of("inspection_dwell","owner_near_dwell","owner_face_dwell").contains(string(autonomy,"reason"));
        double assurance=autonomy.has("reassurance") ? autonomy.getAsJsonObject("reassurance").get("value").getAsDouble() : 0;
        return new View(eye,yaw,searching ? assurance/100 : 0,null);
    }
    /** 同じframeで適用する注意範囲を、共通の変化テーブルへ添える。座標や未視認の対象は補わない。 */
    JsonObject observation(JsonObject autonomy) {
        View view=view(Vec3d.ZERO,0,autonomy);
        String scope=view == null ? "ordinary" : view.target() != null ? "target_only" : view.narrow() ? "narrow" : "ambient";
        var result=new JsonObject(); result.addProperty("scope",scope);
        result.addProperty("expansion",view == null || view.target() != null ? 1 : view.openness());
        return result;
    }
    private static boolean following(JsonObject row) {
        return row != null && "observed".equals(string(row,"status"))
            && row.get("enabled").getAsBoolean() && "follow".equals(string(row,"mode"));
    }

    /** 眼を中心とするblock単位の箱。yawに追従し、上下は世界の鉛直を保つ。対象集中時も遮蔽検査は省略しない。 */
    record View(Vec3d eye, double yaw, double openness, Target target) {
        View {
            openness=Double.isFinite(openness) ? Math.max(0,Math.min(1,openness)) : 0;
        }
        View(Vec3d eye,double yaw,boolean narrow,Target target) { this(eye,yaw,narrow ? 0 : 1,target); }
        boolean narrow() { return openness == 0; }
        /** 範囲に入る実測点だけへ既存の遮蔽判定を呼ぶ。遮蔽・未ロードは視認へ昇格させない。 */
        boolean visible(List<Vec3d> points, double range, Function<Vec3d,BodyVisibility.Trace> trace) {
            for (Vec3d point : points) {
                if (point.squaredDistanceTo(eye) <= range*range && contains(point)
                        && trace.apply(point) == BodyVisibility.Trace.CLEAR) return true;
            }
            return false;
        }
        boolean contains(Vec3d point) {
            if (point == null || !Double.isFinite(point.x) || !Double.isFinite(point.y) || !Double.isFinite(point.z)) return false;
            if (target != null) return true; // 個体/ブロックの一致をentity()/block()で先に検査する。
            Vec3d delta=point.subtract(eye);
            double angle=Math.toRadians(yaw);
            double forward=-Math.sin(angle)*delta.x+Math.cos(angle)*delta.z;
            double side=Math.cos(angle)*delta.x+Math.sin(angle)*delta.z;
            // 安心度からの各寸法を四捨五入する。追従中も前方と地面を拾えるよう前5・上下2を下限にする。
            long frontBlocks=Math.max(5,Math.round(1+7*openness));
            long sideBlocks=Math.round(1+openness);
            long verticalBlocks=Math.max(2,Math.round(1+2*openness));
            return forward >= -1-1e-9 && forward <= frontBlocks+1e-9
                && Math.abs(side) <= sideBlocks+1e-9 && Math.abs(delta.y) <= verticalBlocks+1e-9;
        }
        boolean entity(UUID id) { return target == null || "entity".equals(target.kind()) && id.equals(target.entityId()); }
        boolean block(BlockPos pos, String typeId) {
            return target == null || "block".equals(target.kind()) && pos != null
                && target.typeId().equals(typeId)
                && pos.equals(BlockPos.ofFloored(target.position().x(),target.position().y(),target.position().z()));
        }
    }
}
