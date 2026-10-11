package dogido.fabric.observation;

import com.google.gson.JsonObject;
import net.minecraft.entity.Entity;
import net.minecraft.entity.passive.AbstractHorseEntity;
import net.minecraft.entity.passive.CamelEntity;
import net.minecraft.entity.passive.TameableEntity;
import net.minecraft.registry.Registries;

public record MobIdentityObservation(String entityId, String customName, boolean tamed) {
        public JsonObject toJson() {
            JsonObject result = new JsonObject();
            result.addProperty("entity_id", entityId);
            if (customName != null) result.addProperty("custom_name", customName);
            result.addProperty("tamed", tamed);
            return result;
        }

    public static MobIdentityObservation mobIdentity(Entity entity) {
        boolean tamed = entity instanceof TameableEntity tameable && tameable.isTamed()
            || entity instanceof AbstractHorseEntity horse && !(horse instanceof CamelEntity) && horse.isTame();
        String name = entity.getCustomName() == null ? null : entity.getCustomName().getString();
        return new MobIdentityObservation(entity.getUuid().toString(), customName(name), tamed);
    }

    public static String entityTypeName(Entity entity) {
        return Registries.ENTITY_TYPE.getId(entity.getType()).getPath();
    }
    public static String customName(String raw) {
        if (raw == null) return null;
        String clean = raw.codePoints().filter(c -> !Character.isISOControl(c))
            .limit(64).collect(StringBuilder::new, StringBuilder::appendCodePoint, StringBuilder::append)
            .toString().strip();
        return clean.isEmpty() ? null : clean;
    }
}
