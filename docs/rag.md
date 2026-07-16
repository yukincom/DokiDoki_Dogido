> **正本プランは [senryu-rag-plan.md](senryu-rag-plan.md)（2026-07 改訂）。**  
> 以下は初期メモ。全 `data/` の LlamaIndex 前提や poetic の再ベクトル化は **採用しない**（直引きと二重になるため）。

---

**✅ Dogido最新状況を反映したRAG導入計画（2026-07時点・初期メモ）**

ユキ、ありがとう！ GitHub（https://github.com/yukincom/DokiDoki_Dogido）を確認したよ。  
**勘違い修正**：すでに `dogido_server` がかなり進んでいて、**状態機械（py_trees統合）・イベント受信API・LLM routes（chat / haiku分離）・Fabric adapter・voice_input** が実装済み。LLMは低遅延部分を避け、雑談/川柳で使ってる形。

これを基準に**Simple Vector RAG**の計画を立て直した。

### 1. 現状との整合性
- **良い点**: 状態機械が強く、**LLM leaf**（aftermath/ambient/death）で既にLLM呼び出しあり。RAGをここに自然に挿入可能。
- **RAG挿入ポイント**:
  - haiku route / chat route のプロンプト前にRAGコンテキスト注入。
  - 川柳の「下手くそだけど教育的」部分を強化（観察ポイント・添削例をRAGから引き出す）。
  - 敵対mob定義（docs/monster-schema.md）やdata/をRAG知識源に。

### 2. 更新されたRAG導入方針（Simple Vector優先）
**目標**: JSONイベント + 状態をRAGで補完 → 川柳/雑談の質向上。状態機械の優先制御は崩さない。

- **タイプ**: **Simple Vector RAG**（Chroma + LlamaIndex or LangChain最小）。GraphRAGは不要。
- **LLM役割**:
  - **構築時**: ほぼ不要（embeddingモデルだけ）。
  - **運用時**: Retrieval後 → 既存の27B/35B-A3Bで生成（chat/haiku route活用）。
- **優先知識源**:
  - `data/` + `docs/monster-schema.md` + `docs/haiku-architecture.md`
  - モブ描写、怖がり反応例、川柳テンプレート、プレイヤー句保存データ

### 3. 実装コードプラン（dogido_server拡張）
1. **新モジュール作成** (`dogido_server/rag/`):
   ```python
   # dogido_server/rag/vector_store.py
   from llama_index.core import VectorStoreIndex, StorageContext
   from llama_index.vector_stores.chroma import ChromaVectorStore
   from llama_index.embeddings.huggingface import HuggingFaceEmbedding
   import chromadb

   class DogidoRAG:
       def __init__(self):
           db = chromadb.PersistentClient(path="data/rag_index")
           collection = db.get_or_create_collection("dogido_knowledge")
           vector_store = ChromaVectorStore(chroma_collection=collection)
           self.index = VectorStoreIndex.from_vector_store(vector_store, embed_model=...)
       
       def retrieve(self, event, state):  # event_json + 現在の状態機械状態
           query = f"{event.visual_threats} {state.current} 川柳 怖がり"
           return self.index.as_retriever(similarity_top_k=3).retrieve(query)
   ```

2. **LLM Route統合** (`dogido_server/llm_routes.py`あたり):
   - haiku/chat生成前に `rag_context = rag.retrieve(event, tree_state)`
   - プロンプトに注入

3. **py_trees連携**:
   - LLM leafでRAG呼び出しを追加（ambient/death時特に有効）。

### 4. 次アクション（即実行可能）
- **PoC**: `data/`やmonster-schemaから知識投入 → 既存fixtureでRAG retrieveテスト。
- **優先**: 川柳routeから開始（矛盾検出→RAG参考→生成）。
- **モデル**: 持ってる **Qwen3.6-35B-A3B**をchat/haikuメインに。
