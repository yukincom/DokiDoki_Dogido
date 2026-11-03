package dogido.fabric.screenshot;

import com.sun.jna.*;
import java.io.*;
import java.nio.file.*;

/** macOSのJDKはSecureDirectoryStreamを提供しない。Minecraft同梱JNAで同じdirfd相対操作だけを行う。 */
final class MacPoemDirectory implements PoemImageFile.Directory {
    private interface Lib extends Library {
        int open(String path,int flags);
        int openat(int fd,String name,int flags);
        int unlinkat(int fd,String name,int flags);
        int fgetattrlist(int fd,Pointer fields,Pointer result,long size,long options);
        int close(int fd);
    }
    private static final Lib LIB=Native.load("c",Lib.class);
    // Darwin <sys/fcntl.h>。NOFOLLOW_ANYはディレクトリまでの全要素に適用。未対応OSでは失敗時に保持する。
    private static final int NOFOLLOW_ANY=0x20000000,DIRECTORY=0x00100000,CLOEXEC=0x01000000,NOFOLLOW=0x100,NONBLOCK=4;
    private final int fd;
    MacPoemDirectory(Path dir) throws IOException {
        this.fd=checked(LIB.open(dir.toString(),DIRECTORY|NOFOLLOW_ANY|CLOEXEC));
    }
    private static int checked(int fd) throws IOException {
        if (fd>=0) return fd;
        int errno=Native.getLastError();
        if (errno==2) throw new NoSuchFileException("missing_entry");
        throw new IOException("darwin_directory_io_"+errno);
    }
    // fgetattrlistの4バイト境界の返却順: 総長、dev、vnode種別、64bit inode（Darwin <sys/attr.h>）。
    private static String key(int fd,int expectedType) throws IOException {
        try (Memory fields=new Memory(24); Memory result=new Memory(20)) {
            fields.clear();fields.setShort(0,(short)5);fields.setInt(4,0x0200000a); // DEVID | OBJTYPE | FILEID
            checked(LIB.fgetattrlist(fd,fields,result,result.size(),0));
            if (result.getInt(0)!=20 || result.getInt(8)!=expectedType) throw new IOException("image_changed");
            return "(dev="+Integer.toHexString(result.getInt(4))+",ino="+result.getLong(12)+")";
        }
    }
    private int file(Path name) throws IOException {
        return checked(LIB.openat(fd,PoemImageFile.leaf(name),CLOEXEC|NOFOLLOW|NONBLOCK));
    }
    public String key() throws IOException { return key(fd,2); }
    public String fileKey(Path name) throws IOException {
        int child=file(name);
        try { return key(child,1); } finally { LIB.close(child); }
    }
    public InputStream read(Path name,String expected) throws IOException {
        int child=file(name);
        try {
            String actual=key(child,1);
            if (expected!=null && !actual.equals(expected)) throw new IOException("image_changed");
            // Darwinの/dev/fd/Nのopenは今検査したfdの複製。ハッシュのために元のファイル名を再度開かない。
            return new FileInputStream("/dev/fd/"+child);
        } finally { LIB.close(child); }
    }
    public void deleteFile(Path name) throws IOException { checked(LIB.unlinkat(fd,PoemImageFile.leaf(name),0)); }
    public void close() { LIB.close(fd); }
}
