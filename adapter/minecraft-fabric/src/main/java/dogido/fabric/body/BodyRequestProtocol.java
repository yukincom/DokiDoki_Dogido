package dogido.fabric.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import static dogido.fabric.body.BodyInspectProtocol.*;

/** 学習済み分類を既存身体操作へ渡す封筒。言葉の再分類やモデル生成座標は扱わない。 */
public final class BodyRequestProtocol {
    public static final String CAPABILITY = "body.request.v1";
    private BodyRequestProtocol() {}
    public enum Mode {
        RECALL, LOOK_DIRECTION, LOOK_TARGET, LOOK_PLAYER,
        LOOK_RELATIVE_FRONT, LOOK_RELATIVE_BACK, LOOK_RELATIVE_LEFT, LOOK_RELATIVE_RIGHT,
        LOOK_RELATIVE_FRONT_LEFT, LOOK_RELATIVE_FRONT_RIGHT, LOOK_RELATIVE_BACK_LEFT, LOOK_RELATIVE_BACK_RIGHT
    }
    public record Command(UUID commandId, Instant issuedAt, Instant expiresAt, long inputGeneration,
            UUID ownerId, String dimension, Mode mode, Context context) {
        public Command {
            if (commandId == null || issuedAt == null || expiresAt == null || inputGeneration < 0 || ownerId == null
                    || dimension == null || !dimension.matches("[a-z0-9_.-]+:[a-z0-9_./-]+") || dimension.length() > 128
                    || mode == null || mode != Mode.RECALL && context == null
                    || context != null && (!ownerId.equals(context.ownerId()) || !dimension.equals(context.dimension())))
                throw new IllegalArgumentException("body_request");
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
            var root = JsonParser.parseString(body).getAsJsonObject();
            if (!root.has("commands") || !root.get("commands").isJsonArray()) return commands;
            for (var value : root.getAsJsonArray("commands")) try {
                var row = value.getAsJsonObject();
                if (!"body_request".equals(string(row,"type"))) continue;
                var generation = row.get("input_generation");
                if (generation == null || !generation.isJsonPrimitive() || !generation.getAsJsonPrimitive().isNumber()
                        || !generation.getAsString().matches("[0-9]{1,19}")) continue;
                Command command = new Command(uuid(row,"command_id"),Instant.parse(string(row,"issued_at")),Instant.parse(string(row,"expires_at")),
                    generation.getAsLong(),uuid(row,"owner_id"),string(row,"dimension"),Mode.valueOf(string(row,"mode").toUpperCase(Locale.ROOT)),
                    !row.has("context") || row.get("context").isJsonNull() ? null : context(row.getAsJsonObject("context")));
                if (command.timeError(command.issuedAt()) == null) commands.add(command);
            } catch (RuntimeException ignored) { }
        } catch (RuntimeException ignored) { }
        return commands;
    }
    private static UUID uuid(JsonObject row,String field) {
        String text = string(row,field); UUID id = UUID.fromString(text);
        if (!id.toString().equalsIgnoreCase(text)) throw new IllegalArgumentException(field);
        return id;
    }
    public static JsonObject result(Command command,String status,String detail,Instant now,Context resolved) {
        JsonObject row = new JsonObject(); row.addProperty("command_id",command.commandId().toString());
        row.addProperty("command_type","body_request"); row.addProperty("status",status);
        row.addProperty("executed_at",now.toString()); row.addProperty("detail_code",detail);
        row.addProperty("input_generation",command.inputGeneration()); row.addProperty("owner_id",command.ownerId().toString());
        row.addProperty("dimension",command.dimension());
        if (resolved != null) { row.addProperty("body_id",resolved.bodyId().toString()); row.addProperty("epoch",resolved.epoch().toString()); }
        return row;
    }
}
