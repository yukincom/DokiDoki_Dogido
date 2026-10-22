package dogido.fabric.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** 身体の観察移動に限定したwire契約。モデル由来の対象を不変値へ変換し、実行器で現在候補へ再照合する。 */
public final class BodyInspectProtocol {
    public static final String CAPABILITY = "body.inspect_target.v2";
    public static final int MAX_CANDIDATES = 24;
    private BodyInspectProtocol() {}
    public record Point(double x, double y, double z) {
        public Point {
            if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)
                    || Math.abs(x) > 30_000_000 || Math.abs(y) > 30_000_000 || Math.abs(z) > 30_000_000)
                throw new IllegalArgumentException("position");
        }
        public double distance(Point other) { return Math.sqrt(Math.pow(x-other.x,2)+Math.pow(y-other.y,2)+Math.pow(z-other.z,2)); }
        public JsonObject json() {
            JsonObject row = new JsonObject(); row.addProperty("x",x); row.addProperty("y",y); row.addProperty("z",z); return row;
        }
    }
    public record Context(UUID ownerId, UUID bodyId, UUID epoch, String dimension) {
        public JsonObject json() {
            JsonObject row = new JsonObject(); row.addProperty("owner_id",ownerId.toString());
            row.addProperty("body_id",bodyId.toString()); row.addProperty("epoch",epoch.toString());
            row.addProperty("dimension",dimension); return row;
        }
    }
    public record Target(String id, String kind, String typeId, String label, Point position, UUID entityId) {
        public JsonObject json() {
            JsonObject row = new JsonObject(); row.addProperty("id",id); row.addProperty("kind",kind);
            row.addProperty("type_id",typeId); row.addProperty("label",label); row.add("position",position.json());
            if (entityId != null) row.addProperty("entity_id",entityId.toString()); return row;
        }
    }
    /** モデルは対象座標を作らず、ownerへの接近目的だけを指定できる。 */
    public enum Purpose { INSPECT, OWNER_NEAR, OWNER_FACE }
    /** 見た目による申請。descriptionは説明用で、実物照合は閉じた属性と撮影時の対応表で行う。 */
    public record ImageTarget(String captureId,String description,String kind,String color,String horizontal,String vertical) {
        public ImageTarget {
            if(!dogido.fabric.screenshot.BodyViewProtocol.captureId(captureId) || description==null || description.isBlank()
                || description.codePointCount(0,description.length())>80 || description.codePoints().anyMatch(Character::isISOControl)
                || !dogido.fabric.screenshot.BodyViewAppearance.valid(kind,color,horizontal,vertical))
                throw new IllegalArgumentException("image_target");
        }
    }
    public record Command(UUID commandId, Instant issuedAt, Instant expiresAt, Context context, Target target, Purpose purpose,ImageTarget imageTarget) {
        public Command {
            if (purpose == null || (purpose == Purpose.INSPECT) != (target != null || imageTarget != null)
                    || target != null && imageTarget != null)
                throw new IllegalArgumentException("purpose_target");
        }
        public Command(UUID commandId, Instant issuedAt, Instant expiresAt, Context context, Target target,Purpose purpose) {
            this(commandId,issuedAt,expiresAt,context,target,purpose,null);
        }
        public Command(UUID commandId, Instant issuedAt, Instant expiresAt, Context context, Target target) {
            this(commandId,issuedAt,expiresAt,context,target,Purpose.INSPECT);
        }
        public String timeError(Instant now) {
            if (!expiresAt.isAfter(issuedAt) || Duration.between(issuedAt,expiresAt).compareTo(Duration.ofSeconds(5)) > 0) return "invalid_lifetime";
            if (now.isBefore(issuedAt)) return "issued_in_future";
            return now.isBefore(expiresAt) ? null : "expired";
        }
    }
    public static List<Command> parseCommands(String body) {
        List<Command> commands = new ArrayList<>();
        try {
            JsonObject root = JsonParser.parseString(body).getAsJsonObject();
            if (!root.has("commands") || !root.get("commands").isJsonArray()) return commands;
            for (JsonElement item : root.getAsJsonArray("commands")) {
                try {
                    JsonObject row = item.getAsJsonObject();
                    if (!"inspect_body_target".equals(string(row,"type"))) continue;
                    Purpose purpose = switch (required(row,"purpose",16)) {
                        case "inspect" -> Purpose.INSPECT;
                        case "owner_near" -> Purpose.OWNER_NEAR;
                        case "owner_face" -> Purpose.OWNER_FACE;
                        default -> throw new IllegalArgumentException("purpose");
                    };
                    if (!row.has("target")) throw new IllegalArgumentException("target_missing");
                    Target target = row.get("target").isJsonNull() ? null : target(row.getAsJsonObject("target"));
                    ImageTarget image=null;
                    if(row.has("image_target")) {
                        JsonObject selection=row.getAsJsonObject("image_target");
                        if(!selection.keySet().equals(java.util.Set.of("capture_id","description","kind","color","horizontal","vertical")))throw new IllegalArgumentException("image_fields");
                        image=new ImageTarget(required(selection,"capture_id",64),required(selection,"description",160),required(selection,"kind",16),required(selection,"color",16),required(selection,"horizontal",16),required(selection,"vertical",16));
                    }
                    Command command = new Command(uuid(row,"command_id"),Instant.parse(string(row,"issued_at")),
                        Instant.parse(string(row,"expires_at")),context(row.getAsJsonObject("context")),target,purpose,image);
                    if (command.timeError(command.issuedAt()) == null) commands.add(command);
                } catch (RuntimeException ignored) { }
            }
        } catch (RuntimeException ignored) { }
        return commands;
    }
    static Context context(JsonObject row) {
        String dimension = required(row,"dimension",128);
        if (!registryId(dimension)) throw new IllegalArgumentException("dimension");
        return new Context(uuid(row,"owner_id"),uuid(row,"body_id"),uuid(row,"epoch"),dimension);
    }
    static Target target(JsonObject row) {
        String id = required(row,"id",192), kind = required(row,"kind",16), type = required(row,"type_id",128);
        String label = required(row,"label",160);
        if (label.codePointCount(0,label.length()) > 80 || label.codePoints().anyMatch(Character::isISOControl)) throw new IllegalArgumentException("label");
        Point position = point(row.getAsJsonObject("position"));
        if (!registryId(type)) throw new IllegalArgumentException("type_id");
        UUID entity = null;
        if ("entity".equals(kind)) {
            entity = uuid(row,"entity_id");
            if (!id.equals("e:"+entity)) throw new IllegalArgumentException("entity_id");
        } else if ("block".equals(kind)) {
            if (row.has("entity_id") || !id.equals(blockId(position,type))
                    || position.x()-Math.floor(position.x()) != .5 || position.y()-Math.floor(position.y()) != .5
                    || position.z()-Math.floor(position.z()) != .5) throw new IllegalArgumentException("block_id");
        } else throw new IllegalArgumentException("kind");
        return new Target(id,kind,type,label,position,entity);
    }
    public static String blockId(Point position, String type) {
        return "b:"+(int)Math.floor(position.x())+","+(int)Math.floor(position.y())+","+(int)Math.floor(position.z())+":"+type;
    }
    static Point point(JsonObject row) {
        return new Point(number(row,"x"),number(row,"y"),number(row,"z"));
    }
    private static double number(JsonObject row,String key) {
        JsonElement value = row.get(key);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) throw new IllegalArgumentException(key);
        return value.getAsDouble();
    }
    public static JsonObject unavailable(String reason) {
        JsonObject row = new JsonObject(); row.addProperty("version",2); row.addProperty("status","unavailable"); row.addProperty("reason",reason); return row;
    }
    public static JsonObject result(Command command, String status, String detail, Instant now) {
        JsonObject row = new JsonObject(); row.addProperty("command_id",command.commandId().toString());
        row.addProperty("command_type","inspect_body_target"); row.addProperty("status",status);
        row.addProperty("executed_at",now.toString()); row.addProperty("detail_code",detail);
        row.addProperty("body_id",command.context().bodyId().toString()); row.addProperty("epoch",command.context().epoch().toString()); return row;
    }
    static String string(JsonObject row,String key) {
        JsonElement value = row == null ? null : row.get(key);
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isString() ? value.getAsString() : null;
    }
    private static String required(JsonObject row,String key,int max) {
        String value = string(row,key);
        if (value == null || value.isBlank() || value.length() > max) throw new IllegalArgumentException(key); return value;
    }
    private static UUID uuid(JsonObject row,String key) {
        String text = required(row,key,36); UUID value = UUID.fromString(text);
        if (!value.toString().equalsIgnoreCase(text)) throw new IllegalArgumentException(key); return value;
    }
    private static boolean registryId(String text) { return text.matches("[a-z0-9_.-]+:[a-z0-9_./-]+"); }
}
