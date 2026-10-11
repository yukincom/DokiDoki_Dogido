package dogido.fabric.horse_recall;

/**
 * 一回の呼戻しの段階を管理する。移動結果と見た目の完了は別で、演出取消から再移動へ戻らない。
 * client/serverの両スレッドから通知されるため、段階の変更はこの型の同期した入口だけで行う。
 */
public final class HorseRecallLifecycle {
    public enum Stage { ACCEPTED, PREVIEW, MOVED, BURST, FINISHED, CANCELLED }
    private Stage stage = Stage.ACCEPTED;
    private boolean resultRecorded;
    private int previewTicks;

    public synchronized Stage stage() { return stage; }
    public synchronized boolean startPreview() {
        if (stage != Stage.ACCEPTED) return false;
        stage = Stage.PREVIEW;
        return true;
    }

    /** clientで予兆開始後にserver tickを数える。10tick未満では移動を許可しない。 */
    public synchronized boolean previewTick() {
        return stage == Stage.PREVIEW && ++previewTicks >= 10;
    }

    public synchronized boolean moved() {
        if (stage != Stage.PREVIEW || previewTicks < 10) return false;
        stage = Stage.MOVED;
        return true;
    }

    /** 到着した馬をclientで確認した一度だけ、いななきと粒子散開を同時開始する。 */
    public synchronized boolean startBurst() {
        if (stage != Stage.MOVED) return false;
        stage = Stage.BURST;
        return true;
    }

    public synchronized boolean finish() {
        if (stage != Stage.BURST) return false;
        stage = Stage.FINISHED;
        return true;
    }

    public synchronized void cancel() {
        if (stage != Stage.FINISHED) stage = Stage.CANCELLED;
    }

    public synchronized boolean recordResult() {
        if (resultRecorded) return false;
        resultRecorded = true;
        return true;
    }
}
