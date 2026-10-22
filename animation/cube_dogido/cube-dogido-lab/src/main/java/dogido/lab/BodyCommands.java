package dogido.lab;

import com.mojang.brigadier.arguments.DoubleArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import dogido.lab.api.BodyProtocol.*;
import java.util.Locale;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.command.argument.EntityArgumentType;
import net.minecraft.command.argument.Vec3ArgumentType;
import net.minecraft.entity.Entity;
import net.minecraft.server.command.CommandManager;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;

/** 身体単体の試験コマンドを受け、所有者を照合してモード・行動・調整値をserver身体へ渡す。結果はコマンド実行者へ返し、会話やHTTP sessionは作らない。 */
final class BodyCommands {
    private BodyCommands() {}
    static void initialize() {
        CommandRegistrationCallback.EVENT.register((dispatcher,registries,environment) -> {
            var root = CommandManager.literal("dogidobody").requires(s -> s.getPlayer() != null)
                .executes(c -> status(c.getSource()));
            root.then(CommandManager.literal("bind").then(CommandManager.argument("body",EntityArgumentType.entity()).executes(c -> {
                Entity entity = EntityArgumentType.getEntity(c,"body");
                if (!(entity instanceof DogidoEntity body)) return say(c.getSource(),"3Dドギドを指定してください。",false);
                String result = body.bind(c.getSource().getPlayerOrThrow());
                return say(c.getSource(),result.equals("bound") ? "持ち主を登録しました。FOLLOWを開始します。" : result,result.equals("bound"));
            })));
            for (String operation : new String[]{"direct","rest","wait","follow","explore","hold","retreat","safe","call","unbind"})
                root.then(CommandManager.literal(operation).executes(c -> operate(c.getSource(),operation)));
            root.then(CommandManager.literal("status").executes(c -> status(c.getSource())));
            root.then(CommandManager.literal("food").executes(c -> {
                DogidoEntity body = BodyManager.body(c.getSource().getPlayerOrThrow());
                if (body == null) return unavailable(c.getSource());
                FoodObservation food = body.foodObservation();
                for (FoodTarget target : food.targets()) say(c.getSource(),target.id()+" / "+target.itemId()+" / "+target.position(),true);
                return say(c.getSource(),"食料の見通し観測: "+food.targets().size()+"件"+(food.truncated() ? "（上限まで表示）" : ""),true);
            }));
            root.then(CommandManager.literal("move").then(CommandManager.argument("destination",Vec3ArgumentType.vec3()).executes(c -> {
                ServerPlayerEntity player = c.getSource().getPlayerOrThrow();
                DogidoEntity body = BodyManager.body(player);
                if (body == null) return unavailable(c.getSource());
                var p = Vec3ArgumentType.getVec3(c,"destination");
                return action(c.getSource(),body.localAction(player,Action.MOVE_TO,new Position(p.x,p.y,p.z),null));
            })));
            root.then(CommandManager.literal("eat").then(CommandManager.argument("food_id",StringArgumentType.greedyString()).executes(c -> {
                ServerPlayerEntity player = c.getSource().getPlayerOrThrow();
                DogidoEntity body = BodyManager.body(player);
                if (body == null) return unavailable(c.getSource());
                return action(c.getSource(),body.localAction(player,Action.EAT,null,StringArgumentType.getString(c,"food_id")));
            })));
            root.then(CommandManager.literal("tune")
                .then(CommandManager.argument("parameter",StringArgumentType.word()).suggests((c,b) -> {
                    for (String key : new String[]{"speed","catchup_speed","retreat_speed","follow_distance","call_distance","explore_min","explore_max","retreat_distance","warp_distance",
                            "wall_grace_ticks","wall_damage_interval","wall_damage","rest_delay_ticks","rest_heal_interval","rest_healing","respawn_delay_ticks"})
                        if (key.startsWith(b.getRemaining())) b.suggest(key);
                    return b.buildFuture();
                }).then(CommandManager.argument("value",DoubleArgumentType.doubleArg()).executes(c -> {
                    DogidoEntity body = BodyManager.body(c.getSource().getPlayerOrThrow());
                    if (body == null) return unavailable(c.getSource());
                    try {
                        String key = StringArgumentType.getString(c,"parameter");
                        double value = DoubleArgumentType.getDouble(c,"value");
                        body.tuning().set(key,value);
                        return say(c.getSource(),key+" = "+value+"（この個体の読み込み中だけ有効）",true);
                    } catch (IllegalArgumentException e) { return say(c.getSource(),e.getMessage(),false); }
                }))));
            dispatcher.register(root);
        });
    }
    private static int operate(ServerCommandSource source, String operation) {
        ServerPlayerEntity player = source.getPlayer();
        if (!player.isAlive() || player.isSpectator()) return say(source,"現在は身体を操作できません。",false);
        if (operation.equals("call")) {
            ActionResult result = BodyManager.recall(player);
            return say(source,"呼び寄せ: "+result.outcome()+" / "+result.reason(),result.outcome() == Outcome.ACCEPTED);
        }
        DogidoEntity body = BodyManager.body(player);
        if (body == null) return unavailable(source);
        switch (operation) {
            case "direct" -> body.localMode(Mode.DIRECT,false);
            case "rest" -> { return action(source,body.localAction(player,Action.REST,null,null)); }
            case "wait" -> { return action(source,body.localAction(player,Action.WAIT,null,null)); }
            case "follow" -> body.localMode(Mode.FOLLOW,false);
            case "explore" -> body.localMode(Mode.EXPLORE,false);
            case "hold" -> body.localMode(Mode.HOLD,false);
            case "retreat" -> body.localRetreat();
            case "safe" -> body.localSafe();
            case "unbind" -> body.unbind(player);
            default -> { return 0; }
        }
        return say(source,"身体操作: "+operation,true);
    }
    private static int action(ServerCommandSource source, ActionResult result) {
        return say(source,result.outcome()+" / "+result.reason(),result.outcome() == Outcome.ACCEPTED || result.outcome() == Outcome.SUCCEEDED);
    }
    private static int status(ServerCommandSource source) {
        ServerPlayerEntity player = source.getPlayer();
        Snapshot snapshot = BodyManager.snapshot(player);
        if (snapshot.position() == null) return say(source,snapshot.presence()+" / "+snapshot.reason(),true);
        Position p = snapshot.position();
        double horizontal = Math.hypot(p.x()-player.getX(),p.z()-player.getZ());
        Vitals v = snapshot.vitals();
        return say(source,String.format(Locale.ROOT,"%s / %s | HP %.1f/%.1f | 壁 %dtick | 復活待ち %dtick | 水平 %.2f | 座標 %.2f, %.2f, %.2f",
            snapshot.activity(),snapshot.reason(),v.health(),v.maxHealth(),v.wallTicks(),v.respawnRemainingTicks(),horizontal,p.x(),p.y(),p.z()),true);
    }
    private static int unavailable(ServerCommandSource source) { return say(source,"持ち主未登録、または登録した身体のチャンクが未読み込みです。",false); }
    private static int say(ServerCommandSource source, String text, boolean success) {
        if (success) source.sendFeedback(() -> Text.literal(text),false); else source.sendError(Text.literal(text));
        return success ? 1 : 0;
    }
}
