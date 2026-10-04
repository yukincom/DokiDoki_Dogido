# RAGの初期検討記録（2026-07）

> **正本プランは [senryu-rag-plan.md](senryu-rag-plan.md)（現行方針）。**  
> ※ 以下は初期検討時のメモです。全 `data/` の LlamaIndex 前提や poetic の再ベクトル化は **採用していません**（カタログ直引きと二重化するため）。  
> ※ 状態機械・本体処理は現在 Rust（`dogido-rust`）へ移行完了しており、初期案にある Python / `py_trees` / `dogido_server/rag/` は現行構成ではありません。

---

**状態:** 初期案の記録。以下の配置・依存・作業案は現行の実装手順ではない。

本メモは初期検討段階の記録です。
当時想定していた「Simple Vector RAG」の検討経緯を参考資料として残しています。

## 1. 当時の構成との整合性
- **良い点**: 状態機械が強く、**LLM leaf**（aftermath/ambient/death）で既にLLM呼び出しあり。RAGをここに自然に挿入可能。
- **RAG挿入ポイント**:
  - haiku route / chat route のプロンプト前にRAGコンテキスト注入。
  - 川柳の「下手くそだけど教育的」部分を強化（観察ポイント・添削例をRAGから引き出す）。
  - 敵対mob定義（docs/monster-schema.md）やdata/をRAG知識源に。

## 2. 当時のRAG案（Simple Vector優先・後に方針変更）
**目標**: JSONイベント + 状態をRAGで補完 → 川柳/雑談の質向上。状態機械の優先制御は崩さない。

- **タイプ**: **Simple Vector RAG**。ライブラリ選定は未決とし、導入する場合も `dogido_server/rag/` 内に限定して、プロジェクト全体のワークフロー基盤にはしない。GraphRAGは不要。
- **LLM役割**:
  - **構築時**: ほぼ不要（embeddingモデルだけ）。
  - **運用時**: Retrieval後 → 既存の27B/35B-A3Bで生成（chat/haiku route活用）。
- **優先知識源**:
  - `data/` + `docs/monster-schema.md` + `docs/haiku-architecture.md`
  - モブ描写、怖がり反応例、川柳テンプレート、プレイヤー句保存データ

## 3. 当時のPython配置案（未採用）
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

## 4. 当時の着手候補（現行タスクではない）
- **PoC**: `data/`やmonster-schemaから知識投入 → 既存fixtureでRAG retrieveテスト。
- **優先**: 川柳routeから開始（矛盾検出→RAG参考→生成）。
- **モデル**: 持ってる **Qwen3.6-35B-A3B**をchat/haikuメインに。
