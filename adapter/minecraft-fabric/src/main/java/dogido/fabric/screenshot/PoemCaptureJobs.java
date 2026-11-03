package dogido.fabric.screenshot;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.*;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.ByteBuffer;
import java.nio.file.attribute.BasicFileAttributes;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.imageio.ImageIO;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.*;

/** 一つのshotだけが所有するPNGの台帳。端末で作ったファイル名、実体の鍵とハッシュだけで決着する。 */
final class PoemCaptureJobs {
    private PoemCaptureJobs() {}
    private static final DateTimeFormatter NAME = DateTimeFormatter.ofPattern("yyyy-MM-dd_HH.mm.ss",Locale.ROOT)
        .withZone(ZoneId.systemDefault());

    static synchronized void reserve(Path game,ScreenshotProtocol.Command c) throws IOException {
        Path dir=job(game,c.id());
        Files.createDirectory(dir); // 再送・再起動でも同じshotの画像を作り直さない。
        store(dir,identity(c.id(),c.sessionId(),c.ownerId()));
    }

    private static JsonObject identity(String shot,String sid,UUID owner) {
        JsonObject state=new JsonObject();
        state.addProperty("capture_command_id",shot); state.addProperty("source_session_id",sid);
        state.addProperty("owner_uuid",owner.toString());
        return state;
    }

    // 一度も撮影を受領しないまま切断することがある。旧sessionの未作成決着にも端末自身のowner記録を使う。
    static synchronized void rememberSession(Path game,String sid,UUID owner) throws IOException {
        Path dir=sessionDirectory(game,sid);
        if (Files.exists(dir,NOFOLLOW_LINKS)) {
            if (!sessionOwned(game,sid,owner)) throw new IOException("session_owner_mismatch");
            return;
        }
        Files.createDirectory(dir);
        JsonObject state=new JsonObject();
        state.addProperty("session_id",sid); state.addProperty("owner_uuid",owner.toString());
        store(dir,state);
    }
    private static Path sessionDirectory(Path game,String sid) throws IOException {
        String key=UUID.nameUUIDFromBytes(sid.getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString();
        return directory(jobsRoot(game).resolve("_sessions"),true).resolve(key);
    }
    private static boolean sessionOwned(Path game,String sid,UUID owner) throws IOException {
        Path dir=sessionDirectory(game,sid);
        if (Files.notExists(dir,NOFOLLOW_LINKS)) return false;
        JsonObject state=metadata(dir);
        return value(state,"session_id").equals(sid) && value(state,"owner_uuid").equals(owner.toString());
    }

    /** 保存と処分は同じIOキュー。データを閉じてshaと識別情報を永続化してから、screenshotsへ公開する。 */
    static synchronized ScreenshotRuntime.Saved save(Path game,ScreenshotProtocol.Command c,Instant time,
                                                      int width,int height,int[] pixels,JsonObject captureView) throws IOException {
        if (width<1 || height<1 || (long)width*height>16_777_216L || pixels.length!=(long)width*height)
            throw new IllegalArgumentException("invalid_image_size");
        Path dir=job(game,c.id());
        JsonObject state=read(dir,c.id(),c.sessionId(),c.ownerId());
        if ("discard".equals(value(state,"action")) || state.has("filename")) throw new IOException("shot_settled");
        Path shots=directory(game.toRealPath().resolve("screenshots"),true);
        BufferedImage raw=new BufferedImage(width,height,BufferedImage.TYPE_INT_ARGB);
        raw.setRGB(0,0,width,height,pixels,0,width);
        Path stage=dir.resolve("frame.part");
        try (var channel=FileChannel.open(stage,CREATE_NEW,WRITE,NOFOLLOW_LINKS)) {
            if (!ImageIO.write(raw,"png",Channels.newOutputStream(channel))) throw new IOException("png_encoder_unavailable");
            channel.force(true);
        }
        PoemImageFile.syncDirectory(dir);
        Object directoryKey=attrs(shots).fileKey();
        if (directoryKey!=null) state.addProperty("directory_key",directoryKey.toString());
        String sha=ScreenshotRuntime.hash(stage);
        var stageAttrs=attrs(stage);
        String stem="dogido_"+NAME.format(time);
        for (int i=1;i<=10000;i++) {
            String name=stem+(i==1?"":"_"+i)+".png";
            Path target=shots.resolve(name);
            if (Files.exists(target,NOFOLLOW_LINKS)) continue;
            state.addProperty("filename",name); state.addProperty("sha256",sha);
            JsonObject receipt=ScreenshotProtocol.result(c,"succeeded","saved",Instant.now());
            receipt.addProperty("filename",name);receipt.addProperty("directory",shots.toString());receipt.addProperty("sha256",sha);
            receipt.addProperty("captured_at",time.toString());receipt.add("capture_view",captureView.deepCopy());
            state.add("receipt",receipt);
            setKey(state,stageAttrs);
            store(dir,state); // この直後にプロセスが止まっても対象のPNGと期待shaが分かる。
            try {
                Files.createLink(target,stage);
            } catch (FileAlreadyExistsException collision) { continue; }
            catch (UnsupportedOperationException | FileSystemException noLink) {
                // hardlink不可のMinecraft領域でも原寸撮影は継続する。生成した対象の実体の鍵を先に記録する。
                FileChannel out;
                try { out=FileChannel.open(target,CREATE_NEW,WRITE,NOFOLLOW_LINKS); }
                catch (FileAlreadyExistsException collision) { continue; }
                try (out) {
                    setKey(state,attrs(target));
                    state.addProperty("copying",true);store(dir,state);
                    try (var in=Files.newInputStream(stage,NOFOLLOW_LINKS)) { in.transferTo(Channels.newOutputStream(out)); }
                    out.force(true);
                } catch (IOException | RuntimeException failure) {
                    try { PoemImageFile.remove(shots,state,stage); }
                    catch (IOException changed) { failure.addSuppressed(changed); }
                    throw failure;
                }
                state.remove("copying");store(dir,state);
            }
            if (!sha.equals(ScreenshotRuntime.hash(target))) throw new IOException("image_changed");
            PoemImageFile.syncDirectory(shots);
            state.addProperty("receipt_published",true);store(dir,state);
            try { clearStage(dir,stage); }
            catch (IOException cleanup) {
                org.slf4j.LoggerFactory.getLogger("dogido-screenshot").warn("Screenshot saved; private staging file retained",cleanup);
            }
            return new ScreenshotRuntime.Saved(name,shots.toString(),sha,receipt);
        }
        throw new IOException("screenshot_name_exhausted");
    }

    /** 成功resultは元sessionのまま届ける。保存済みのkeep/discardは配送確認を兼ねない。 */
    static synchronized List<JsonObject> pendingReceipts(Path game,UUID owner) throws IOException {
        var results=new ArrayList<JsonObject>();
        try (var jobs=Files.newDirectoryStream(jobsRoot(game),"shot_*")) {
            for (Path dir:jobs) {
                try {
                    String shot=dir.getFileName().toString();
                    if (!ScreenshotProtocol.shotId(shot) || !Files.isDirectory(dir,NOFOLLOW_LINKS)) continue;
                    JsonObject state=metadata(dir);
                    if (!shot.equals(value(state,"capture_command_id")) || !owner.toString().equals(value(state,"owner_uuid"))
                            || !state.has("receipt") || state.has("receipt_acknowledged")) continue;
                    JsonObject receipt=state.getAsJsonObject("receipt");
                    if (!"succeeded".equals(value(receipt,"status")) || !"capture_screenshot".equals(value(receipt,"command_type"))
                            || !game.toRealPath().resolve("screenshots").toString().equals(value(receipt,"directory"))
                            || !value(receipt,"poem_id").startsWith("session:"+value(state,"source_session_id")+":")
                            || !shot.equals(value(receipt,"command_id")) || !value(state,"source_session_id").equals(value(receipt,"session_id"))
                            || !owner.toString().equals(value(receipt.getAsJsonObject("capture_view"),"owner_uuid"))
                            || !value(state,"filename").equals(value(receipt,"filename")) || !value(state,"sha256").equals(value(receipt,"sha256"))) continue;
                    if (!state.has("receipt_published")) {
                        // 公開直後・成功記録の確定直前の停止だけを復元する。部分PNGや衝突先から成功を推測しない。
                        Path shots=game.toRealPath().resolve("screenshots");
                        if (!PoemImageFile.published(shots,state)) continue;
                        state.addProperty("receipt_published",true);store(dir,state);
                    }
                    results.add(receipt.deepCopy());
                } catch (IOException | RuntimeException bad) {
                    org.slf4j.LoggerFactory.getLogger("dogido-screenshot").warn("Unconfirmed screenshot receipt retained: {}",dir.getFileName(),bad);
                }
            }
        }
        return results;
    }
    static synchronized boolean isPendingReceipt(Path game,UUID owner,JsonObject receipt) throws IOException {
        String shot=value(receipt,"command_id");
        JsonObject state=read(job(game,shot),shot,value(receipt,"session_id"),owner);
        return state.has("receipt_published") && !state.has("receipt_acknowledged") && receipt.equals(state.get("receipt"));
    }
    static synchronized boolean acknowledgeReceipt(Path game,UUID owner,JsonObject receipt) throws IOException {
        String shot=value(receipt,"command_id");
        Path dir=job(game,shot);
        JsonObject state=read(dir,shot,value(receipt,"session_id"),owner);
        if (!state.has("receipt_published") || !receipt.equals(state.get("receipt"))) return false;
        if (!state.has("receipt_acknowledged")) {
            state.addProperty("receipt_acknowledged",true);store(dir,state);
        }
        return true;
    }

    static synchronized String decide(Path game,ScreenshotProtocol.Disposition d,String currentSession,UUID currentOwner)
            throws IOException {
        if (!d.sessionId().equals(currentSession) || !d.owner().equals(currentOwner)) throw new IOException("stale_owner_or_session");
        if (!d.action().equals("discard") && !d.action().equals("keep")) throw new IOException("unknown_action");
        Path dir=job(game,d.captureId());
        if (Files.notExists(dir,NOFOLLOW_LINKS)) {
            if (!d.action().equals("discard")) throw new IOException("unknown_capture");
            if (!d.sourceSessionId().equals(currentSession) && !sessionOwned(game,d.sourceSessionId(),currentOwner))
                throw new IOException("receipt_mismatch");
            // 固定shotの墓標だけを作り、Minecraft画像ディレクトリを探索しない。
            JsonObject tombstone=identity(d.captureId(),d.sourceSessionId(),currentOwner);
            tombstone.addProperty("action","discard"); tombstone.addProperty("decision_id",d.decisionId());
            tombstone.addProperty("not_created",true); tombstone.addProperty("discard_complete",true);
            Files.createDirectory(dir);
            store(dir,tombstone);
            return "not_created";
        }
        JsonObject state=read(dir,d.captureId(),d.sourceSessionId(),d.owner());
        String prior=value(state,"action");
        if (!prior.isEmpty() && (!prior.equals(d.action()) || !d.decisionId().equals(value(state,"decision_id"))))
            throw new IOException("decision_conflict");
        if (state.has("not_created")) return "not_created";
        if (prior.isEmpty()) {
            state.addProperty("action",d.action()); state.addProperty("decision_id",d.decisionId());
            store(dir,state); // keepは以後discardへ変えない。unlinkの途中で停止したら同じ決定で再開。
        }
        if ("discard".equals(d.action()) && state.has("filename") && !state.has("discard_complete")) {
            String name=value(state,"filename"),sha=value(state,"sha256");
            if (!name.matches("dogido_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}\\.[0-9]{2}\\.[0-9]{2}(?:_[0-9]+)?\\.png")
                    || !sha.matches("[a-f0-9]{64}")) throw new IOException("invalid_receipt");
            Path shots=directory(game.toRealPath().resolve("screenshots"),false);
            PoemImageFile.remove(shots,state,dir.resolve("frame.part"));
            PoemImageFile.syncDirectory(shots);
        }
        clearStage(dir,dir.resolve("frame.part"));
        if ("discard".equals(d.action()) && !state.has("discard_complete")) {
            state.addProperty("discard_complete",true); store(dir,state);
        }
        return d.action().equals("discard")?"discarded":"kept";
    }

    private static void clearStage(Path dir,Path stage) throws IOException {
        if (Files.notExists(stage,NOFOLLOW_LINKS)) return;
        // この一時名は検証済みjobのprivateな作業領域にのみ存在する。
        try (var handle=PoemImageFile.open(dir)) { handle.deleteFile(stage.getFileName()); }
        catch (IOException unsupported) {
            if (!"secure_unlink_unavailable".equals(unsupported.getMessage())) throw unsupported;
            // 非対応FSでは公開画像も作業ファイルも保持する。保存済みPNGの受領は返せる。
        }
    }
    private static void setKey(JsonObject state,BasicFileAttributes attrs) {
        state.remove("file_key");
        if (attrs.fileKey()!=null) state.addProperty("file_key",attrs.fileKey().toString());
    }
    private static BasicFileAttributes attrs(Path file) throws IOException {
        return Files.readAttributes(file,BasicFileAttributes.class,NOFOLLOW_LINKS);
    }
    private static String value(JsonObject state,String key) {
        return state.has(key)?state.get(key).getAsString():"";
    }
    private static JsonObject read(Path dir,String shot,String sid,UUID owner) throws IOException {
        JsonObject state=metadata(dir);
        if (!value(state,"capture_command_id").equals(shot) || !value(state,"source_session_id").equals(sid)
                || !value(state,"owner_uuid").equals(owner.toString())) throw new IOException("receipt_mismatch");
        return state;
    }
    private static JsonObject metadata(Path dir) throws IOException {
        directory(dir,false);
        Path meta=dir.resolve("state.json");
        if (!Files.isRegularFile(meta,NOFOLLOW_LINKS)) throw new IOException("missing_receipt");
        try (var reader=new java.io.InputStreamReader(Files.newInputStream(meta,NOFOLLOW_LINKS),java.nio.charset.StandardCharsets.UTF_8)) {
            return JsonParser.parseReader(reader).getAsJsonObject();
        }
    }
    private static Path directory(Path path,boolean create) throws IOException {
        if (create) { try { Files.createDirectory(path); } catch (FileAlreadyExistsException exists) {} }
        if (!Files.isDirectory(path,NOFOLLOW_LINKS)) throw new IOException("invalid_capture_directory");
        return path;
    }
    private static Path job(Path game,String id) throws IOException {
        if (!ScreenshotProtocol.shotId(id)) throw new IOException("invalid_shot_id");
        return jobsRoot(game).resolve(id);
    }
    private static Path jobsRoot(Path game) throws IOException {
        Path root=directory(game,false).toRealPath();
        return directory(directory(root.resolve("dogido-vision"),true).resolve("poem-capture-jobs"),true);
    }
    static void store(Path dir,JsonObject state) throws IOException {
        Path tmp=Files.createTempFile(dir,"state-",".tmp");
        try {
            try (var channel=FileChannel.open(tmp,WRITE,TRUNCATE_EXISTING,NOFOLLOW_LINKS)) {
                var bytes=ByteBuffer.wrap(state.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
                while (bytes.hasRemaining()) channel.write(bytes);
                channel.force(true);
            }
            Files.move(tmp,dir.resolve("state.json"),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
            PoemImageFile.syncDirectory(dir);PoemImageFile.syncDirectory(dir.getParent());
        } finally { Files.deleteIfExists(tmp); }
    }
}
