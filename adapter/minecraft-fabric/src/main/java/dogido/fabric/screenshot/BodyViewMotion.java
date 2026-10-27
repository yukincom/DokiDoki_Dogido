package dogido.fabric.screenshot;

/** client tickの身体姿勢から、視点の変化量と静止中10秒ごとの観測更新を数える。単位はblockと度。
 * 通常観測は2 tick（通常100ms）の静止後、前回の観測基準から0.5 blockか10度変われば進める。
 * 小さな浮遊・向き直しでは基準を更新せず、徐々に進む変化は次の判定へ残す。
 * translationは旋回だけの連続を画像比較側へ伝える番号。往復して元の座標へ戻っても移動を失わない。
 */
final class BodyViewMotion {
    record Pose(double x,double y,double z,double yaw,double pitch) {
        double distance(Pose p) { return Math.sqrt(Math.pow(x-p.x,2)+Math.pow(y-p.y,2)+Math.pow(z-p.z,2)); }
        double angle(Pose p) { return Math.max(Math.abs(Math.IEEEremainder(yaw-p.yaw,360)),Math.abs(pitch-p.pitch)); }
    }
    private Object context;
    private Pose previous,anchor,translationAnchor;
    static final int REFRESH_TICKS = 200;
    static final double CAPTURE_DISTANCE = .5;
    static final double CAPTURE_ANGLE = 10;
    private int quiet, sinceCapture;
    private long sequence,translation;
    void clear() { context=null;previous=null;anchor=null;translationAnchor=null;quiet=sinceCapture=0; }
    void observe(Object key,Pose pose) {
        if (!key.equals(context)) {
            clear();context=key;previous=translationAnchor=pose;translation++;return;
        }
        if (pose.distance(translationAnchor)>=.02) { translation++;translationAnchor=pose; }
        boolean moving=pose.distance(previous)>.0001 || pose.angle(previous)>.05;
        quiet=moving?0:Math.min(quiet+1,2);previous=pose;
        sinceCapture++;
        // 経路の最大変化ではなく、停止した現在の視点を比較する。往復して戻れば追加撮影しない。
        boolean changed=anchor==null || pose.distance(anchor)>=CAPTURE_DISTANCE || pose.angle(anchor)>=CAPTURE_ANGLE;
        if (quiet==2 && (changed || sinceCapture>=REFRESH_TICKS)) {
            sequence++;anchor=pose;sinceCapture=0;
        }
    }
    boolean still(Pose pose) { return previous!=null && quiet==2 && pose.distance(previous)<=.0001 && pose.angle(previous)<=.05; }
    /** 明示注視の成功で一枚を撮る時だけ進める。同方向の再依頼でも一意になり、通常停止番号を巻き戻さない。 */
    long afterLookSequence(long after) {
        long previous=Math.max(sequence,after);
        if(previous==Long.MAX_VALUE)throw new IllegalStateException("motion_sequence_exhausted");
        sequence=previous+1;anchor=this.previous;sinceCapture=0;return sequence;
    }
    long sequence() { return sequence; }
    long translation() { return translation; }
}
