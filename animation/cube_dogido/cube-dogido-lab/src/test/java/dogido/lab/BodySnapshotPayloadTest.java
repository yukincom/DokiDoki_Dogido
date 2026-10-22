package dogido.lab;

import com.google.gson.Gson;
import dogido.lab.api.BodyProtocol;
import dogido.lab.api.BodyProtocol.*;
import dogido.lab.api.perception.BodyVisionGeometry;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 通信枠を増やさず追加音が収まること、打切り時も元のpose・視覚・食料・音時刻を変えないことを検査する。 */
class BodySnapshotPayloadTest {
    private static final Gson JSON = new Gson();
    private static final Position POSITION = new Position(30000000,320,-30000000);
    private static Snapshot snapshot(String reason, List<FoodTarget> food, List<HeardSound> sounds) {
        return new Snapshot(BodyProtocol.VERSION,Presence.PRESENT,
            new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld"),"minecraft:overworld",
            30,42,POSITION,180,Activity.HOLDING,reason,41,null,null,Sensors.BODY_VISION_AND_HEARING,
            new Vitals(Life.ACTIVE,20,20,true,"survival",0,0,0,0,null),
            new FoodObservation(ObservationStatus.OBSERVED,30,food,false),
            new PoseObservation(POSITION,POSITION,180,0,0,0),BodyVisionGeometry.unavailable(30,"area_unloaded"),
            new HearingObservation(ObservationStatus.OBSERVED,30,sounds,false),new ControlObservation(true,Mode.FOLLOW,16,24,new Reassurance(87.5,2.1,true,true,false)));
    }
    private static List<HeardSound> sounds() {
        var result = new ArrayList<HeardSound>();
        for (int i=0;i<BodyHearing.MAX_SOUNDS;i++) result.add(new HeardSound(i+1,20+i,POSITION,
            "minecraft:"+"a".repeat(BodyHearing.MAX_SOUND_ID_LENGTH-10),"blocks",4,i==0 ? null : 1f,UUID.randomUUID()));
        return result;
    }

    @Test void ordinarySnapshotRoundTripsV10AndPreservesUnknownPitch() {
        Snapshot original = snapshot("holding",List.of(),sounds());
        String encoded = BodySnapshotPayload.encode(original);
        Snapshot decoded = JSON.fromJson(encoded,Snapshot.class);
        assertEquals(10,decoded.version());
        assertEquals(new ControlObservation(true,Mode.FOLLOW,16,24,new Reassurance(87.5,2.1,true,true,false)),decoded.control());
        assertEquals(original,decoded);
        assertNull(decoded.hearing().sounds().getFirst().pitch());
        assertTrue(encoded.getBytes(StandardCharsets.UTF_8).length <= BodySnapshotPayload.MAX_BYTES);
    }

    @Test void hearingIsTrimmedToPacketBudgetWithoutChangingOtherObservations() {
        var food = new ArrayList<FoodTarget>();
        for (int i=0;i<BodyFood.MAX_TARGETS;i++) food.add(new FoodTarget("drop:"+UUID.randomUUID(),FoodKind.DROPPED_ITEM,
            POSITION,"minecraft:"+"a".repeat(80),6));
        // recordの直列化長を推測せず、実測から送信上限を1byte超えるfixtureを作る。
        Snapshot seed = snapshot("",food,sounds());
        int padding = BodySnapshotPayload.MAX_BYTES+1-JSON.toJson(seed).getBytes(StandardCharsets.UTF_8).length;
        assertTrue(padding > 0);
        Snapshot original = snapshot("x".repeat(padding),food,sounds());
        assertEquals(BodySnapshotPayload.MAX_BYTES+1,JSON.toJson(original).getBytes(StandardCharsets.UTF_8).length);
        String encoded = BodySnapshotPayload.encode(original);
        assertTrue(encoded.getBytes(StandardCharsets.UTF_8).length <= BodySnapshotPayload.MAX_BYTES);
        Snapshot decoded = JSON.fromJson(encoded,Snapshot.class);
        assertEquals(original.food(),decoded.food());
        assertEquals(original.control(),decoded.control());
        assertEquals(original.pose(),decoded.pose());
        assertEquals(original.vision(),decoded.vision());
        assertEquals(original.snapshotRevision(),decoded.snapshotRevision());
        assertTrue(decoded.hearing().truncated());
        assertTrue(decoded.hearing().sounds().size() < original.hearing().sounds().size());
        assertFalse(decoded.hearing().sounds().isEmpty());
        assertEquals(original.hearing().sounds().getLast(),decoded.hearing().sounds().getLast());
    }

    @Test void maximumFoodAndSoundSamplesStillFitWithReassuranceAfterTrimming() {
        var food = new ArrayList<FoodTarget>();
        for (int i=0;i<BodyFood.MAX_TARGETS;i++) food.add(new FoodTarget("drop:"+UUID.randomUUID(),FoodKind.DROPPED_ITEM,
            POSITION,"minecraft:"+"a".repeat(110),6));
        Snapshot original = snapshot("holding",food,sounds());
        String encoded = BodySnapshotPayload.encode(original);
        assertTrue(encoded.getBytes(StandardCharsets.UTF_8).length <= BodySnapshotPayload.MAX_BYTES);
        Snapshot decoded = JSON.fromJson(encoded,Snapshot.class);
        assertEquals(original.food(),decoded.food());
        assertEquals(original.control(),decoded.control());
        assertTrue(decoded.hearing().truncated());
        assertFalse(decoded.hearing().sounds().isEmpty());
    }

    @Test void utf8LimitIsCheckedAndOversizedNonHearingValuesFailBeforeSend() {
        Snapshot original = snapshot("あ".repeat(BodySnapshotPayload.MAX_BYTES/2),List.of(),sounds());
        assertThrows(IllegalArgumentException.class,() -> BodySnapshotPayload.encode(original));
    }
}
