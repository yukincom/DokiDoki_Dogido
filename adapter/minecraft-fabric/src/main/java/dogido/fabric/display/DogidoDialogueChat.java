package dogido.fabric.display;

import com.mojang.brigadier.arguments.StringArgumentType;
import dogido.fabric.protocol.DialoguePayload;
import dogido.fabric.protocol.DogidoDialogueClient;
import dogido.fabric.protocol.DogidoEventClient;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.function.Predicate;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.minecraft.client.MinecraftClient;
import net.minecraft.text.Text;

import static net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.argument;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.literal;

/** Private /dogido input and local transcript; ordinary chat and other commands never enter this path. */
public final class DogidoDialogueChat {
    private record Context(DogidoEventClient.SessionRef session, Object world, Object connection) {}
    private final DogidoEventClient events;
    private final DogidoDialogueClient dialogue;
    private final DialogueDisplayState state = new DialogueDisplayState();
    private final Predicate<Boolean> saveVoiceEnabled;
    private CompletableFuture<DialoguePayload.Acknowledgement> acknowledgement;

    private DogidoDialogueChat(DogidoEventClient events, Predicate<Boolean> saveVoiceEnabled) {
        this.events = events;
        this.dialogue = new DogidoDialogueClient(events);
        this.saveVoiceEnabled = saveVoiceEnabled;
    }

    public static DogidoDialogueChat register(DogidoEventClient events, Predicate<Boolean> saveVoiceEnabled) {
        DogidoDialogueChat chat = new DogidoDialogueChat(events, saveVoiceEnabled);
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) ->
            dispatcher.register(literal("dogido")
                .executes(context -> chat.usage())
                // One greedy argument consumes every private message locally, including malformed options.
                .then(argument("text", StringArgumentType.greedyString())
                    .suggests((context, builder) -> {
                        for (String option : List.of("voice on", "voice off")) {
                            if (option.startsWith(builder.getRemaining())) builder.suggest(option);
                        }
                        return builder.buildFuture();
                    })
                    .executes(context -> chat.submit(StringArgumentType.getString(context, "text"))))));
        return chat;
    }

    public void reset() {
        this.state.reset();
        this.dialogue.invalidatePoll();
        this.acknowledgement = null;
    }

    public void poll() {
        Context context = context();
        if (context == null) return;
        this.dialogue.poll(context.session(), snapshot -> {
            if (!isCurrent(context)) return;
            this.state.receive(snapshot, DogidoDialogueChat::insert);
            flushAcknowledgements(context);
        });
    }

    private int usage() {
        String mode = context() == null ? "接続待ち" : this.state.voiceEnabled() == null ? "状態確認中"
            : this.state.voiceEnabled() ? "文字＋音声" : "文字のみ";
        insert("[ドギド設定] " + mode + "。/dogido <文章>・/dogido voice on（文字＋音声）・/dogido voice off（文字のみ）");
        return 1;
    }

    private int submit(String input) {
        String text = input.strip();
        String invalid = DialoguePayload.inputError(text);
        if (invalid != null) {
            insert("[ドギド送信] " + (invalid.equals("text_too_long") ? "文章は1000文字以内で入力してください。" : "文章を入力してください。"));
            return 0;
        }
        Context context = context();
        if (context == null) {
            insert("[ドギド送信] 未送信: サーバーへの接続を待っています。");
            return 0;
        }
        Boolean voice = switch (text) {
            case "voice on" -> true;
            case "voice off" -> false;
            default -> null;
        };
        DialoguePayload.Scope scope = this.state.scope();
        List<String> pending = this.state.pending();
        CompletableFuture<DogidoDialogueClient.Operation> operation = voice == null
            ? this.dialogue.text(context.session(), pending, text)
            : this.dialogue.voice(context.session(), pending, voice);
        operation.whenComplete((result, error) -> MinecraftClient.getInstance().execute(() -> {
            if (!isCurrent(context)) return;
            if (error != null) {
                insert("[ドギド送信] 通信に失敗しました。受理されたか確認できないため、自動再送はしていません。");
                return;
            }
            this.state.acknowledge(scope, result.acknowledgement());
            DialoguePayload.Reply reply = result.reply();
            if (!reply.accepted()) {
                insert("[ドギド送信] " + failure(reply.reason()));
            } else if (voice == null) {
                insert("[あなた → ドギド] " + text);
                insert("[ドギド送信] 受理" + (reply.reason().isBlank() ? "" : "（" + reply.reason() + "）"));
            } else if (reply.voiceEnabled() == null || reply.voiceGeneration().isBlank()) {
                insert("[ドギド設定] 応答を確認できません。現在の状態は次の取得で確認します。");
            } else {
                this.dialogue.invalidatePoll();
                this.state.voiceChanged(reply);
                this.events.rememberVoicePreference(reply.voiceEnabled());
                boolean saved = this.saveVoiceEnabled.test(reply.voiceEnabled());
                insert("[ドギド設定] " + (reply.voiceEnabled() ? "文字＋音声" : "文字のみ")
                    + (saved ? "にしました。" : "にしました。設定保存に失敗したため、次回起動には引き継げません。"));
            }
        }));
        return 1;
    }

    private void flushAcknowledgements(Context context) {
        List<String> pending = this.state.pending();
        if (pending.isEmpty() || this.acknowledgement != null) return;
        DialoguePayload.Scope scope = this.state.scope();
        CompletableFuture<DialoguePayload.Acknowledgement> request = this.dialogue.acknowledge(context.session(), pending);
        this.acknowledgement = request;
        request.whenComplete((reply, error) -> MinecraftClient.getInstance().execute(() -> {
            if (this.acknowledgement == request) this.acknowledgement = null;
            if (isCurrent(context) && error == null) this.state.acknowledge(scope, reply);
            // Unconfirmed IDs remain pending. A later poll retries without inserting the text again.
        }));
    }

    private Context context() {
        MinecraftClient client = MinecraftClient.getInstance();
        DogidoEventClient.SessionRef session = this.events.currentSession();
        return client.world == null || client.player == null || session == null ? null
            : new Context(session, client.world, client.getNetworkHandler());
    }

    private boolean isCurrent(Context context) {
        MinecraftClient client = MinecraftClient.getInstance();
        return this.events.isCurrent(context.session()) && client.world == context.world()
            && client.player != null && client.getNetworkHandler() == context.connection();
    }

    private static void insert(String text) {
        MinecraftClient.getInstance().inGameHud.getChatHud().addMessage(Text.literal(text));
    }

    private static String failure(String reason) {
        return switch (reason) {
            case "display_ack_failed" -> "未送信: 直前の返答の表示確認ができませんでした。接続回復後にもう一度入力してください。";
            case "connection_failed" -> "通信に失敗しました。受理されたか確認できないため、自動再送はしていません。";
            case "invalid_response" -> "応答を確認できません。受理されたか不明のため、自動再送はしていません。";
            default -> "受理されませんでした（" + (reason.isBlank() ? "理由なし" : reason) + "）。";
        };
    }
}
