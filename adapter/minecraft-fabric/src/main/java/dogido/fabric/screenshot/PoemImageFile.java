package dogido.fabric.screenshot;

import com.google.gson.JsonObject;
import java.io.*;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributeView;
import java.util.Arrays;
import java.util.Set;
import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.READ;

/** 削除はパスの再解決をせず、開いたディレクトリの同じentryに対して照合・実行する。 */
final class PoemImageFile {
    private PoemImageFile() {}
    interface Directory extends Closeable {
        String key() throws IOException;
        String fileKey(Path name) throws IOException;
        InputStream read(Path name,String expected) throws IOException;
        void deleteFile(Path name) throws IOException;
    }
    static String leaf(Path name) throws IOException {
        if (name.isAbsolute() || name.getNameCount()!=1 || name.toString().isEmpty()
                || name.toString().equals(".") || name.toString().equals("..")) throw new IOException("invalid_image_name");
        return name.toString();
    }
    @SuppressWarnings("unchecked")
    static Directory open(Path dir) throws IOException {
        Path absolute=dir.toAbsolutePath().normalize();
        var root=Files.newDirectoryStream(absolute.getRoot());
        if (!(root instanceof SecureDirectoryStream<?>)) {
            root.close();
            if (System.getProperty("os.name","").startsWith("Mac")) {
                try { return new MacPoemDirectory(absolute); }
                catch (LinkageError unavailable) { throw new IOException("secure_unlink_unavailable",unavailable); }
            }
            throw new IOException("secure_unlink_unavailable");
        }
        SecureDirectoryStream<Path> cursor=(SecureDirectoryStream<Path>)root;
        try {
            for (Path part:absolute) {
                var next=cursor.newDirectoryStream(part,NOFOLLOW_LINKS);
                cursor.close(); cursor=next;
            }
            var handle=cursor;
            return new Directory() {
                public String key() throws IOException {
                    return String.valueOf(handle.getFileAttributeView(BasicFileAttributeView.class).readAttributes().fileKey());
                }
                public String fileKey(Path name) throws IOException {
                    leaf(name);
                    var attrs=handle.getFileAttributeView(name,BasicFileAttributeView.class,NOFOLLOW_LINKS).readAttributes();
                    if (!attrs.isRegularFile() || attrs.fileKey()==null) throw new IOException("image_changed");
                    return attrs.fileKey().toString();
                }
                public InputStream read(Path name,String expected) throws IOException {
                    leaf(name);
                    if (expected!=null && !fileKey(name).equals(expected)) throw new IOException("image_changed");
                    return Channels.newInputStream(handle.newByteChannel(name,Set.of(READ,NOFOLLOW_LINKS)));
                }
                public void deleteFile(Path name) throws IOException { leaf(name);handle.deleteFile(name); }
                public void close() throws IOException { handle.close(); }
            };
        } catch (IOException | RuntimeException failure) { cursor.close();throw failure; }
    }

    static boolean published(Path shots,JsonObject state) throws IOException {
        if (!state.has("file_key")) return false;
        Path file=Path.of(state.get("filename").getAsString());
        try (var dir=open(shots)) {
            if (state.has("directory_key") && !dir.key().equals(state.get("directory_key").getAsString())) return false;
            String key=state.get("file_key").getAsString();
            try (var in=dir.read(file,key)) {
                return ScreenshotRuntime.hash(in).equals(state.get("sha256").getAsString()) && dir.fileKey(file).equals(key);
            }
        } catch (NoSuchFileException absent) { return false; }
    }
    static void remove(Path shots,JsonObject state,Path stage) throws IOException {
        try (var dir=open(shots)) { remove(dir,state,stage); }
    }
    static void remove(Directory dir,JsonObject state,Path stage) throws IOException {
        Path file=Path.of(state.get("filename").getAsString());
        if (state.has("directory_key") && !dir.key().equals(state.get("directory_key").getAsString()))
            throw new IOException("image_directory_changed");
        String key;
        try { key=dir.fileKey(file); } catch (NoSuchFileException absent) { return; }
        if (!state.has("file_key") || !key.equals(state.get("file_key").getAsString())) throw new IOException("image_changed");
        String expected=state.get("sha256").getAsString();
        String observed;
        try (var fileIn=dir.read(file,key)) { observed=ScreenshotRuntime.hash(fileIn); }
        if (!observed.equals(expected)) {
            if (!state.has("copying") || !prefixMatches(dir,file,key,stage,expected)) throw new IOException("image_changed");
            state.addProperty("partial_sha256",observed);
            PoemCaptureJobs.store(stage.getParent(),state);
        }
        try (var verify=dir.read(file,key)) {
            if (!ScreenshotRuntime.hash(verify).equals(observed)) throw new IOException("image_changed");
        }
        if (!dir.fileKey(file).equals(key)) throw new IOException("image_changed");
        dir.deleteFile(file);
    }
    // CREATE_NEW後にcopyが中断した場合だけ、閉じた同一inodeが元PNGの完全な先頭部分か照合する。
    private static boolean prefixMatches(Directory publicDir,Path name,String key,Path stage,String expected) throws IOException {
        try (var privateDir=open(stage.getParent());var source=privateDir.read(stage.getFileName(),null)) {
            if (!ScreenshotRuntime.hash(source).equals(expected)) return false;
            try (var src=privateDir.read(stage.getFileName(),null);var partial=publicDir.read(name,key)) {
                byte[] a=new byte[8192],b=new byte[8192];
                for (int n;(n=partial.readNBytes(a,0,a.length))>0;)
                    if (src.readNBytes(b,0,n)!=n || !Arrays.equals(a,0,n,b,0,n)) return false;
                return true;
            }
        }
    }
    static void syncDirectory(Path dir) {
        try (var channel=FileChannel.open(dir,READ)) { channel.force(true); }
        catch (IOException | UnsupportedOperationException cannotSyncDirectory) {
            // Java/FSがdirectoryの同期を提供しない場合も、ファイル自身のforceは必須。
        }
    }
}
