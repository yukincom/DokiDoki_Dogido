package dogido.fabric.screenshot;

import com.google.gson.*;
import com.sun.net.httpserver.HttpServer;
import dogido.fabric.protocol.DogidoEventClient;
import java.net.*;
import java.nio.file.*;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

/** 端末の一時PNG・台帳と模擬HTTPを通し、再起動後の所有者別再配送を検査する。実Minecraft描画は含まない。 */
class ScreenshotReceiptDeliveryTest {
    @TempDir Path game;
    private static final UUID OWNER=UUID.randomUUID();
    private ScreenshotRuntime.Saved capture() throws Exception {
        Instant now=Instant.now();
        var c=new ScreenshotProtocol.Command("shot_"+UUID.randomUUID(),"source","session:source:capture:local-job",
            "minecraft:overworld",now,now.plusSeconds(10),UUID.randomUUID(),UUID.randomUUID(),OWNER);
        PoemCaptureJobs.reserve(game,c);
        JsonObject context=new JsonObject();context.addProperty("owner_id",OWNER.toString());context.addProperty("body_id",c.bodyId().toString());
        context.addProperty("epoch",c.epoch().toString());context.addProperty("dimension",c.dimension());
        return PoemCaptureJobs.save(game,c,now,1,1,new int[]{0xffabcdef},ScreenshotProtocol.captureView(c,context,true));
    }
    private DogidoEventClient connect(HttpServer server,UUID owner) throws Exception {
        var c=new DogidoEventClient(org.slf4j.LoggerFactory.getLogger("receipt-http"),
            new DogidoEventClient.ConnectionSettings(true,"http://127.0.0.1:"+server.getAddress().getPort(),""),
            cmd->{},(cmd,ref)->{},()->false,null,new ScreenshotRuntime(()->game,Runnable::run),null,null,null);
        c.ensureSession("local",owner,true);
        long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(4);
        while (c.currentSession()==null && System.nanoTime()<end) Thread.sleep(5);
        assertNotNull(c.currentSession());
        return c;
    }

    // 見込み: PNG保全後に通信が切れても同じownerへ元の受領証を届ける。合格: 異ownerには0件、未ACKは2回同一、中間keepで止めずACK後の再起動で0件。
    @Test void reconnectReplaysExactOriginalReceiptUntilDurableAck() throws Exception {
        var saved=capture();var receipt=saved.receipt();String shot=receipt.get("command_id").getAsString();
        String meta=Files.readString(game.resolve("dogido-vision/poem-capture-jobs").resolve(shot).resolve("state.json"));
        assertEquals(receipt,JsonParser.parseString(meta).getAsJsonObject().get("receipt"));
        var server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        AtomicInteger sessions=new AtomicInteger();AtomicBoolean ack=new AtomicBoolean();
        AtomicReference<JsonArray> batch=new AtomicReference<>();
        server.createContext("/api/v1/adapter-sessions",ex->{
            ex.getRequestBody().readAllBytes();
            byte[] body=("{\"session_id\":\"reconnect-"+sessions.incrementAndGet()+"\"}").getBytes(java.nio.charset.StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200,body.length);ex.getResponseBody().write(body);ex.close();
        });
        server.createContext("/api/v1/game-events",ex->{
            var event=JsonParser.parseString(new String(ex.getRequestBody().readAllBytes(),java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
            batch.set(event.getAsJsonArray("command_results"));
            JsonObject response=new JsonObject();JsonArray ids=new JsonArray();
            if (ack.get()) ids.add(shot);
            response.add("acknowledged_command_ids",ids);
            byte[] body=response.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200,body.length);ex.getResponseBody().write(body);ex.close();
        });
        server.start();
        try {
            var foreign=connect(server,UUID.randomUUID());
            foreign.postEvent(new JsonObject()).get(5,TimeUnit.SECONDS);
            assertTrue(batch.get().isEmpty());foreign.disconnected();
            var own=connect(server,OWNER);
            var d=new ScreenshotProtocol.Disposition(UUID.randomUUID().toString(),own.currentSession().sessionId(),"source",OWNER,shot,"keep","keep-id");
            assertEquals("kept",PoemCaptureJobs.decide(game,d,own.currentSession().sessionId(),OWNER));
            for (int i=0;i<2;i++) {
                ack.set(i==1);
                own.postEvent(new JsonObject()).get(5,TimeUnit.SECONDS);
                assertEquals(1,batch.get().size());assertEquals(receipt,batch.get().get(0));
                assertEquals("source",batch.get().get(0).getAsJsonObject().get("session_id").getAsString());
            }
            assertTrue(Files.exists(Path.of(saved.directory(),saved.filename())));
            own.disconnected();
            connect(server,OWNER).postEvent(new JsonObject()).get(5,TimeUnit.SECONDS);
            assertTrue(batch.get().isEmpty());
            assertTrue(PoemCaptureJobs.pendingReceipts(game,OWNER).isEmpty());
        } finally { server.stop(0); }
    }

    // 見込み: 公開とready印の間で停止しても実体から復旧する。合格: 同じinode・SHAのPNGだけ復元し、破損PNGを成功にしない。実FSの停止点を再現。
    @Test void tentativeReceiptRequiresPublishedImageProof() throws Exception {
        var good=capture();var changed=capture();
        for (var saved:new ScreenshotRuntime.Saved[]{good,changed}) {
            var dir=game.resolve("dogido-vision/poem-capture-jobs").resolve(saved.receipt().get("command_id").getAsString());
            var state=JsonParser.parseString(Files.readString(dir.resolve("state.json"))).getAsJsonObject();
            state.remove("receipt_published");PoemCaptureJobs.store(dir,state);
        }
        Files.writeString(Path.of(changed.directory(),changed.filename()),"other bytes");
        assertEquals(java.util.List.of(good.receipt()),PoemCaptureJobs.pendingReceipts(game,OWNER));
    }
}
