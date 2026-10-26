package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import net.minecraft.network.RegistryByteBuf;
import net.minecraft.network.codec.PacketCodec;
import net.minecraft.network.codec.PacketCodecs;
import net.minecraft.network.packet.CustomPayload;
import net.minecraft.util.Identifier;

/** 身体の要求は8192文字、注視結果台帳を含むsnapshotは16384文字上限のFabric payloadで往復させる。入力は不変の要求、出力はJSON通信枠で、HTTPとsessionは既存adapterが所有する。 */
public final class BodyPackets {
    private BodyPackets() {}
    public record Message(int version, String kind, StateRequest state, ActionRequest action, OwnerWorkObservation ownerWork, SpeechGazeObservation speechGaze, RecallRequest recall) {
        public Message(int version, String kind, StateRequest state, ActionRequest action) { this(version,kind,state,action,null,null,null); }
        public Message(int version, String kind, StateRequest state, ActionRequest action, OwnerWorkObservation ownerWork, SpeechGazeObservation speechGaze) {
            this(version,kind,state,action,ownerWork,speechGaze,null);
        }
    }
    public record Request(String json) implements CustomPayload {
        public static final Id<Request> ID = new Id<>(Identifier.of("dogido_experiment","body_request_v12"));
        public static final PacketCodec<RegistryByteBuf,Request> CODEC = PacketCodec.tuple(PacketCodecs.string(8192),Request::json,Request::new);
        @Override public Id<Request> getId() { return ID; }
    }
    public record Response(String json) implements CustomPayload {
        public static final Id<Response> ID = new Id<>(Identifier.of("dogido_experiment","body_snapshot_v12"));
        public static final PacketCodec<RegistryByteBuf,Response> CODEC = PacketCodec.tuple(PacketCodecs.string(BodySnapshotPayload.MAX_BYTES),Response::json,Response::new);
        @Override public Id<Response> getId() { return ID; }
    }
}
