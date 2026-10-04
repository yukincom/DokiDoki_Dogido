//! ドギド本体の判断・会話・外部接続をまとめるライブラリ。
//!
//! HTTPの入口はserver、接続ごとの状態変更と生成・取消の配線はdialogueが担う。
//! combat/environmentは観測への反応、haiku/workshopは句の生成・共同編集を扱う。
//! LLMの提案は各ドメインで検証してから使い、採否・状態変更・保存はRustが決める。
//! Python補助の通信と寿命管理はpython_worker、録音・音声認識の制御はvoiceを参照する。

pub mod address;
pub mod assist;
pub mod catalog_editor;
pub mod chat_catalog;
pub mod chat_names;
pub mod chat_prompt;
pub mod chat_topics;
pub mod combat;
pub mod companion_prompt;
mod compat;
pub mod contextual_asr;
pub mod conversation_observation;
pub mod dialogue;
mod entry_catalog;
pub mod environment;
pub mod episode_log;
pub mod events;
pub mod foreground;
pub mod haiku;
pub mod python_worker;
pub mod haiku_memory;
pub mod haiku_prompt;
pub mod haiku_record;
pub mod haiku_response;
pub mod ingress;
pub mod input_context;
pub mod input_policy;
pub mod knowledge;
pub mod language;
pub mod light_plan;
pub mod llm;
pub mod memory_api;
pub mod mob_identity;
pub mod planner;
pub mod playback;
pub mod player_text;
pub mod poem_book;
pub mod poem_input;
pub mod reading_correction;
pub mod recall_query;
mod runtime_settings;
pub mod server;
pub mod speech_choice;
mod text_format;
pub mod threats;
pub mod tts_reading;
pub mod types;
pub mod villager_routines;
pub mod vocalization;
pub mod voice;
pub mod workshop;
pub mod workshop_candidate;
pub mod workshop_combat;
pub mod workshop_combat_input;
pub mod workshop_edit;
pub mod workshop_followup;
pub mod workshop_target;

pub mod reaction_leaf;

pub mod chat_validation;

pub mod workshop_input_guard;

pub mod chat_hints;

pub mod chat_world;

pub mod catalog_knowledge;
pub mod mob_environment;
pub mod world_catalog;

pub mod chat_observation;

pub mod chat_materials;

pub mod workshop_projection;

pub mod workshop_editing;
