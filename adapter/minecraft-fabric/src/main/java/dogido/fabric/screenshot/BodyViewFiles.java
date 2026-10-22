package dogido.fabric.screenshot;

import com.google.gson.*;
import java.io.IOException;
import java.nio.file.*;
import java.security.*;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Set;

/** 診断専用フォルダのJSON・hash・画像保持期限。通常screenshotsや句の画像へ触れず、IO workerだけが呼ぶ。 */
public final class BodyViewFiles {
    private static final Gson JSON=new GsonBuilder().setPrettyPrinting().create();
    private static final Set<String> IMAGE_NAMES=Set.of("frame.png","player-192.png");
    private BodyViewFiles() {}

    public static String hash(Path path) throws IOException {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path))); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    /** 完了通知は画像保存・hash計算の後に公開し、読取側が途中のJSONを観測しないよう置換する。 */
    public static void json(Path path,JsonObject value) throws IOException {
        Path temporary=path.resolveSibling(path.getFileName()+".tmp");
        Files.writeString(temporary,JSON.toJson(value)+"\n",StandardOpenOption.CREATE,StandardOpenOption.TRUNCATE_EXISTING);
        Files.move(temporary,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
    }
    public static JsonObject image(String name,String hash) {
        JsonObject row=new JsonObject(); row.addProperty("filename",name); row.addProperty("sha256",hash); return row;
    }

    /** Python側と共有するmkdir排他。相手のlockは回収せず、異常終了で残った場合も画像を保守的に保持する。 */
    public static RetentionLock tryLock(Path directory) throws IOException {
        Path lock=directory.resolve("retention.lock");
        try { Files.createDirectory(lock);return new RetentionLock(lock); }
        catch (FileAlreadyExistsException occupied) { return null; }
    }
    public record RetentionLock(Path path) implements AutoCloseable {
        @Override public void close() throws IOException { Files.delete(path); }
    }

    /** 作成直後の専用directoryに限る。公開記録が存在すれば触らず、予約された一時ファイルだけを回収する。 */
    public static void discardUnpublished(Path directory) throws IOException {
        if (Files.exists(directory.resolve("frame.json"),LinkOption.NOFOLLOW_LINKS)) return;
        for (String name:Set.of("frame.png","player-192.png","retention.json","retention.json.tmp","frame.json.tmp"))
            Files.deleteIfExists(directory.resolve(name));
        try { Files.delete(directory); }
        catch (DirectoryNotEmptyException addedFile) { /* 外部から追加されたファイルはそのまま保持。 */ }
    }

    /** ACKで確定した最終使用から24時間を保持。共有lock下で更新し、逆順のACKでは期限を短縮しない。 */
    public static boolean markUsed(Path directory,Instant usedAt) throws IOException {
        if(!BodyViewProtocol.captureId(directory.getFileName().toString()) || Files.isSymbolicLink(directory)
                || !Files.isDirectory(directory,LinkOption.NOFOLLOW_LINKS))return true;
        Path manifest=directory.resolve("retention.json");
        if(!Files.isRegularFile(manifest,LinkOption.NOFOLLOW_LINKS) || Files.size(manifest)>65536)return true;
        try(RetentionLock lock=tryLock(directory)) {
            if(lock==null)return false;
            JsonObject data=JsonParser.parseString(Files.readString(manifest)).getAsJsonObject();
            if(data.get("schema_version").getAsInt()!=1 || data.has("deleted_at"))return true;
            Instant previous=data.has("last_used_at")?Instant.parse(data.get("last_used_at").getAsString()):Instant.MIN;
            if(usedAt.isBefore(previous))return true;
            data.addProperty("last_used_at",usedAt.toString());data.addProperty("expires_at",usedAt.plusSeconds(86400).toString());
            json(manifest,data);return true;
        }
    }

    /**
     * 起動時・稼働中に24時間期限を確認する。全hashとmanifestの再一致を先に確認し、変更画像・symlinkは削除しない。
     * モデル送信側は利用前後に同じretention.jsonの期限を更新する。停止中の期限切れは次回起動時に整理する。
     */
    public static int cleanup(Path captures,Instant now) throws IOException {
        if (!Files.isDirectory(captures,LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(captures)) return 0;
        int removed=0;
        try (var directories=Files.list(captures)) {
            for (Path directory:directories.toList()) {
                if (!directory.getFileName().toString().matches("view_[a-f0-9-]{36}")
                        || !Files.isDirectory(directory,LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(directory)) continue;
                Path manifest=directory.resolve("retention.json");
                if (!Files.isRegularFile(manifest,LinkOption.NOFOLLOW_LINKS) || Files.size(manifest)>65536) continue;
                try (RetentionLock lock=tryLock(directory)) {
                    if (lock==null) continue;
                    String before=Files.readString(manifest);
                    JsonObject data=JsonParser.parseString(before).getAsJsonObject();
                    if (data.get("schema_version").getAsInt()!=1 || data.has("deleted_at")
                            || now.isBefore(Instant.parse(data.get("expires_at").getAsString()))) continue;
                    JsonArray images=data.getAsJsonArray("images");
                    if (images==null || images.isEmpty() || images.size()>2) continue;
                    java.util.ArrayList<Path> targets=new java.util.ArrayList<>();
                    boolean matches=true;
                    for (var element:images) {
                        var image=element.getAsJsonObject(); String name=image.get("filename").getAsString();
                        Path target=directory.resolve(name);
                        if (!IMAGE_NAMES.contains(name) || targets.contains(target) || Files.isSymbolicLink(target)
                                || !Files.isRegularFile(target,LinkOption.NOFOLLOW_LINKS) || Files.size(target)>64L*1024*1024
                                || !hash(target).equals(image.get("sha256").getAsString())) { matches=false; break; }
                        targets.add(target);
                    }
                    if (!matches || !Files.readString(manifest).equals(before)) continue;
                    for (Path target:targets) { Files.delete(target); removed++; }
                    data.addProperty("deleted_at",now.toString()); json(manifest,data);
                } catch (RuntimeException ignored) { /* 不正な記録を他の画像の削除へ広げない。 */ }
            }
        }
        return removed;
    }
}
