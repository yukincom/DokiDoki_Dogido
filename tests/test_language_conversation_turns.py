"""独立音声試験の会話正本。実TTS・モデル・ブラウザーは起動しない。"""

from copy import deepcopy
from threading import Event
from unittest.mock import Mock

from dogido_server.language_dialogue.conversation_turns import DialogueWorker, TurnLedger
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.voice_test import VoiceSession
from dogido_server.language_dialogue.voice_test_io import Inbox
from dogido_server.llm.player_chat_prompts import build_player_chat_messages


class CasualLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.structured_requests = []
        self.leaf_requests = []

    def generate_structured_json(self, request):
        self.structured_requests.append(deepcopy(request))
        current = request.details['current']
        return {
            'dialogue_act': 'casual',
            'topic': 'general',
            'relation': 'new',
            'question': current['text'],
            'target': '',
            'facet': 'other',
            'target_status': 'contextual',
            'alternatives': [],
            'evidence': [{'turn_id': current['turn_id'], 'quote': current['text']}],
            'search_terms': [],
            'clarification': '',
        }

    def generate_leaf_text(self, request):
        self.leaf_requests.append(deepcopy(request))
        return self.replies.pop(0)


class ManualWorker:
    def __init__(self):
        self.items = []
        self.session = None

    def submit(self, **item):
        self.items.append(item)
        return True

    def run_next(self):
        item = self.items.pop(0)
        self.session.receive({
            'kind': 'dialogue_work_result',
            'work_id': item['work_id'],
            'work_kind': item['work_kind'],
            'session_epoch': item['session_epoch'],
            'turn_id': item['turn_id'],
            'result': item['run'](),
            'error': '',
        })

    def close(self, **kwargs):
        return True


def session_with_dialogue(dialogue, *, worker=None):
    records = []
    inbox = Inbox(1, 8, records.append)
    playback = Mock()
    playback.valid.return_value = True
    session = VoiceSession(
        dialogue,
        playback,
        inbox,
        records.append,
        dialogue_worker=worker,
    )
    return session, records


def accepted_voice(session, text):
    session.handle({'kind': 'recognized', 'text': text, 'talk': True})


def latest_utterance_id(session):
    return session.playback.submit.call_args.kwargs['utterance_id']


def playback_result(session, status='completed'):
    utterance_id = latest_utterance_id(session)
    session.handle({
        'kind': 'playback_result',
        'utterance_id': utterance_id,
        'status': status,
    })
    return utterance_id


def test_turn_ledger_projects_only_completed_pairs_and_expires_them():
    now = [0.0]
    ledger = TurnLedger(clock=lambda: now[0], ttl_seconds=300)
    ledger.begin('failed', epoch=0, raw_text='聞こえた？', semantic_text='聞こえた？')
    ledger.routed('failed', route='player_chat', status='player_chat')
    ledger.select_reply('failed', reply='聞こえたで。', utterance_id='u1')
    ledger.queued('u1')
    ledger.started('u1')
    ledger.resolve('u1', 'failed', resolution='tts_exit')
    assert ledger.prompt_history() == 'プレイヤー: 聞こえた？'

    ledger.begin('heard', epoch=0, raw_text='今日は元気', semantic_text='今日は元気')
    ledger.routed('heard', route='player_chat', status='player_chat')
    ledger.select_reply('heard', reply='ええやん。', utterance_id='u2')
    ledger.queued('u2')
    ledger.resolve('u2', 'completed')
    assert ledger.prompt_history() == (
        'プレイヤー: 聞こえた？\nプレイヤー: 今日は元気\nドギド: ええやん。'
    )

    now[0] = 300
    assert ledger.prompt_history() == ''
    snapshots = {turn['turn_id']: turn for turn in ledger.snapshot()}
    assert snapshots['failed']['playback_status'] == 'failed'
    assert snapshots['heard']['playback_status'] == 'completed'


def test_cancelled_selected_reply_is_kept_for_diagnostics_but_not_prompt_history():
    ledger = TurnLedger(clock=lambda: 0)
    ledger.begin('t1', epoch=1, raw_text='続き', semantic_text='続き')
    ledger.routed('t1', route='player_chat', status='player_chat')
    ledger.select_reply('t1', reply='続けよか。', utterance_id='u1')
    changed = ledger.cancel_pending(resolution='interrupt')
    assert changed[0]['playback_status'] == 'cancelled'
    assert changed[0]['resolution'] == 'interrupt'
    assert ledger.prompt_history() == ''


def test_generation_cancel_does_not_override_dispatched_playback_truth():
    ledger = TurnLedger(clock=lambda: 0)
    ledger.begin('t1', epoch=1, raw_text='続き', semantic_text='続き')
    ledger.routed('t1', route='player_chat', status='player_chat')
    ledger.select_reply('t1', reply='最後まで言えたで。', utterance_id='u1')
    ledger.dispatched('u1')

    assert ledger.cancel_pending(resolution='combat') == []
    resolved = ledger.resolve('u1', 'completed')

    assert resolved is not None
    assert resolved['playback_status'] == 'completed'
    assert ledger.prompt_history() == 'プレイヤー: 続き\nドギド: 最後まで言えたで。'


def test_worker_busy_turn_never_enters_prompt_before_requeue():
    ledger = TurnLedger(clock=lambda: 0)
    ledger.begin('busy', epoch=0, raw_text='聞き直す', semantic_text='聞き直す')
    ledger.routed('busy', route='learning', status='worker_busy')
    ledger.finish_without_reply('busy', resolution='worker_busy')

    assert ledger.prompt_turns() == []


def test_player_chat_uses_only_previous_playback_completed_exchange():
    llm = CasualLLM('ええやん、絶好調やな。', 'その調子でいこか。')
    dialogue = LanguageDialogue(llm)
    session, _ = session_with_dialogue(dialogue)

    accepted_voice(session, '今日も絶好調だよ')
    assert llm.leaf_requests[0].details['conversation_history'] == ''
    first_turn = session.turns.snapshot()[0]
    assert first_turn['route'] == 'player_chat'
    assert first_turn['playback_status'] == 'queued'
    assert not any(item['role'] == 'assistant' for item in dialogue.history)

    utterance_id = latest_utterance_id(session)
    session.handle({'kind': 'playback_started', 'utterance_id': utterance_id})
    assert session.turns.snapshot()[0]['playback_status'] == 'started'
    playback_result(session)
    assert session.turns.snapshot()[0]['playback_status'] == 'completed'
    assert any(item['role'] == 'assistant' for item in dialogue.history)

    accepted_voice(session, 'うん、今日は洞窟に行きたい')
    assert llm.leaf_requests[1].details['conversation_history'] == (
        'プレイヤー: 今日も絶好調だよ\nドギド: ええやん、絶好調やな。'
    )
    assert llm.leaf_requests[1].kind == 'player_chat'


def test_failed_playback_keeps_user_input_but_not_unheard_reply_in_next_prompt():
    llm = CasualLLM('一つ目の返事', '二つ目の返事')
    dialogue = LanguageDialogue(llm)
    session, _ = session_with_dialogue(dialogue)

    accepted_voice(session, '一つ目')
    playback_result(session, 'failed')
    assert not any(item.get('text') == '一つ目の返事' for item in dialogue.history)

    accepted_voice(session, '二つ目')
    assert llm.leaf_requests[1].details['conversation_history'] == 'プレイヤー: 一つ目'


def test_completed_history_has_an_independent_five_minute_ttl():
    now = [0.0]
    llm = CasualLLM('最初の返事', '次の返事')
    dialogue = LanguageDialogue(llm, clock=lambda: now[0], ttl_seconds=300)
    session, _ = session_with_dialogue(dialogue)

    accepted_voice(session, '最初の話')
    playback_result(session)
    now[0] = 300
    dialogue.last_activity = now[0]  # 参加状態ではなく会話履歴TTLだけを検査する。
    accepted_voice(session, 'ずいぶん後の話')
    assert llm.leaf_requests[1].details['conversation_history'] == ''


def test_standalone_player_chat_prompt_states_that_world_is_not_observed():
    llm = CasualLLM('聞いてるで。')
    dialogue = LanguageDialogue(llm)
    dialogue.turn('今日は元気', turn_id='t1', conversation_history='')
    messages = build_player_chat_messages(llm.leaf_requests[0])
    prompt = '\n'.join(message['content'] for message in messages)
    assert 'Minecraft観測がない' in prompt
    assert '見たふりせず' in prompt


def test_background_turn_result_is_discarded_after_interrupt_epoch_changes():
    entered, release = Event(), Event()
    records = []
    inbox = Inbox(1, 8, records.append)
    dialogue = Mock(paused=False)
    dialogue.clock.return_value = 0
    dialogue.ttl_seconds = 300
    dialogue.research_ttl_seconds = 1800
    dialogue.last_activity = 0
    dialogue.mode = 'normal'
    dialogue.cancel.return_value = {'control': 'cancel'}
    dialogue.interrupt.return_value = {'control': 'interrupt'}
    dialogue.release.return_value = {'control': 'release'}

    def turn(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {'turn_id': kwargs['turn_id'], 'status': 'player_chat', 'reply': '古い返事'}

    dialogue.turn.side_effect = turn
    playback = Mock()
    playback.valid.return_value = True
    holder = {}
    worker = DialogueWorker(lambda event: holder['session'].receive(event))
    session = VoiceSession(dialogue, playback, inbox, records.append, dialogue_worker=worker)
    holder['session'] = session
    try:
        accepted_voice(session, '古い入力')
        assert entered.wait(2)
        session.command('/interrupt')
        release.set()
        while True:
            event = inbox.get()
            session.handle(event)
            if any(row.get('kind') == 'dialogue_work_completed' for row in records):
                break
        playback.submit.assert_not_called()
        assert session.turns.snapshot() == []
    finally:
        release.set()
        worker.close()


def test_background_worker_keeps_one_new_input_until_completed_history_is_ready():
    llm = CasualLLM('一つ目の返事', '二つ目の返事')
    dialogue = LanguageDialogue(llm)
    worker = ManualWorker()
    session, records = session_with_dialogue(dialogue, worker=worker)
    worker.session = session

    accepted_voice(session, '一つ目')
    assert not llm.leaf_requests and len(worker.items) == 1
    accepted_voice(session, '二つ目')
    assert any(row.get('kind') == 'input_deferred' for row in records)

    worker.run_next()
    session.handle(session.inbox.get())
    assert llm.leaf_requests[0].details['conversation_history'] == ''
    assert session._deferred_input['text'] == '二つ目'

    playback_result(session)
    assert worker.items[0]['work_kind'] == 'forecast'
    worker.run_next()
    session.handle(session.inbox.get())
    session.handle(session.inbox.get())  # deferred recognized input
    assert worker.items[0]['work_kind'] == 'turn'

    worker.run_next()
    session.handle(session.inbox.get())
    assert llm.leaf_requests[1].details['conversation_history'] == (
        'プレイヤー: 一つ目\nドギド: 一つ目の返事'
    )
