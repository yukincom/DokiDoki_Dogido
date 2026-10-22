package dogido.fabric.screenshot;

import dogido.fabric.protocol.DogidoEventClient;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.Locale;
import javax.imageio.ImageIO;

/**
 * 句集用の身体視点原寸画像。描画・camera復帰はBodyViewCaptureへ委ね、保存完了したファイルだけ既存結果台帳へ返す。
 * 標準screenshotsへdogido_日時.pngを新規作成し、会話画像の24時間retentionには登録しない。
 */
public final class ScreenshotRuntime {
    private static final long MAX_BYTES=64L*1024*1024;
    public record Saved(String filename,String directory,String sha256) {}

    public void execute(ScreenshotProtocol.Command command,DogidoEventClient.SessionRef ref,DogidoEventClient events) {
        if(ref==null || !command.sessionId().equals(ref.sessionId()))return;
        BodyViewCapture.request(command,()->events.isCurrent(ref),result->events.rememberScreenshotResult(ref,result));
    }

    /**
     * GPUから受け取ったARGBを原寸・無注釈のまま保存する。CREATE_NEWとNOFOLLOW_LINKSで同名画像とsymlinkを上書きしない。
     * 日時は撮影frameの時刻をMinecraft端末の現地時刻へ変換する。同秒の衝突は_2、_3の順で避ける。
     */
    static Saved save(Path gameDirectory,Instant capturedAt,int width,int height,int[] argb) throws IOException {
        if(width<1 || height<1 || (long)width*height>16_777_216L || argb.length!=(long)width*height)
            throw new IllegalArgumentException("invalid_image_size");
        Path game=gameDirectory.toAbsolutePath().normalize();
        if(Files.isSymbolicLink(game) || !Files.isDirectory(game,LinkOption.NOFOLLOW_LINKS))throw new IOException("invalid_game_directory");
        Path root=game.toRealPath().resolve("screenshots");
        try { Files.createDirectory(root); } catch(FileAlreadyExistsException exists) { }
        if(Files.isSymbolicLink(root) || !Files.isDirectory(root,LinkOption.NOFOLLOW_LINKS))throw new IOException("invalid_screenshot_directory");
        Path actualRoot=root.toRealPath();Object directoryKey=Files.readAttributes(root,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey();
        BufferedImage raw=new BufferedImage(width,height,BufferedImage.TYPE_INT_ARGB);raw.setRGB(0,0,width,height,argb,0,width);
        String stem="dogido_"+DateTimeFormatter.ofPattern("yyyy-MM-dd_HH.mm.ss",Locale.ROOT).withZone(ZoneId.systemDefault()).format(capturedAt);
        for(int attempt=1;attempt<=10000;attempt++) {
            verifyDirectory(root,actualRoot,directoryKey);
            Path target=root.resolve(stem+(attempt==1?"":"_"+attempt)+".png");
            java.io.OutputStream output;
            try {output=Files.newOutputStream(target,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS);}
            catch(FileAlreadyExistsException collision) {continue;}
            Object fileKey=null;
            try {
                try(output) {
                    fileKey=Files.readAttributes(target,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey();
                    if(!ImageIO.write(raw,"png",output))throw new IOException("png_encoder_unavailable");
                }
                verifyDirectory(root,actualRoot,directoryKey);
                var attributes=Files.readAttributes(target,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
                if(!attributes.isRegularFile() || attributes.size()>MAX_BYTES || (fileKey!=null && !fileKey.equals(attributes.fileKey())))
                    throw new IOException("invalid_saved_image");
                return new Saved(target.getFileName().toString(),actualRoot.toString(),hash(target));
            } catch(IOException | RuntimeException error) {
                // 自分が作成した未完了ファイルだけ回収し、保存中に他者が差し替えた画像は触らない。
                try {
                    if(fileKey!=null && !Files.isSymbolicLink(target)
                            && fileKey.equals(Files.readAttributes(target,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey()))Files.delete(target);
                } catch(IOException cleanup) {error.addSuppressed(cleanup);}
                throw error;
            }
        }
        throw new IOException("screenshot_name_exhausted");
    }
    private static void verifyDirectory(Path root,Path actualRoot,Object key) throws IOException {
        var attributes=Files.readAttributes(root,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
        if(!attributes.isDirectory() || Files.isSymbolicLink(root) || !root.toRealPath().equals(actualRoot)
                || (key!=null && !key.equals(attributes.fileKey())))throw new IOException("screenshot_directory_changed");
    }
    private static String hash(Path target) throws IOException {
        try {
            MessageDigest digest=MessageDigest.getInstance("SHA-256");long total=0;byte[] buffer=new byte[8192];
            try(var input=Files.newInputStream(target,LinkOption.NOFOLLOW_LINKS)) {
                for(int count;(count=input.read(buffer))>=0;) {total+=count;if(total>MAX_BYTES)throw new IOException("image_too_large");digest.update(buffer,0,count);}
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch(NoSuchAlgorithmException impossible) {throw new IllegalStateException(impossible);}
    }
}
