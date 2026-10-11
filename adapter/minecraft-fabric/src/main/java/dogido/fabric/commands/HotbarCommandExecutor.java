package dogido.fabric.commands;

import com.google.gson.JsonObject;
import dogido.fabric.protocol.DogidoCommandProtocol;
import java.time.Instant;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.item.ItemStack;
import org.slf4j.Logger;

import static dogido.fabric.observation.player.ItemIdentity.itemId;

public final class HotbarCommandExecutor {
    private final Logger logger;
    private final java.util.function.Consumer<JsonObject> resultSink;

    public HotbarCommandExecutor(Logger logger, java.util.function.Consumer<JsonObject> resultSink) {
        this.logger = logger;
        this.resultSink = resultSink;
    }

    public void handleSelectHotbarCommand(DogidoCommandProtocol.SelectHotbarCommand command) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null) {
            rememberSelectHotbarResult(command, "failed", null, null, "client_unavailable");
            return;
        }
        client.execute(() -> executeSelectHotbarCommand(client, command));
    }

    private void executeSelectHotbarCommand(
        MinecraftClient client,
        DogidoCommandProtocol.SelectHotbarCommand command
    ) {
        Instant now = Instant.now();
        if (!now.isBefore(command.expiresAt())) {
            rememberSelectHotbarResult(command, "expired", null, null, "expired");
            return;
        }
        ClientPlayerEntity player = client.player;
        if (player == null || client.world == null) {
            rememberSelectHotbarResult(command, "failed", null, null, "player_or_world_unavailable");
            return;
        }
        if (!net.minecraft.entity.player.PlayerInventory.isValidHotbarIndex(command.slot())) {
            rememberSelectHotbarResult(
                command,
                "rejected",
                player.getInventory().getSelectedSlot(),
                itemId(player.getMainHandStack()),
                "invalid_slot"
            );
            return;
        }
        ItemStack target = player.getInventory().getStack(command.slot());
        String targetItemId = itemId(target);
        if (target.isEmpty() || !command.expectedItemId().equals(targetItemId)) {
            rememberSelectHotbarResult(
                command,
                "rejected",
                player.getInventory().getSelectedSlot(),
                itemId(player.getMainHandStack()),
                "expected_item_mismatch"
            );
            return;
        }

        player.getInventory().setSelectedSlot(command.slot());
        String selectedItemId = itemId(player.getMainHandStack());
        if (
            player.getInventory().getSelectedSlot() != command.slot()
                || !command.expectedItemId().equals(selectedItemId)
        ) {
            rememberSelectHotbarResult(
                command,
                "failed",
                player.getInventory().getSelectedSlot(),
                selectedItemId,
                "selection_not_applied"
            );
            return;
        }
        rememberSelectHotbarResult(command, "succeeded", command.slot(), selectedItemId, "selected");
    }

    private void rememberSelectHotbarResult(
        DogidoCommandProtocol.SelectHotbarCommand command,
        String status,
        Integer selectedSlot,
        String selectedItemId,
        String detailCode
    ) {
        this.resultSink.accept(
            DogidoCommandProtocol.commandResult(
                command,
                status,
                Instant.now(),
                selectedSlot,
                selectedItemId,
                detailCode
            )
        );
        this.logger.info(
            "Dogido command result: command_id={} status={} detail={}",
            command.commandId(),
            status,
            detailCode
        );
    }
}
