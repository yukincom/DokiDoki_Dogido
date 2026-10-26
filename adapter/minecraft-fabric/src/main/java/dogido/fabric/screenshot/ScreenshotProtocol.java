package dogido.fabric.screenshot;

import com.google.gson.*;
import java.time.Instant;
import java.util.*;

/** 発句準備に固定した身体視点の原寸撮影命令。保存先・ファイル名をサーバーから指定させない。 */
public final class ScreenshotProtocol {
    public static final String CAPABILITY = "client.screenshot.body_view.v1";
    private static final Set<String> KEYS = Set.of(
        "type","command_id","session_id","poem_id","dimension","issued_at","expires_at",
        "body_entity_uuid","body_epoch","owner_uuid");
    public record Command(String id, String sessionId, String poemId, String dimension,
                          Instant issuedAt, Instant expiresAt, UUID bodyId, UUID epoch, UUID ownerId) {
        public String timeError(Instant now) {
            return now.isBefore(issuedAt) ? "not_yet_valid" : !now.isBefore(expiresAt) ? "expired" : null;
        }
        public boolean matches(JsonObject context) {
            try {
                return bodyId.toString().equals(string(context,"body_id")) && epoch.toString().equals(string(context,"epoch"))
                    && ownerId.toString().equals(string(context,"owner_id")) && dimension.equals(string(context,"dimension"));
            } catch(RuntimeException invalid) { return false; }
        }
    }
    public static List<Command> parseCommands(String body) {
        return parseCommands(dogido.fabric.protocol.DogidoCommandProtocol.parseObject(body));
    }

    /** 共通解析済み応答からこの種類の命令を読む。共有objectを変更せず、不正な一件だけを除外する。 */
    public static List<Command> parseCommands(JsonObject root) {
        List<Command> out = new ArrayList<>();
        if (root == null) return out;
        try {
            var commands = root.getAsJsonArray("commands");
            if (commands == null) return out;
            for (var value : commands) {
                try {
                    var c = value.getAsJsonObject();
                    if (!"capture_screenshot".equals(string(c,"type")) || !c.keySet().equals(KEYS)) continue;
                    String id=string(c,"command_id"), sid=string(c,"session_id"), poem=string(c,"poem_id");
                    String dimension=string(c,"dimension");
                    if (!id.startsWith("shot_") || !UUID.fromString(id.substring(5)).toString().equals(id.substring(5))
                        || sid.length()>200 || sid.isBlank() || poem.length()>500
                        || !poem.startsWith("session:"+sid+":") || dimension.isBlank() || dimension.length()>200) continue;
                    Instant issued=Instant.parse(string(c,"issued_at")), expires=Instant.parse(string(c,"expires_at"));
                    if (!expires.isAfter(issued) || expires.isAfter(issued.plusSeconds(10))) continue;
                    out.add(new Command(id,sid,poem,dimension,issued,expires,uuid(c,"body_entity_uuid"),uuid(c,"body_epoch"),uuid(c,"owner_uuid")));
                } catch (RuntimeException ignored) { /* 不正な一命令を他の命令へ混ぜない。 */ }
            }
        } catch (RuntimeException ignored) {}
        return out;
    }
    private static String string(JsonObject o,String key) {
        var v=o.get(key);
        if (v==null || !v.isJsonPrimitive() || !v.getAsJsonPrimitive().isString()) throw new IllegalArgumentException(key);
        return v.getAsString();
    }
    private static UUID uuid(JsonObject o,String key) {
        String value=string(o,key);UUID id=UUID.fromString(value);
        if(!id.toString().equals(value))throw new IllegalArgumentException(key);return id;
    }
    /** 復帰確認済みframeの識別情報だけを渡し、保存完了時の別dimensionや再生成された身体へ置き換えない。 */
    public static JsonObject captureView(Command command,JsonObject frameContext,boolean cameraRestored) {
        if(!cameraRestored || !command.matches(frameContext))throw new IllegalArgumentException("unverified_body_view");
        JsonObject view=new JsonObject();view.addProperty("viewpoint","dogido_client_render_eye");
        view.addProperty("body_entity_uuid",string(frameContext,"body_id"));view.addProperty("body_epoch",string(frameContext,"epoch"));
        view.addProperty("owner_uuid",string(frameContext,"owner_id"));view.addProperty("dimension",string(frameContext,"dimension"));
        view.addProperty("camera_restored",true);return view;
    }
    public static JsonObject result(Command c,String status,String detail,Instant now) {
        JsonObject r=new JsonObject();
        r.addProperty("command_id",c.id()); r.addProperty("command_type","capture_screenshot");
        r.addProperty("status",status); r.addProperty("detail_code",detail); r.addProperty("executed_at",now.toString());
        r.addProperty("session_id",c.sessionId()); r.addProperty("poem_id",c.poemId());
        return r;
    }
    /** wire上限32件へ公平に分割する。未送信結果は各台帳に残して次のACK後に送る。 */
    public static JsonArray resultBatch(JsonArray ordinary,JsonArray captures) {
        JsonArray batch=new JsonArray();
        for (int i=0;batch.size()<32 && (i<ordinary.size() || i<captures.size());i++) {
            if (i<ordinary.size()) batch.add(ordinary.get(i).deepCopy());
            if (batch.size()<32 && i<captures.size()) batch.add(captures.get(i).deepCopy());
        }
        return batch;
    }
    /** EventClientのsession monitor内だけで使う。切断は未ACK結果を捨てるが撮影済みIDは保持する。 */
    public static final class State {
        private final LinkedHashSet<String> seen = new LinkedHashSet<>();
        private final LinkedHashMap<String,JsonObject> pending = new LinkedHashMap<>();
        public boolean claim(String id) {
            if (!seen.add(id)) return false;
            while (seen.size()>2048) seen.remove(seen.iterator().next());
            return true;
        }
        public void remember(JsonObject result) {
            pending.put(result.get("command_id").getAsString(),result.deepCopy());
            while (pending.size()>32) pending.remove(pending.keySet().iterator().next());
        }
        public void clearResults() { pending.clear(); }
        public void acknowledge(Collection<String> ids) { ids.forEach(pending::remove); }
        public JsonArray results() {
            JsonArray out=new JsonArray(); pending.values().forEach(r -> out.add(r.deepCopy())); return out;
        }
    }
}
