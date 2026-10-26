package dogido.fabric.body;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.lab.api.BodyProtocol;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** v3観測から独立した制御封筒と、候補を一つの身体frameだけに束縛する契約を検証する。 */
class BodyAutonomyJsonTest {
    static BodyProtocol.Snapshot snapshot() {
        var base=BodyObservationJsonTest.snapshot(BodyProtocol.Presence.PRESENT,new BodyProtocol.Position(10,64,20),0);
        var row=new Gson().toJsonTree(base).getAsJsonObject();
        row.getAsJsonObject("control").addProperty("enabled",true); row.getAsJsonObject("control").addProperty("mode","FOLLOW");
        return new Gson().fromJson(row,BodyProtocol.Snapshot.class);
    }
    static BodySensesCollector collector() {
        return new BodySensesCollector(org.slf4j.LoggerFactory.getLogger("autonomy-test"),() -> 0,10,16,
            new dogido.fabric.observation.mobs.ThreatObserver.Settings(16,16,8,4,2,40,200));
    }
    static void cache(BodySensesCollector collector,JsonObject observation) throws Exception {
        JsonObject sample=observation.getAsJsonObject("sample"); JsonObject senses=new JsonObject(); senses.addProperty("status","observed");
        JsonArray targets=new JsonArray(); targets.add(BodyInspectControllerTest.TARGET.json());
        for (var entry : java.util.Map.of("retainedSenses",senses,"retainedCandidates",targets,
                "frameKey",sample.getAsJsonObject("context")+":"+sample.get("server_tick")+":"+sample.get("snapshot_revision")).entrySet()) {
            var field=BodySensesCollector.class.getDeclaredField(entry.getKey()); field.setAccessible(true); field.set(collector,entry.getValue());
        }
    }
    @Test void bodyControlIsIndependentAndKeepsMissingBridgeUnavailable() {
        var snapshot=snapshot();
        JsonObject observation=BodyObservationJson.observed(snapshot,25,BodyObservationJsonTest.OWNER,BodyObservationJsonTest.WORLD);
        JsonObject autonomy=BodySnapshotReader.autonomy(snapshot,observation);
        assertEquals(2,autonomy.get("version").getAsInt()); assertEquals("follow",autonomy.get("mode").getAsString());
        assertTrue(autonomy.get("enabled").getAsBoolean()); assertEquals(16,autonomy.get("follow_distance").getAsDouble());
        assertTrue(autonomy.has("reassurance")); assertTrue(autonomy.has("attention"));
        assertEquals(snapshot.control().reassurance().value(),autonomy.getAsJsonObject("reassurance").get("value").getAsDouble());
        assertEquals(3,observation.get("version").getAsInt()); assertFalse(observation.has("enabled"));
        JsonObject missing=BodySnapshotReader.autonomy(snapshot,BodyConnection.unavailable("stale_or_missing"));
        assertEquals("unavailable",missing.get("status").getAsString()); assertFalse(missing.has("context"));
    }
    @Test void candidatesOnlySurviveExactlyTheSameFreshEnabledFollowFrame() throws Exception {
        var snapshot=snapshot(); JsonObject observation=BodyObservationJson.observed(snapshot,25,BodyObservationJsonTest.OWNER,BodyObservationJsonTest.WORLD);
        var frame=new BodyConnection.Frame(observation,BodyConnection.Hearing.unavailable(),BodySnapshotReader.autonomy(snapshot,observation),null,null);
        var collector=collector(); cache(collector,observation);
        assertEquals(1,collector.autonomy(frame,observation).getAsJsonArray("candidates").size());
        for (String changed : java.util.List.of("revision","context","mode","enabled","senses")) {
            var altered=observation.deepCopy(); var metadata=frame.autonomy().deepCopy();
            switch (changed) {
                case "revision" -> altered.getAsJsonObject("sample").addProperty("snapshot_revision",999);
                case "context" -> altered.getAsJsonObject("sample").getAsJsonObject("context").addProperty("epoch",java.util.UUID.randomUUID().toString());
                case "mode" -> metadata.addProperty("mode","direct");
                case "enabled" -> metadata.addProperty("enabled",false);
                case "senses" -> { var field=BodySensesCollector.class.getDeclaredField("retainedSenses"); field.setAccessible(true); JsonObject unavailable=new JsonObject(); unavailable.addProperty("status","unavailable"); field.set(collector,unavailable); }
            }
            assertTrue(collector.autonomy(new BodyConnection.Frame(altered,frame.hearing(),metadata,null,null),altered).getAsJsonArray("candidates").isEmpty(),changed);
        }
        cache(collector,observation); collector.reset(); assertTrue(collector.autonomy(frame,observation).getAsJsonArray("candidates").isEmpty());
        observation.addProperty("age_ms",3000); assertEquals("unavailable",collector.autonomy(frame,observation).get("status").getAsString());
    }
    @Test void focusSwitchAndMatchingFailureInvalidateCandidatesEvenWithinTheSameFrame() throws Exception {
        var snapshot=snapshot();
        JsonObject observation=BodyObservationJson.observed(snapshot,25,BodyObservationJsonTest.OWNER,BodyObservationJsonTest.WORLD);
        var frame=new BodyConnection.Frame(observation,BodyConnection.Hearing.unavailable(),BodySnapshotReader.autonomy(snapshot,observation),null,null);
        var collector=collector(); cache(collector,observation);
        var command=BodyInspectControllerTest.command();
        collector.focus(command,snapshot.serverTick(),123,() -> true);
        assertTrue(collector.autonomy(frame,observation).getAsJsonArray("candidates").isEmpty());
        cache(collector,observation);
        collector.inspectionResult(BodyInspectProtocol.result(BodyInspectControllerTest.command(),"expired","result_timeout",BodyInspectControllerTest.NOW));
        assertEquals(1,collector.autonomy(frame,observation).getAsJsonArray("candidates").size());
        collector.inspectionResult(BodyInspectProtocol.result(command,"expired","result_timeout",BodyInspectControllerTest.NOW));
        assertTrue(collector.autonomy(frame,observation).getAsJsonArray("candidates").isEmpty());
    }
}
