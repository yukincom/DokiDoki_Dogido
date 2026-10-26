package dogido.fabric.screenshot;

import com.google.gson.*;
import java.time.Instant;
import java.util.*;

/** 通常会話に添える一枚を既存commands/results/ACKで往復する契約。世界操作や保存先の指定は受け付けない。 */
public final class BodyViewProtocol {
    public static final String CAPABILITY="client.body_view.on_stop.v1";
    public static final String AFTER_LOOK_CAPABILITY="client.body_view.after_look.v1";
    public static final int MAX_PNG_BYTES=128*1024;
    public static final int MAX_EVENT_BYTES=256*1024;
    private static final Set<String> KEYS=Set.of("type","command_id","session_id","issued_at","expires_at",
        "body_entity_uuid","body_epoch","owner_uuid","dimension","after_motion_sequence");
    private static final Set<String> AFTER_LOOK_KEYS=Set.of("command_id","input_generation","executed_at");
    private BodyViewProtocol() {}
    /** 既存の明示注視成功へ結び付ける識別子。回転完了を推測した時刻ではなく結果のexecuted_atを使う。 */
    public record AfterLook(UUID commandId,long inputGeneration,Instant executedAt) {}
    public record Command(String id,String sessionId,Instant issuedAt,Instant expiresAt,
                          UUID bodyId,UUID epoch,UUID ownerId,String dimension,long afterMotionSequence,AfterLook afterLook) {
        public Command(String id,String sessionId,Instant issuedAt,Instant expiresAt,
                UUID bodyId,UUID epoch,UUID ownerId,String dimension,long afterMotionSequence) {
            this(id,sessionId,issuedAt,expiresAt,bodyId,epoch,ownerId,dimension,afterMotionSequence,null);
        }
        public String timeError(Instant now) {
            return now.isBefore(issuedAt)?"not_yet_valid":!now.isBefore(expiresAt)?"expired":null;
        }
        public boolean matches(JsonObject context) {
            try {
                return bodyId.toString().equals(string(context,"body_id")) && epoch.toString().equals(string(context,"epoch"))
                    && ownerId.toString().equals(string(context,"owner_id")) && dimension.equals(string(context,"dimension"));
            } catch (RuntimeException invalid) { return false; }
        }
    }
    public static List<Command> parseCommands(String body) {
        return parseCommands(dogido.fabric.protocol.DogidoCommandProtocol.parseObject(body));
    }

    /** 共通解析済み応答からこの種類の命令を読む。共有objectを変更せず、不正な一件だけを除外する。 */
    public static List<Command> parseCommands(JsonObject root) {
        List<Command> out=new ArrayList<>();
        if (root == null) return out;
        try {
            JsonArray commands=root.getAsJsonArray("commands");
            if (commands==null) return out;
            for (JsonElement value:commands) try {
                JsonObject c=value.getAsJsonObject();
                if (!"capture_body_view".equals(string(c,"type")) || !c.keySet().containsAll(KEYS)
                        || c.keySet().stream().anyMatch(key->!KEYS.contains(key) && !key.equals("after_look"))) continue;
                String id=string(c,"command_id"),session=string(c,"session_id"),dimension=string(c,"dimension");
                if (!captureId(id) || session.isBlank() || session.length()>200 || dimension.length()>200
                        || !dimension.matches("[a-z0-9_.-]+:[a-z0-9_./-]+")) continue;
                Instant issued=Instant.parse(string(c,"issued_at")),expires=Instant.parse(string(c,"expires_at"));
                if (!expires.isAfter(issued) || expires.isAfter(issued.plusSeconds(10))) continue;
                var sequence=c.get("after_motion_sequence");
                if(sequence==null || !sequence.isJsonPrimitive() || !sequence.getAsJsonPrimitive().isNumber())continue;
                long after=sequence.getAsBigDecimal().longValueExact();if(after<0)continue;
                AfterLook look=null;
                if(c.has("after_look")) {
                    JsonObject valueLook=c.getAsJsonObject("after_look");
                    if(valueLook==null || !valueLook.keySet().equals(AFTER_LOOK_KEYS))continue;
                    var generation=valueLook.get("input_generation");
                    if(generation==null || !generation.isJsonPrimitive() || !generation.getAsJsonPrimitive().isNumber())continue;
                    long inputGeneration=generation.getAsBigDecimal().longValueExact();if(inputGeneration<0)continue;
                    Instant executed=Instant.parse(string(valueLook,"executed_at"));
                    if(executed.isAfter(issued))continue;
                    look=new AfterLook(uuid(valueLook,"command_id"),inputGeneration,executed);
                }
                out.add(new Command(id,session,issued,expires,uuid(c,"body_entity_uuid"),uuid(c,"body_epoch"),uuid(c,"owner_uuid"),dimension,after,look));
            } catch (RuntimeException invalid) { /* 不正な一件を他の命令へ波及させない。 */ }
        } catch (RuntimeException invalid) { }
        return out;
    }
    public static boolean captureId(String id) {
        if (id==null || !id.startsWith("view_")) return false;
        try { return UUID.fromString(id.substring(5)).toString().equals(id.substring(5)); }
        catch (RuntimeException invalid) { return false; }
    }
    private static String string(JsonObject o,String key) {
        var v=o.get(key);
        if (v==null || !v.isJsonPrimitive() || !v.getAsJsonPrimitive().isString()) throw new IllegalArgumentException(key);
        return v.getAsString();
    }
    private static UUID uuid(JsonObject o,String key) {
        String text=string(o,key);UUID id=UUID.fromString(text);
        if (!id.toString().equals(text)) throw new IllegalArgumentException(key);return id;
    }
    public static JsonObject result(Command c,String status,String detail,Instant now) {
        JsonObject r=new JsonObject();r.addProperty("command_id",c.id());r.addProperty("command_type","capture_body_view");
        r.addProperty("status",status);r.addProperty("detail_code",detail);r.addProperty("executed_at",now.toString());return r;
    }
    /** 画像は一送信一件だけ。残り31枠で従来結果を運び、未ACK画像を次の送信にも保持する。 */
    public static JsonArray resultBatch(JsonArray ordinary,JsonArray captures) {
        JsonArray batch=new JsonArray();
        if (!captures.isEmpty()) batch.add(captures.get(0).deepCopy());
        for (JsonElement value:ordinary) { if (batch.size()==32) break;batch.add(value.deepCopy()); }
        return batch;
    }
    /** UTF-8のevent全体を測り、画像で上限を超える時はその結果だけ小さい失敗へ置換する。観測本文は変えない。 */
    public static String encodeEvent(JsonObject outbound,Gson gson,java.util.function.Consumer<JsonObject> failed) {
        String encoded=gson.toJson(outbound);
        if(encoded.getBytes(java.nio.charset.StandardCharsets.UTF_8).length<=MAX_EVENT_BYTES)return encoded;
        for(JsonElement value:outbound.getAsJsonArray("command_results")) {
            JsonObject result=value.getAsJsonObject();
            if(!result.has("body_view") || !"capture_body_view".equals(string(result,"command_type")))continue;
            result.remove("body_view");result.addProperty("status","failed");result.addProperty("detail_code","payload_too_large");
            failed.accept(result.deepCopy());
        }
        return gson.toJson(outbound);
    }
    /** session monitor内で使う画像専用台帳。切断・worldEpoch変更で結果を捨て、重複commandの再撮影を防ぐ。 */
    public static final class State {
        private final LinkedHashSet<String> seen=new LinkedHashSet<>();
        private final LinkedHashMap<String,JsonObject> pending=new LinkedHashMap<>();
        private final Map<String,String> captureIds=new HashMap<>();
        public boolean claim(String id) {
            if (!seen.add(id)) return false;
            while(seen.size()>2048) seen.remove(seen.iterator().next());return true;
        }
        public void remember(JsonObject result) {
            String id=string(result,"command_id");pending.put(id,result.deepCopy());
            if(result.has("body_view"))captureIds.put(id,string(result.getAsJsonObject("body_view"),"capture_id"));
            while(pending.size()>32) {String first=pending.keySet().iterator().next();pending.remove(first);captureIds.remove(first);}
        }
        public void clearResults() { pending.clear();captureIds.clear(); }
        public List<String> acknowledge(Collection<String> ids) {
            List<String> captures=new ArrayList<>();
            for (String id:ids) {
                JsonObject r=pending.remove(id);String capture=captureIds.remove(id);
                // 大きい別eventでfailureへ縮退した後に、先行eventの画像ACKが届く場合も利用記録を失わない。
                if(r!=null && captureId(capture))captures.add(capture);
            }
            return captures;
        }
        public JsonArray results() { JsonArray out=new JsonArray();pending.values().forEach(r->out.add(r.deepCopy()));return out; }
    }
}
