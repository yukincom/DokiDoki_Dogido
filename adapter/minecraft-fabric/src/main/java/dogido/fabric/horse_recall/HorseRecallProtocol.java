package dogido.fabric.horse_recall;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** 馬呼戻しの閉じたwire契約。JSONを検査して不変値へ変え、ゲーム状態には触らない。 */
public final class HorseRecallProtocol {
    public static final String CAPABILITY = "integrated_server.horse.recall.v2";
    public static final int MAX_CANDIDATES = 32;
    public static final Duration MAX_COMMAND_AGE = Duration.ofSeconds(5);

    private HorseRecallProtocol() {}

    /** 座標は受け取らない。対象UUIDと接続世代を実行時のサーバー状態へ再照合する。 */
    public record Command(String commandId, Instant issuedAt, Instant expiresAt,
            UUID worldId, UUID playerUuid, String dimension, UUID entityId) {
        public String timeError(Instant now) {
            if (!expiresAt.isAfter(issuedAt) || Duration.between(issuedAt, expiresAt).compareTo(MAX_COMMAND_AGE) > 0)
                return "invalid_lifetime";
            if (now.isBefore(issuedAt)) return "issued_in_future";
            return now.isBefore(expiresAt) ? null : "expired";
        }
    }

    public record Candidate(UUID entityId, String entityType, String customName, boolean tamed, UUID ownerUuid,
            boolean adult, boolean equipped, boolean alive, String dimension, boolean hasPassengers, boolean hasVehicle, boolean leashed) {
        JsonObject toJson() {
            JsonObject row = new JsonObject();
            row.addProperty("entity_id", entityId.toString());
            row.add("custom_name", customName == null ? JsonNull.INSTANCE : new com.google.gson.JsonPrimitive(customName));
            row.addProperty("tamed", tamed);
            row.add("owner_uuid", ownerUuid == null ? JsonNull.INSTANCE : new com.google.gson.JsonPrimitive(ownerUuid.toString()));
            row.addProperty("entity_type", entityType);
            row.addProperty("adult", adult);
            row.addProperty("equipped", equipped);
            row.addProperty("alive", alive);
            row.addProperty("dimension", dimension);
            row.addProperty("has_passengers", hasPassengers);
            row.addProperty("has_vehicle", hasVehicle);
            row.addProperty("leashed", leashed);
            return row;
        }
    }

    /** サーバースレッドで取得した一世代の写し。32頭超過は一覧全体を利用不可にする。 */
    public record Snapshot(boolean available, UUID worldId, long revision, Instant observedAt,
            UUID playerUuid, String dimension, UUID recentMountId, List<Candidate> candidates) {
        public Snapshot {
            if (revision < 1) throw new IllegalArgumentException("revision must be positive");
            candidates = List.copyOf(candidates);
            if (!available || candidates.size() > MAX_CANDIDATES) {
                available = false;
                candidates = List.of();
            }
        }

        public JsonObject toJson() {
            JsonObject root = new JsonObject();
            root.addProperty("schema_version", 2);
            root.addProperty("status", available ? "available" : "unavailable");
            root.addProperty("world_id", worldId.toString());
            root.addProperty("revision", revision);
            root.addProperty("observed_at", observedAt.toString());
            root.addProperty("player_uuid", playerUuid.toString());
            root.addProperty("dimension", dimension);
            root.add("recent_mount_id", recentMountId == null ? JsonNull.INSTANCE
                : new com.google.gson.JsonPrimitive(recentMountId.toString()));
            JsonArray rows = new JsonArray();
            candidates.forEach(candidate -> rows.add(candidate.toJson()));
            root.add("candidates", rows);
            return root;
        }
    }

    /** game-event応答内のrecall_horseだけを読む。不正な型や任意コマンドは実行対象にしない。 */
    public static List<Command> parseCommands(String body) {
        List<Command> commands = new ArrayList<>();
        try {
            JsonObject root = JsonParser.parseString(body).getAsJsonObject();
            if (!root.has("commands") || !root.get("commands").isJsonArray()) return commands;
            for (JsonElement item : root.getAsJsonArray("commands")) {
                if (!item.isJsonObject()) continue;
                JsonObject row = item.getAsJsonObject();
                if (!"recall_horse".equals(string(row, "type"))) continue;
                try {
                    String id = required(row, "command_id");
                    if (id.length() > 128) continue;
                    Instant issued = Instant.parse(required(row, "issued_at"));
                    Instant expires = Instant.parse(required(row, "expires_at"));
                    if (!expires.isAfter(issued) || Duration.between(issued, expires).compareTo(MAX_COMMAND_AGE) > 0) continue;
                    String dimension = required(row, "dimension");
                    if (!dimension.matches("[a-z0-9_.-]+:[a-z0-9_./-]+")) continue;
                    commands.add(new Command(id, issued, expires, uuid(row, "world_id"),
                        uuid(row, "player_uuid"), dimension, uuid(row, "entity_id")));
                } catch (RuntimeException invalid) {
                    // 一件の不正な命令で、同じ応答の他の有効な命令を失わない。
                }
            }
        } catch (RuntimeException invalid) {
            return commands;
        }
        return commands;
    }

    public static JsonObject result(Command command, String status, String detail, Instant now) {
        JsonObject row = new JsonObject();
        row.addProperty("command_id", command.commandId());
        row.addProperty("command_type", "recall_horse");
        row.addProperty("status", status);
        row.addProperty("executed_at", now.toString());
        row.addProperty("detail_code", detail);
        // 既存command_resultの追加情報はRust側のflatten契約に合わせて最上位へ置く。
        row.addProperty("entity_id", command.entityId().toString());
        row.addProperty("world_id", command.worldId().toString());
        return row;
    }

    private static String string(JsonObject row, String key) {
        JsonElement value = row.get(key);
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isString()
            ? value.getAsString() : null;
    }

    private static String required(JsonObject row, String key) {
        String value = string(row, key);
        if (value == null || value.isBlank()) throw new IllegalArgumentException(key);
        return value;
    }

    private static UUID uuid(JsonObject row, String key) {
        String value = required(row, key);
        UUID uuid = UUID.fromString(value);
        if (!uuid.toString().equalsIgnoreCase(value)) throw new IllegalArgumentException(key);
        return uuid;
    }
}
