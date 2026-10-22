package dogido.lab.control;

import java.util.ArrayList;
import java.util.List;
import net.minecraft.util.math.Vec3d;

/** 呼び寄せ・初回出現・距離追従で共有する立ち位置候補。現在のowner位置・向き・既存の作業観測を受け、実空間の検査は呼出元へ委ねる。 */
public final class CallPlacement {
    private CallPlacement() {}

    /** 水平距離は指定上限より0.25block内側。到着の許容幅を含めても上限内へ収まり、作業中は横・後ろだけを使う。 */
    public static List<Vec3d> candidates(BodyController.Owner owner, double distance) {
        return candidates(owner,distance,owner.feet().add(0,owner.bodyHeight(),0));
    }
    /** 接近中は身体の現在位置を受け、上下だけが空く竪穴でownerを縦に横切らない方を先に調べる。 */
    public static List<Vec3d> candidates(BodyController.Owner owner, double distance, Vec3d from) {
        List<Vec3d> result = new ArrayList<>(owner.working() ? besideCandidates(owner,distance)
            : candidates(owner,distance,new float[]{0,45,-45,90,-90,135,-135,180}));
        result.addAll(fallbackCandidates(owner,distance,from));
        return result;
    }

    /** 普段の追従は横・後ろへ収まる。呼び寄せの作業中と同じ候補と高さを使い、正面へ割り込まない。 */
    public static List<Vec3d> besideCandidates(BodyController.Owner owner, double distance) {
        return candidates(owner,distance,new float[]{90,-90,135,-135,180});
    }

    /** 顔を見に行く候補は斜め前だけ。作業中や塞がった場所で正面へ割り込む代替は作らない。 */
    public static List<Vec3d> faceCandidates(BodyController.Owner owner, double distance) {
        return owner.working() ? List.of() : candidates(owner,distance,new float[]{45,-45});
    }

    /**
     * 通常の立ち位置が全て塞がれた時だけ使う有界候補。通路のワールド軸、狭い横／後ろ、最後に上下を調べる。
     * 上下は作業正面と重ならない高さ。owner・他生体・壁・危険・読み込み範囲の再検査は呼出し側が行う。
     */
    public static List<Vec3d> fallbackCandidates(BodyController.Owner owner, double distance) {
        return fallbackCandidates(owner,distance,owner.feet().add(0,owner.bodyHeight(),0));
    }
    public static List<Vec3d> fallbackCandidates(BodyController.Owner owner, double distance, Vec3d from) {
        List<Vec3d> result = new ArrayList<>();
        // 視点が斜めでも、1block幅のX/Z通路に沿った安全位置を候補から失わない。
        for (double radius : new double[]{distance-.25,1.1,.85}) {
            if (radius < distance-.25) {
                for (float turn : new float[]{90,-90,135,-135,180})
                    addHeights(result,owner,owner.yaw()+turn,radius);
            }
            for (float yaw : new float[]{0,90,180,270}) addHeights(result,owner,yaw,radius);
        }
        Vec3d above = owner.feet().add(0,2.75,0), below = owner.feet().add(0,-1.25,0);
        if (from.squaredDistanceTo(above) <= from.squaredDistanceTo(below)) { result.add(above); result.add(below); }
        else { result.add(below); result.add(above); }
        return result.stream().filter(p -> !BodyMotion.nearFront(p,owner.feet(),owner.yaw())).toList();
    }

    /** 既存の遠距離ワープ候補を先に保ち、通常時だけ狭所用の近い候補を最後に追加する。 */
    public static List<Vec3d> warpCandidates(BodyController.Owner owner, boolean retreat) {
        List<Vec3d> result = new ArrayList<>();
        for (double radius : new double[]{3,5,8,11})
            for (float turn : retreat ? new float[]{180,135,-135} : new float[]{45,-45,90,-90,135,-135,180})
                result.add(BodyMotion.offset(owner.feet(),owner.yaw()+turn,radius,0,owner.bodyHeight()));
        if (!retreat) {
            result.addAll(besideCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE));
            result.addAll(fallbackCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE));
        }
        return result;
    }

    private static List<Vec3d> candidates(BodyController.Owner owner, double distance, float[] turns) {
        List<Vec3d> result = new ArrayList<>();
        for (float turn : turns) for (double dy : new double[]{0,.6,-.6})
            result.add(BodyMotion.offset(owner.feet(),owner.yaw()+turn,distance-.25,0,Math.max(.1,owner.bodyHeight()+dy)));
        return result;
    }
    private static void addHeights(List<Vec3d> result, BodyController.Owner owner, float yaw, double radius) {
        for (double dy : new double[]{0,.6,-.6})
            result.add(BodyMotion.offset(owner.feet(),yaw,radius,0,Math.max(.1,owner.bodyHeight()+dy)));
    }
}
