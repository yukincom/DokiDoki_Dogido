package dogido.fabric.screenshot;

import dogido.fabric.protocol.DogidoEventClient;
import com.google.gson.JsonObject;
import java.util.UUID;
import java.util.List;
import java.util.function.Consumer;
import java.util.function.Supplier;
import java.io.IOException;
import java.nio.file.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;

/**
 * 句集用の身体視点原寸画像。描画・camera復帰はBodyViewCaptureへ委ね、保存完了したファイルだけ既存結果台帳へ返す。
 * 標準screenshotsへdogido_日時.pngを新規作成し、会話画像の24時間retentionには登録しない。
 */
public final class ScreenshotRuntime {
    private static final long MAX_BYTES=64L*1024*1024;
    public record Saved(String filename,String directory,String sha256,JsonObject receipt) {}
    private final Supplier<Path> game;
    private final Consumer<Runnable> io;
    public ScreenshotRuntime() { this(BodyViewCapture::gameDirectory,BodyViewCapture::onIo); }
    ScreenshotRuntime(Supplier<Path> game,Consumer<Runnable> io) { this.game=game;this.io=io; }

    public void rememberSession(String session,UUID owner,DogidoEventClient events) {
        if (owner==null) return;
        io.accept(()->{
            try {
                PoemCaptureJobs.rememberSession(game.get(),session,owner);
                for (JsonObject receipt:PoemCaptureJobs.pendingReceipts(game.get(),owner))
                    events.rememberOwnedReceipt(owner,receipt);
            }
            catch (IOException | RuntimeException failure) {
                org.slf4j.LoggerFactory.getLogger("dogido-screenshot").warn("Failed to record screenshot session owner",failure);
            }
        });
    }

    public void acknowledge(UUID owner,List<JsonObject> receipts,DogidoEventClient events) {
        if (owner==null || receipts.isEmpty()) return;
        io.accept(()->{
            for (JsonObject receipt:receipts) {
                try {
                    if (PoemCaptureJobs.acknowledgeReceipt(game.get(),owner,receipt)) events.forgetOwnedReceipt(owner,receipt);
                } catch (IOException | RuntimeException failure) {
                    org.slf4j.LoggerFactory.getLogger("dogido-screenshot").warn("Screenshot receipt ACK could not be stored",failure);
                }
            }
        });
    }

    public void execute(ScreenshotProtocol.Command command,DogidoEventClient.SessionRef ref,DogidoEventClient events) {
        if (ref==null || !command.sessionId().equals(ref.sessionId())) return;
        io.accept(()->{
            if (!events.isCurrentOwner(ref,command.ownerId())) {
                events.rememberScreenshotResult(ref,ScreenshotProtocol.result(command,"rejected","stale_owner_or_session",Instant.now()));
                return;
            }
            try {
                PoemCaptureJobs.reserve(game.get(),command);
                BodyViewCapture.request(command,()->events.isCurrent(ref),result->{
                    if (!"succeeded".equals(result.get("status").getAsString())) { events.rememberScreenshotResult(ref,result);return; }
                    // 遅延したGPU完了通知は、この間に新接続で受領確認済みなら再登録しない。
                    io.accept(()->{
                        try {
                            if (PoemCaptureJobs.isPendingReceipt(game.get(),command.ownerId(),result))
                                events.rememberOwnedReceipt(command.ownerId(),result);
                        } catch (IOException | RuntimeException failure) {
                            org.slf4j.LoggerFactory.getLogger("dogido-screenshot").warn("Pending screenshot receipt retained",failure);
                        }
                    });
                });
            } catch (IOException | RuntimeException failure) {
                events.rememberScreenshotResult(ref,ScreenshotProtocol.result(command,"failed","capture_job_unavailable",Instant.now()));
            }
        });
    }

    public void dispose(ScreenshotProtocol.Disposition command,DogidoEventClient.SessionRef ref,DogidoEventClient events) {
        io.accept(()->{
            String status="succeeded",detail;
            try {
                if (!events.isCurrentOwner(ref,command.owner())) throw new IOException("stale_owner_or_session");
                detail=PoemCaptureJobs.decide(game.get(),command,ref.sessionId(),command.owner());
            } catch (IOException | RuntimeException failure) {
                status="failed";
                detail=switch (String.valueOf(failure.getMessage())) {
                    case "decision_conflict","image_changed","receipt_mismatch","stale_owner_or_session","unknown_capture" -> failure.getMessage();
                    default -> "capture_job_unavailable";
                };
            }
            events.rememberDispositionResult(ref,ScreenshotProtocol.result(command,status,detail));
        });
    }

    static String hash(Path target) throws IOException {
        try (var input=Files.newInputStream(target,LinkOption.NOFOLLOW_LINKS)) { return hash(input); }
    }
    static String hash(java.io.InputStream input) throws IOException {
        try {
            MessageDigest digest=MessageDigest.getInstance("SHA-256");long total=0;byte[] buffer=new byte[8192];
            for(int count;(count=input.read(buffer))>=0;) {
                total+=count;if(total>MAX_BYTES)throw new IOException("image_too_large");
                digest.update(buffer,0,count);
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch(NoSuchAlgorithmException impossible) {throw new IllegalStateException(impossible);}
    }
}
