package dogido.fabric.screenshot;

/** client tickの身体姿勢から「動いた後に止まった」を一度だけ数える。単位はblockと度。
 * 2 tick（通常100ms）の静止を確認し、細かな補間中やyawの±180度境界では余分な撮影を作らない。
 * translationは旋回だけの連続を画像比較側へ伝える番号。往復して元の座標へ戻っても移動を失わない。
 */
final class BodyViewMotion {
    record Pose(double x,double y,double z,double yaw,double pitch) {
        double distance(Pose p) { return Math.sqrt(Math.pow(x-p.x,2)+Math.pow(y-p.y,2)+Math.pow(z-p.z,2)); }
        double angle(Pose p) { return Math.max(Math.abs(Math.IEEEremainder(yaw-p.yaw,360)),Math.abs(pitch-p.pitch)); }
    }
    private Object context;
    private Pose previous,anchor,translationAnchor;
    private int quiet;
    private boolean dirty;
    private long sequence,translation;
    void clear() { context=null;previous=null;anchor=null;translationAnchor=null;quiet=0;dirty=false; }
    void observe(Object key,Pose pose) {
        if (!key.equals(context)) {
            clear();context=key;previous=anchor=translationAnchor=pose;dirty=true;translation++;return;
        }
        if (pose.distance(translationAnchor)>=.02) { translation++;translationAnchor=pose; }
        if (pose.distance(anchor)>=.02 || pose.angle(anchor)>=.5) dirty=true;
        boolean moving=pose.distance(previous)>.0001 || pose.angle(previous)>.05;
        quiet=moving?0:Math.min(quiet+1,2);previous=pose;
        if (quiet==2 && dirty) { sequence++;dirty=false;anchor=pose; }
    }
    boolean still(Pose pose) { return previous!=null && quiet==2 && pose.distance(previous)<=.0001 && pose.angle(previous)<=.05; }
    long sequence() { return sequence; }
    long translation() { return translation; }
}
