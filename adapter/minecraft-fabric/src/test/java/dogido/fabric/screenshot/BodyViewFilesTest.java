package dogido.fabric.screenshot;

import com.google.gson.*;
import java.nio.file.*;
import java.time.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class BodyViewFilesTest {
    @TempDir Path root;
    private final Instant now=Instant.parse("2026-10-21T00:00:00Z");
    private Path make(Instant expires) throws Exception {
        Path directory=Files.createDirectory(root.resolve("view_12345678-1234-1234-1234-123456789abc"));
        Files.writeString(directory.resolve("frame.png"),"image bytes");
        JsonObject manifest=new JsonObject();manifest.addProperty("schema_version",1);manifest.addProperty("expires_at",expires.toString());
        JsonArray images=new JsonArray();images.add(BodyViewFiles.image("frame.png",BodyViewFiles.hash(directory.resolve("frame.png"))));manifest.add("images",images);
        BodyViewFiles.json(directory.resolve("retention.json"),manifest);return directory;
    }
    @Test void expiresOnlyExactGeneratedImagesAndKeepsRecords() throws Exception {
        Path directory=make(now.minusSeconds(1));Files.writeString(directory.resolve("frame.json"),"record");
        Files.writeString(directory.resolve("personal.png"),"untouched");
        assertEquals(1,BodyViewFiles.cleanup(root,now));assertFalse(Files.exists(directory.resolve("frame.png")));
        assertTrue(Files.exists(directory.resolve("frame.json")));assertTrue(Files.exists(directory.resolve("personal.png")));
        assertEquals(0,BodyViewFiles.cleanup(root,now));
    }
    @Test void retainsUntilAfterLastUseDeadline() throws Exception {
        Path directory=make(now.plusSeconds(1));assertEquals(0,BodyViewFiles.cleanup(root,now));
        assertTrue(Files.exists(directory.resolve("frame.png")));
    }
    @Test void modifiedImagePreventsDeletion() throws Exception {
        Path directory=make(now.minusSeconds(1));Files.writeString(directory.resolve("frame.png"),"changed");
        assertEquals(0,BodyViewFiles.cleanup(root,now));assertTrue(Files.exists(directory.resolve("frame.png")));
    }
    @Test void pathOutsideAllowlistIsNeverDeleted() throws Exception {
        Path directory=make(now.minusSeconds(1));Path outside=root.resolve("original.png");Files.writeString(outside,"personal");
        JsonObject data=JsonParser.parseString(Files.readString(directory.resolve("retention.json"))).getAsJsonObject();
        data.getAsJsonArray("images").get(0).getAsJsonObject().addProperty("filename","../original.png");
        BodyViewFiles.json(directory.resolve("retention.json"),data);
        assertEquals(0,BodyViewFiles.cleanup(root,now));assertEquals("personal",Files.readString(outside));
    }
    @Test void sharedLockPreventsDeleteDuringUseAndUpdatedDeadlineWins() throws Exception {
        Path directory=make(now.minusSeconds(1));
        try (var lock=BodyViewFiles.tryLock(directory)) {
            assertNotNull(lock);assertNull(BodyViewFiles.tryLock(directory));
            assertEquals(0,BodyViewFiles.cleanup(root,now));
            JsonObject data=JsonParser.parseString(Files.readString(directory.resolve("retention.json"))).getAsJsonObject();
            data.addProperty("expires_at",now.plusSeconds(86400).toString());
            BodyViewFiles.json(directory.resolve("retention.json"),data);
        }
        assertEquals(0,BodyViewFiles.cleanup(root,now));assertTrue(Files.exists(directory.resolve("frame.png")));
    }
    @Test void failedUnpublishedSaveLeavesNoUnmanagedImageAndKeepsUnrelatedFile() throws Exception {
        Path directory=make(now);Files.writeString(directory.resolve("retention.json.tmp"),"partial");
        Files.writeString(directory.resolve("personal.png"),"keep");
        BodyViewFiles.discardUnpublished(directory);assertFalse(Files.exists(directory.resolve("frame.png")));
        assertEquals("keep",Files.readString(directory.resolve("personal.png")));
    }
    @Test void publishedRecordCannotBeRolledBackByFailureCleanup() throws Exception {
        Path directory=make(now);Files.writeString(directory.resolve("frame.json"),"published");
        BodyViewFiles.discardUnpublished(directory);assertTrue(Files.exists(directory.resolve("frame.png")));
    }
    @Test void acknowledgementExtendsSharedRetentionAndNeverShortensNewerUse() throws Exception {
        Path directory=make(now.plusSeconds(1));
        try(var lock=BodyViewFiles.tryLock(directory)) {assertFalse(BodyViewFiles.markUsed(directory,now.plusSeconds(5)));}
        assertTrue(BodyViewFiles.markUsed(directory,now.plusSeconds(5)));
        assertTrue(BodyViewFiles.markUsed(directory,now));
        JsonObject data=JsonParser.parseString(Files.readString(directory.resolve("retention.json"))).getAsJsonObject();
        assertEquals(now.plusSeconds(5).toString(),data.get("last_used_at").getAsString());
        assertEquals(now.plusSeconds(86405).toString(),data.get("expires_at").getAsString());
        assertEquals(0,BodyViewFiles.cleanup(root,now.plusSeconds(86404)));
    }
    @Test void unpublishedResizedImageIsAlsoRecovered() throws Exception {
        Path directory=make(now);Files.writeString(directory.resolve("player-192.png"),"resized");
        BodyViewFiles.discardUnpublished(directory);assertFalse(Files.exists(directory));
    }
}
