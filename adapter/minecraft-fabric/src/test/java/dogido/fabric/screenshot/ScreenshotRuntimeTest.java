package dogido.fabric.screenshot;

import java.io.IOException;
import java.nio.file.*;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.*;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

/** 原寸の恒久保存・同秒衝突・symlink隔離をMinecraft描画から独立して検査する。 */
class ScreenshotRuntimeTest {
    @TempDir Path game;
    private final Instant captured=Instant.parse("2026-10-21T02:03:04Z");
    private String filename() {
        return "dogido_"+DateTimeFormatter.ofPattern("yyyy-MM-dd_HH.mm.ss",Locale.ROOT).withZone(ZoneId.systemDefault()).format(captured)+".png";
    }
    @Test void savesEveryPixelAtOriginalDimensionsWithoutAnnotationOrTemporaryRetention() throws Exception {
        int width=384,height=192;int[] pixels=new int[width*height];
        for(int i=0;i<pixels.length;i++)pixels[i]=0xff000000|((i*73)&0xffffff);
        var saved=ScreenshotRuntime.save(game,captured,width,height,pixels);
        Path path=Path.of(saved.directory()).resolve(saved.filename());
        assertEquals(game.resolve("screenshots").toRealPath(),path.getParent());assertEquals(filename(),saved.filename());
        var image=ImageIO.read(path.toFile());assertEquals(width,image.getWidth());assertEquals(height,image.getHeight());
        assertArrayEquals(pixels,image.getRGB(0,0,width,height,null,0,width));assertEquals(BodyViewFiles.hash(path),saved.sha256());
        assertFalse(Files.exists(game.resolve("dogido-vision")));
        assertEquals(0,BodyViewFiles.cleanup(game.resolve("screenshots"),captured.plusSeconds(90000)));assertTrue(Files.isRegularFile(path));
    }
    @Test void sameSecondCreatesSuffixAndKeepsExistingBytes() throws Exception {
        var first=ScreenshotRuntime.save(game,captured,1,1,new int[]{0xff123456});
        Path original=Path.of(first.directory()).resolve(first.filename());byte[] before=Files.readAllBytes(original);
        var second=ScreenshotRuntime.save(game,captured,1,1,new int[]{0xffabcdef});
        assertEquals(filename().replace(".png","_2.png"),second.filename());assertArrayEquals(before,Files.readAllBytes(original));
        assertNotEquals(first.sha256(),second.sha256());
    }
    @Test void concurrentSavesUseDistinctFilesWithoutOverwriting() throws Exception {
        try(var executor=Executors.newFixedThreadPool(4)) {
            List<Future<ScreenshotRuntime.Saved>> results=new ArrayList<>();
            for(int i=0;i<4;i++){final int color=i;results.add(executor.submit(()->ScreenshotRuntime.save(game,captured,1,1,new int[]{0xff000000|color})));}
            Set<String> names=new HashSet<>(),hashes=new HashSet<>();
            for(var future:results){var saved=future.get(5,TimeUnit.SECONDS);names.add(saved.filename());hashes.add(saved.sha256());}
            assertEquals(4,names.size());assertEquals(4,hashes.size());
        }
    }
    @Test void rejectsScreenshotDirectorySymlink() throws Exception {
        Path outside=Files.createDirectory(game.resolve("outside"));Files.createSymbolicLink(game.resolve("screenshots"),outside);
        assertThrows(IOException.class,()->ScreenshotRuntime.save(game,captured,1,1,new int[]{0xff123456}));
        try(var files=Files.list(outside)){assertEquals(0,files.count());}
    }
    @Test void existingFilenameSymlinkIsSkippedAndItsTargetIsUntouched() throws Exception {
        Path root=Files.createDirectory(game.resolve("screenshots")),personal=game.resolve("personal.png");Files.writeString(personal,"keep");
        Path link=Files.createSymbolicLink(root.resolve(filename()),personal);
        var saved=ScreenshotRuntime.save(game,captured,1,1,new int[]{0xff123456});
        assertEquals(filename().replace(".png","_2.png"),saved.filename());assertTrue(Files.isSymbolicLink(link));assertEquals("keep",Files.readString(personal));
    }
    @Test void invalidPixelsAndBlockedDirectoryNeverProduceSuccess() throws Exception {
        assertThrows(IllegalArgumentException.class,()->ScreenshotRuntime.save(game,captured,2,2,new int[3]));
        assertFalse(Files.exists(game.resolve("screenshots")));
        Files.writeString(game.resolve("screenshots"),"personal");
        assertThrows(IOException.class,()->ScreenshotRuntime.save(game,captured,1,1,new int[]{0xff123456}));
        assertEquals("personal",Files.readString(game.resolve("screenshots")));
    }
}
