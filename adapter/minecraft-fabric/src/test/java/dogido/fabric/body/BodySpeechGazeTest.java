package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.protocol.DialoguePayload;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodySpeechGazeTest {
    private final BodyInspectProtocol.Context context=new BodyInspectProtocol.Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private final AtomicLong now=new AtomicLong();
    private final BodySpeechGaze gaze=new BodySpeechGaze(new BodyConnection(),now::get);
    private DialoguePayload.Snapshot snapshot(boolean voice,String turn,String... references) {
        JsonObject request=null;
        if (turn != null) {
            request=new JsonObject();request.addProperty("turn_id",turn);request.add("context",context.json());
            JsonArray targets=new JsonArray();
            for (String ref : references) { var row=new JsonObject();row.addProperty("observer","body");row.addProperty("kind",ref.startsWith("sound:") ? "sound" : "entity");row.addProperty("id",ref.substring(ref.indexOf(':')+1));targets.add(row); }
            request.add("targets",targets);
        }
        return new DialoguePayload.Snapshot(new DialoguePayload.Scope("s","instance"),voice,"v",true,List.of(),request);
    }
    private BodySpeechGaze.Target target(String kind,String id,double x) { return new BodySpeechGaze.Target("body",kind,id,new Vec3d(x,64,4)); }
    @Test void pinsOneObservedIndividualUntilEndAndNeverSubstitutesAnother() {
        var a=target("entity","a",1);var b=target("entity","b",2);
        gaze.receive(snapshot(true,"turn","entity:a","entity:b"),() -> true);
        assertEquals(a.position(),gaze.resolve(context,List.of(a,b),0));
        gaze.receive(snapshot(true,"turn","entity:b","entity:a"),() -> true);
        assertEquals(a.position(),gaze.resolve(context,List.of(b,a),0));
        assertNull(gaze.resolve(context,List.of(b),0));
        gaze.receive(snapshot(true,null),() -> true);
        assertNull(gaze.resolve(context,List.of(a,b),0));
    }
    @Test void soundUsesRecordedPointEvenWhenSameEntityHasMoved() {
        var sound=target("sound","uuid",2);var entity=target("entity","uuid",20);
        gaze.receive(snapshot(true,"turn","sound:uuid"),() -> true);
        assertEquals(sound.position(),gaze.resolve(context,List.of(entity,sound),0));
        assertNull(gaze.resolve(context,List.of(entity),0));
    }
    @Test void sameSoundIdentityNeverBorrowsTheOtherObserversPosition() {
        var body=target("sound","uuid",2);
        var player=new BodySpeechGaze.Target("player","sound","uuid",new Vec3d(20,64,4));
        var snapshot=snapshot(true,"turn","sound:uuid");
        snapshot.bodyGaze().getAsJsonArray("targets").get(0).getAsJsonObject().addProperty("observer","player");
        gaze.receive(snapshot,() -> true);
        assertEquals(player.position(),gaze.resolve(context,List.of(body,player),0));
        assertNull(gaze.resolve(context,List.of(body),0));
    }
    @Test void expiryVoiceOffAndSessionOrBodyChangeReleaseTheTarget() {
        var target=target("entity","a",1);
        gaze.receive(snapshot(true,"turn","entity:a"),() -> true);
        assertNull(gaze.resolve(context,List.of(target),1_200_000_000L));
        gaze.receive(snapshot(false,"turn","entity:a"),() -> true);
        assertNull(gaze.resolve(context,List.of(target),0));
        gaze.receive(snapshot(true,"turn","entity:a"),() -> false);
        assertNull(gaze.resolve(context,List.of(target),0));
        gaze.receive(snapshot(true,"turn","entity:a"),() -> true);
        var other=new BodyInspectProtocol.Context(context.ownerId(),context.bodyId(),UUID.randomUUID(),context.dimension());
        assertNull(gaze.resolve(other,List.of(target),0));
    }
    @Test void initialMissingObservationCanArriveButNotInventACoordinate() {
        gaze.receive(snapshot(true,"turn","entity:a"),() -> true);
        assertNull(gaze.resolve(context,List.of(),0));
        assertEquals(new Vec3d(3,64,4),gaze.resolve(context,List.of(target("entity","a",3)),0));
    }
}
