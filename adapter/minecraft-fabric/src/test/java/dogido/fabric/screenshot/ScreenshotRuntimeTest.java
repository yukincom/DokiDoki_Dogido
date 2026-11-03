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
    private static final UUID OWNER=UUID.fromString("c7491b4b-fba1-4d50-ac26-73b7f268f151");
    private ScreenshotProtocol.Command shot() throws IOException {
        var c=new ScreenshotProtocol.Command("shot_"+UUID.randomUUID(),"origin","session:origin:capture:job","minecraft:overworld",
            captured,captured.plusSeconds(10),UUID.randomUUID(),UUID.randomUUID(),OWNER);
        PoemCaptureJobs.reserve(game,c);
        return c;
    }
    private ScreenshotRuntime.Saved save(Path dir,Instant time,int width,int height,int[] pixels) throws IOException {
        return publish(dir,shot(),time,width,height,pixels);
    }
    private ScreenshotRuntime.Saved publish(Path dir,ScreenshotProtocol.Command c,Instant time,int width,int height,int[] pixels) throws IOException {
        var context=new com.google.gson.JsonObject();context.addProperty("body_id",c.bodyId().toString());
        context.addProperty("epoch",c.epoch().toString());context.addProperty("owner_id",c.ownerId().toString());context.addProperty("dimension",c.dimension());
        return PoemCaptureJobs.save(dir,c,time,width,height,pixels,ScreenshotProtocol.captureView(c,context,true));
    }
    private String filename() {
        return "dogido_"+DateTimeFormatter.ofPattern("yyyy-MM-dd_HH.mm.ss",Locale.ROOT).withZone(ZoneId.systemDefault()).format(captured)+".png";
    }
    @Test void savesEveryPixelAtOriginalDimensionsWithoutAnnotationOrTemporaryRetention() throws Exception {
        int width=384,height=192;int[] pixels=new int[width*height];
        for(int i=0;i<pixels.length;i++)pixels[i]=0xff000000|((i*73)&0xffffff);
        var saved=save(game,captured,width,height,pixels);
        Path path=Path.of(saved.directory()).resolve(saved.filename());
        assertEquals(game.resolve("screenshots").toRealPath(),path.getParent());assertEquals(filename(),saved.filename());
        var image=ImageIO.read(path.toFile());assertEquals(width,image.getWidth());assertEquals(height,image.getHeight());
        assertArrayEquals(pixels,image.getRGB(0,0,width,height,null,0,width));assertEquals(BodyViewFiles.hash(path),saved.sha256());
        assertTrue(Files.isDirectory(game.resolve("dogido-vision/poem-capture-jobs")));
        assertEquals(0,BodyViewFiles.cleanup(game.resolve("screenshots"),captured.plusSeconds(90000)));assertTrue(Files.isRegularFile(path));
    }
    @Test void sameSecondCreatesSuffixAndKeepsExistingBytes() throws Exception {
        var first=save(game,captured,1,1,new int[]{0xff123456});
        Path original=Path.of(first.directory()).resolve(first.filename());byte[] before=Files.readAllBytes(original);
        var second=save(game,captured,1,1,new int[]{0xffabcdef});
        assertEquals(filename().replace(".png","_2.png"),second.filename());assertArrayEquals(before,Files.readAllBytes(original));
        assertNotEquals(first.sha256(),second.sha256());
    }
    @Test void concurrentSavesUseDistinctFilesWithoutOverwriting() throws Exception {
        try(var executor=Executors.newFixedThreadPool(4)) {
            List<Future<ScreenshotRuntime.Saved>> results=new ArrayList<>();
            for(int i=0;i<4;i++){final int color=i;results.add(executor.submit(()->save(game,captured,1,1,new int[]{0xff000000|color})));}
            Set<String> names=new HashSet<>(),hashes=new HashSet<>();
            for(var future:results){var saved=future.get(5,TimeUnit.SECONDS);names.add(saved.filename());hashes.add(saved.sha256());}
            assertEquals(4,names.size());assertEquals(4,hashes.size());
        }
    }
    @Test void rejectsScreenshotDirectorySymlink() throws Exception {
        Path outside=Files.createDirectory(game.resolve("outside"));Files.createSymbolicLink(game.resolve("screenshots"),outside);
        assertThrows(IOException.class,()->save(game,captured,1,1,new int[]{0xff123456}));
        try(var files=Files.list(outside)){assertEquals(0,files.count());}
    }
    @Test void existingFilenameSymlinkIsSkippedAndItsTargetIsUntouched() throws Exception {
        Path root=Files.createDirectory(game.resolve("screenshots")),personal=game.resolve("personal.png");Files.writeString(personal,"keep");
        Path link=Files.createSymbolicLink(root.resolve(filename()),personal);
        var saved=save(game,captured,1,1,new int[]{0xff123456});
        assertEquals(filename().replace(".png","_2.png"),saved.filename());assertTrue(Files.isSymbolicLink(link));assertEquals("keep",Files.readString(personal));
    }
    @Test void invalidPixelsAndBlockedDirectoryNeverProduceSuccess() throws Exception {
        assertThrows(IllegalArgumentException.class,()->save(game,captured,2,2,new int[3]));
        assertFalse(Files.exists(game.resolve("screenshots")));
        Files.writeString(game.resolve("screenshots"),"personal");
        assertThrows(IOException.class,()->save(game,captured,1,1,new int[]{0xff123456}));
        assertEquals("personal",Files.readString(game.resolve("screenshots")));
    }
    private ScreenshotProtocol.Disposition decision(ScreenshotProtocol.Command shot,String action) {
        return new ScreenshotProtocol.Disposition(UUID.randomUUID().toString(),"new",shot.sessionId(),OWNER,
            shot.id(),action,UUID.randomUUID().toString());
    }

    // 見込み: 切断後は同じ本人の新sessionで元shotを決着できる。合格: SHA一致の元PNGだけを消し、再送でも再作成した同名PNGを保持。一時FSの実保存。
    @Test void oldSessionDispositionDeletesOnlyItsOwnImageOnce() throws Exception {
        var shot=shot();
        var saved=publish(game,shot,captured,1,1,new int[]{0xff123456});
        var other=save(game,captured,1,1,new int[]{0xff987654});
        Path png=game.resolve("screenshots").resolve(saved.filename());
        var d=decision(shot,"discard");
        assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,d,"other",OWNER));
        assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,d,"new",UUID.randomUUID()));
        var wrongOrigin=new ScreenshotProtocol.Disposition(d.id(),"new","wrong",OWNER,d.captureId(),d.action(),d.decisionId());
        assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,wrongOrigin,"new",OWNER));
        assertTrue(Files.exists(png));
        assertEquals("discarded",PoemCaptureJobs.decide(game,d,"new",OWNER));
        assertFalse(Files.exists(png));
        Files.writeString(png,"new user photo");
        assertEquals("discarded",PoemCaptureJobs.decide(game,d,"new",OWNER));
        assertEquals("new user photo",Files.readString(png));
        assertEquals(other.sha256(),BodyViewFiles.hash(game.resolve("screenshots").resolve(other.filename())));
    }

    // 見込み: 同じパスでも別実体・内容なら保護。合格: 同一バイトの別inodeへの交換と、元inodeの内容変更の両方で削除拒否。一時FSの実写像。
    @Test void replacementAndChangedBytesSurviveDiscard() throws Exception {
        for (boolean replace : List.of(true,false)) {
            var shot=shot();
            var saved=publish(game,shot,captured,1,1,new int[]{0xff123456});
            Path path=game.resolve("screenshots").resolve(saved.filename());
            if (replace) {
                Path moved=Files.move(path,game.resolve("old.png"));
                Files.copy(moved,path);
            } else Files.writeString(path,"modified");
            assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,decision(shot,"discard"),"new",OWNER));
            assertTrue(Files.isRegularFile(path));
        }
    }

    // 見込み: 採用は永続で、撮影前の取消は遅延writerを止める。合格: keep後のdiscardは失敗、先着discard後はPNG生成を拒否。GPUは模擬。
    @Test void keepWinsAndPreCaptureDiscardPreventsLaterPublication() throws Exception {
        var kept=shot(); var keep=decision(kept,"keep");
        assertEquals("kept",PoemCaptureJobs.decide(game,keep,"new",OWNER));
        var saved=publish(game,kept,captured,1,1,new int[]{0xff123456});
        assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,decision(kept,"discard"),"new",OWNER));
        assertTrue(Files.exists(game.resolve("screenshots").resolve(saved.filename())));
        var cancelled=shot();
        assertEquals("discarded",PoemCaptureJobs.decide(game,decision(cancelled,"discard"),"new",OWNER));
        assertThrows(IOException.class,()->publish(game,cancelled,captured,1,1,new int[]{0xff654321}));
    }
    // 見込み: IO writer使用中に届いたdiscardは保留。合格: writer開放前は処分せず、close後は正しいPNGだけ削除。実IOキュー、GPUと通信は模擬。
    @Test void discardWaitsBehindWriterAndCompletesOnlyAfterPublication() throws Exception {
        var shot=shot();var d=decision(shot,"discard");
        var started=new CountDownLatch(1);var allow=new CountDownLatch(1);
        var wrote=new CompletableFuture<ScreenshotRuntime.Saved>();var disposed=new CompletableFuture<String>();
        BodyViewCapture.onIo(()->{
            started.countDown();
            try {
                if (!allow.await(5,TimeUnit.SECONDS)) throw new IOException("test timeout");
                wrote.complete(publish(game,shot,captured,1,1,new int[]{0xffabcdef}));
            } catch (Exception e) { wrote.completeExceptionally(e); }
        });
        try {
            assertTrue(started.await(5,TimeUnit.SECONDS));
            BodyViewCapture.onIo(()->{
                try { disposed.complete(PoemCaptureJobs.decide(game,d,"new",OWNER)); }
                catch (Exception e) { disposed.completeExceptionally(e); }
            });
            assertThrows(TimeoutException.class,()->disposed.get(100,TimeUnit.MILLISECONDS));
        } finally { allow.countDown(); }
        var name=wrote.get(5,TimeUnit.SECONDS).filename();
        assertEquals("discarded",disposed.get(5,TimeUnit.SECONDS));
        assertTrue(Files.notExists(game.resolve("screenshots").resolve(name)));
    }
    // 見込み: 取消が撮影より先でも配送は決着。合格: 対象shotの墓標だけ作り、再送と遅延予約でもPNGなし。現在/旧sessionの本人照合を一時FSで検査。
    @Test void earlyDiscardCreatesOnlyItsTombstoneAcrossAuthorizedSessions() throws Exception {
        var oldOwner=UUID.randomUUID(); PoemCaptureJobs.rememberSession(game,"foreign",oldOwner);
        PoemCaptureJobs.rememberSession(game,"former",OWNER);
        for (String source : List.of("current","former","foreign","unseen")) {
            var shot=new ScreenshotProtocol.Command("shot_"+UUID.randomUUID(),source,"session:"+source+":capture:job",
                "minecraft:overworld",captured,captured.plusSeconds(10),UUID.randomUUID(),UUID.randomUUID(),OWNER);
            var d=new ScreenshotProtocol.Disposition(UUID.randomUUID().toString(),"current",source,OWNER,
                shot.id(),"discard","decision-1");
            if (source.equals("foreign") || source.equals("unseen")) {
                assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,d,"current",OWNER));
                assertTrue(Files.notExists(game.resolve("dogido-vision/poem-capture-jobs").resolve(shot.id())));
            } else {
                assertEquals("not_created",PoemCaptureJobs.decide(game,d,"current",OWNER));
                assertEquals("not_created",PoemCaptureJobs.decide(game,d,"current",OWNER));
                assertThrows(IOException.class,()->PoemCaptureJobs.reserve(game,shot));
            }
        }
        assertTrue(Files.notExists(game.resolve("screenshots")));
    }
    // 見込み: 照合後にscreenshotsを外部へのリンクへ差し替えても外部を削除しない。合格: 開いた元dir内だけunlink。同じinodeを外部にも置く実FS試験。
    @Test void unlinkRemainsRelativeToOpenedDirectoryAcrossParentReplacement() throws Exception {
        var shot=shot();var saved=publish(game,shot,captured,1,1,new int[]{0xffabcdef});
        Path shots=game.toRealPath().resolve("screenshots"),outside=Files.createDirectory(game.resolve("personal"));
        Path job=game.toRealPath().resolve("dogido-vision/poem-capture-jobs").resolve(shot.id());
        var state=com.google.gson.JsonParser.parseString(Files.readString(job.resolve("state.json"))).getAsJsonObject();
        var external=Files.createLink(outside.resolve(saved.filename()),shots.resolve(saved.filename()));
        try (var handle=PoemImageFile.open(shots)) {
            Path original=Files.move(shots,game.resolve("original-screenshots"));
            Files.createSymbolicLink(shots,outside);
            PoemImageFile.remove(handle,state,job.resolve("frame.part"));
            assertTrue(Files.notExists(original.resolve(saved.filename())));
        }
        assertEquals(saved.sha256(),ScreenshotRuntime.hash(external));
    }

    // 見込み: fallback copy中断は閉じた同一inode＋元PNGの先頭一致だけ回収。合格: 正しい部分PNGのみ削除してSHAを記録、違う内容を保持。中断点を実FSで再現。
    @Test void incompleteCopyIsRecoveredOnlyWhenItsClosedBytesMatchTheSourcePrefix() throws Exception {
        for (boolean match : List.of(true,false)) {
            var shot=shot(); var saved=publish(game,shot,captured,1,1,new int[]{0xff123456});
            Path job=game.toRealPath().resolve("dogido-vision/poem-capture-jobs").resolve(shot.id());
            Path image=Path.of(saved.directory()).resolve(saved.filename());
            Path meta=job.resolve("state.json");
            Files.copy(image,job.resolve("frame.part"));
            byte[] prefix=Arrays.copyOf(Files.readAllBytes(image),20);
            if (!match) prefix[10]^=1;
            Files.write(image,prefix);
            String partialHash=ScreenshotRuntime.hash(image);
            var state=com.google.gson.JsonParser.parseString(Files.readString(meta)).getAsJsonObject();
            state.addProperty("copying",true); PoemCaptureJobs.store(job,state);
            var d=decision(shot,"discard");
            if (match) {
                assertEquals("discarded",PoemCaptureJobs.decide(game,d,"new",OWNER));
                assertTrue(Files.notExists(image));
                assertEquals(partialHash,com.google.gson.JsonParser.parseString(Files.readString(meta))
                    .getAsJsonObject().get("partial_sha256").getAsString());
            } else {
                assertThrows(IOException.class,()->PoemCaptureJobs.decide(game,d,"new",OWNER));
                assertArrayEquals(prefix,Files.readAllBytes(image));
            }
        }
    }
}
