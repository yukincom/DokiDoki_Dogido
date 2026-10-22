package dogido.fabric.screenshot;

/**
 * 単発撮影の世代とGPU待機期限を所有する。時刻は呼出側の単調時計（ns）。
 * callback到着前は期限で解放し、到着後は保存完了まで保持する。遅れた旧callbackは新しい予約を完了できない。
 */
final class CaptureDeadline {
    private String current;
    private long deadline;
    private boolean saving;
    synchronized boolean busy() { return current!=null; }
    synchronized boolean start(String id,long expires) {
        if (current!=null) return false;
        current=id;deadline=expires;saving=false;return true;
    }
    synchronized String expire(long now) {
        if (current==null || saving || now<deadline) return null;
        String expired=current;current=null;return expired;
    }
    synchronized boolean claim(String id,long now) {
        if (!id.equals(current) || saving || now>=deadline) return false;
        saving=true;return true;
    }
    synchronized boolean finish(String id) {
        if (!id.equals(current)) return false;
        current=null;return true;
    }
}
