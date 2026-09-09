"""音声ホストの配線検査。実マイク・TTS・モデル・ブラウザーは起動しない。"""

from array import array
import io
import json
from pathlib import Path
import queue
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dogido_server.config import Settings
from dogido_server.language_dialogue.voice_test import VoiceSession, preflight
from dogido_server.language_dialogue.voice_test_io import Inbox, Microphone, Playback, capture_worker
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.contracts import ParticipationAssessment
from dogido_server.language_dialogue.participation import ParticipationState
from dogido_server.language_dialogue.participation_planner import fallback_forecast
from test_language_web_consent import proposed, approve


class Process:
    def __init__(self, code=0):
        self.returncode = code
        self.killed = self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('fake-audio', timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class Backend:
    def __init__(self, code=0):
        self.process = Process(code)
        self.started = []

    def prepare(self, text, **kwargs):
        return text

    def start_prepared(self, text):
        self.started.append(text)
        return SimpleNamespace(process=self.process, cleanup_path=None)


def playback_event(events):
    while True:
        event = events.get(timeout=2)
        if event['kind'] == 'playback_result':
            return event


@pytest.mark.parametrize('code,status', [(0, 'completed'), (1, 'failed')])
def test_only_actual_zero_process_exit_completes(code, status):
    events = queue.Queue()
    player = Playback(Backend(code), events.put, max_pending=1, speed_scale=.88)
    try:
        player.submit('ほな一緒にいこか！', utterance_id='original', departure=True)
        result = playback_event(events)
        assert result['status'] == status
        assert result['utterance_id'] == 'original' and result['departure']
    finally:
        assert player.close()


def test_prepare_is_not_playback_and_cancel_cannot_start_prepared_audio():
    entered, release = threading.Event(), threading.Event()
    events = queue.Queue()
    backend = Backend()

    def prepare(text, **kwargs):
        entered.set()
        assert release.wait(2)
        return text

    backend.prepare = prepare
    player = Playback(backend, events.put, max_pending=1, speed_scale=.88)
    try:
        player.submit('案内', departure=True)
        assert entered.wait(2) and events.empty()
        player.cancel()
        release.set()
        assert playback_event(events)['status'] == 'cancelled'
        assert not backend.started
    finally:
        release.set()
        assert player.close()


def test_cancel_running_audio_terminates_only_owned_process():
    events, backend = queue.Queue(), Backend(None)
    player = Playback(backend, events.put, max_pending=1, speed_scale=.88)
    try:
        player.submit('音声')
        assert events.get(timeout=2)['kind'] == 'playback_started'
        player.cancel()
        assert playback_event(events)['status'] == 'cancelled'
        assert backend.process.terminated
    finally:
        assert player.close()


class Planner:
    def __init__(self, *assessments):
        self.assessments = list(assessments)
        self.forecast_calls = []
        self.assess_calls = []

    def forecast(self, text, reply, **kwargs):
        self.forecast_calls.append((text, reply, kwargs))
        forecast = fallback_forecast(
            text,
            reply,
            response_status=kwargs['response_status'],
            needs_reaction=kwargs['needs_reaction'],
        )
        if kwargs['needs_reaction']:
            forecast = forecast.model_copy(update={'reaction': 'そっか。聞いてるで。'})
        return forecast, 'accepted'

    def assess(self, text, **kwargs):
        self.assess_calls.append((text, kwargs))
        return self.assessments.pop(0)


def possible_aside(text='晩ごはんできたよ'):
    return ParticipationAssessment(
        relation='possibly_not_addressed',
        matched_pattern_ids=[],
        topic_changed=True,
        clear_question=False,
        minecraft_topic=False,
        evidence=text,
        confidence=.94,
    )


def expected(text='その続きやで'):
    return ParticipationAssessment(
        relation='expected',
        matched_pattern_ids=['p1'],
        topic_changed=False,
        clear_question=False,
        minecraft_topic=False,
        evidence=text,
        confidence=.9,
    )


def fake_session(dialogue=None, planner=None):
    records = []
    inbox = Inbox(1, 8, records.append)
    if dialogue is None:
        dialogue = Mock(paused=False)
        dialogue.cancel.return_value = {'control': 'cancel'}
        dialogue.interrupt.return_value = {'control': 'interrupt'}
        dialogue.release.return_value = {'control': 'release'}
        dialogue.turn.return_value = {'reply': '返事'}
        dialogue.research = None
        dialogue.ttl_seconds = 300
        dialogue.research_ttl_seconds = 1800
        dialogue.last_activity = 0
        dialogue.clock.return_value = 0
        dialogue.mode = 'normal'
    player = Mock()
    player.valid.return_value = True
    session = VoiceSession(
        dialogue, player, inbox, records.append, participation_planner=planner,
    )
    return session, records


def complete_latest_reply(session, *, status='completed'):
    call = session.playback.submit.call_args
    assert call is not None
    session.handle({
        'kind': 'playback_result',
        'utterance_id': call.kwargs['utterance_id'],
        'status': status,
        'epoch': call.kwargs.get('epoch'),
        'departure': call.kwargs.get('departure', False),
        'refresh_token': call.kwargs.get('refresh_token'),
    })


def test_stt_is_received_during_playback_without_muting_or_text_matching():
    session, records = fake_session()
    session.receive({'kind': 'playback_started', 'text': '同じ文'})
    session.receive({'kind': 'recognized', 'text': '同じ文'})
    assert records[-1]['kind'] == 'recognized'
    session.handle(session.inbox.get())  # playback_started
    session.handle(session.inbox.get())  # recognized
    assert session.dialogue.turn.call_args.args == ('同じ文',)
    assert session.dialogue.turn.call_args.kwargs['source'] == 'voice'
    session.playback.cancel.assert_not_called()


def test_listen_keeps_recognition_but_stops_automatic_reply_and_allows_probe():
    session, records = fake_session()
    session.ready = True
    session.command('/listen')
    session.receive({'kind': 'recognized', 'text': '石炭'})
    session.handle(session.inbox.get())  # wake
    session.handle(session.inbox.get())
    session.dialogue.turn.assert_not_called()
    assert any(row.get('text') == '石炭' for row in records)
    session.command('/say')
    session.playback.submit.assert_called_once()


def test_idle_voice_becomes_quiet_and_only_explicit_name_call_resumes():
    session, records = fake_session()
    session.must_accept_next = False
    session.dialogue.clock.return_value = 301
    session.receive({'kind': 'recognized', 'text': '口に突っ込んだ感じ'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.QUIET
    session.dialogue.turn.assert_not_called()
    assert records[-1]['status'] == 'quiet_not_called'

    session.receive({'kind': 'recognized', 'text': 'ドギドの声が聞こえた'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.QUIET
    session.dialogue.turn.assert_not_called()

    session.receive({'kind': 'recognized', 'text': 'ドギド、きょうって何音？'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.ACTIVE
    assert session.dialogue.turn.call_args.args == ('きょうって何音?',)
    assert any(row.get('reason') == 'direct_call' for row in records)


def test_participation_quiets_after_five_minutes_even_when_research_context_is_longer():
    session, records = fake_session()
    session.must_accept_next = False
    session.dialogue.research = object()
    session.dialogue.clock.return_value = 301
    session.receive({'kind': 'recognized', 'text': '家族に話しただけ'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.QUIET
    session.dialogue.turn.assert_not_called()
    assert records[-1]['status'] == 'quiet_not_called'


def test_generic_handoff_does_not_infer_dialogue_closed_or_suppress_next_input():
    session, records = fake_session()
    session.deliver({
        'status': 'handoff',
        'handoff_topic': 'language',
        'mode_after': 'normal',
        'reply': '',
    }, epoch=0)
    assert session.participation is ParticipationState.ACTIVE
    session.receive({'kind': 'recognized', 'text': '今エンダーマン'})
    session.handle(session.inbox.get())
    assert session.dialogue.turn.call_args.args == ('今エンダーマン',)
    assert not any(row.get('status') == 'quiet_not_called' for row in records)


def test_explicit_adventure_handoff_keeps_normal_voice_input_active():
    session, records = fake_session()
    session.deliver({
        'status': 'handoff',
        'handoff_topic': 'minecraft',
        'mode_after': 'normal',
        'reply': 'よし、冒険にもどろか！',
    }, epoch=0)
    assert session.participation is ParticipationState.ACTIVE
    complete_latest_reply(session)

    session.receive({'kind': 'recognized', 'text': '掘るっていう漢字は何年生で習うのかな'})
    session.handle(session.inbox.get())
    assert session.dialogue.turn.call_args.args == ('掘るっていう漢字は何年生で習うのかな',)
    assert not any(row.get('status') == 'quiet_not_called' for row in records)


def test_natural_availability_call_resumes_after_idle_without_broad_name_matching():
    session, records = fake_session()
    session.participation = ParticipationState.QUIET
    session.receive({'kind': 'recognized', 'text': 'あれドギドの声が聞こえた'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.QUIET
    session.dialogue.turn.assert_not_called()

    session.receive({'kind': 'recognized', 'text': 'あれドギドいる？'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.ACTIVE
    assert any(row.get('reply') == 'おるで。どないしたん？' for row in records)
    session.dialogue.turn.assert_not_called()


def test_name_only_call_acknowledges_and_reopens_dialogue_window():
    session, records = fake_session()
    session.participation = ParticipationState.QUIET
    session.dialogue.clock.return_value = 42
    session.receive({'kind': 'recognized', 'text': 'ドギド'})
    session.handle(session.inbox.get())
    assert session.participation is ParticipationState.ACTIVE
    assert session.dialogue.last_activity == 42
    assert any(row.get('reply') == 'おるで。どないしたん？' for row in records)
    session.dialogue.turn.assert_not_called()


def test_listen_and_talk_are_explicit_participation_states():
    session, _ = fake_session()
    session.command('/listen')
    assert session.participation is ParticipationState.MIC_OFF
    session.command('/talk')
    assert session.participation is ParticipationState.ACTIVE


def test_first_input_after_long_start_delay_is_always_answered_and_forecasted():
    planner = Planner()
    session, records = fake_session(planner=planner)
    session.dialogue.clock.return_value = 301
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリチプリキプリキ'})
    session.handle(session.inbox.get())

    assert session.participation is ParticipationState.ACTIVE
    session.dialogue.turn.assert_not_called()
    assert session.playback.submit.call_args.args[0] == '歌かいな。ご機嫌やな〜。'
    assert any(row.get('reason') == 'first_input_always_accept' for row in records)
    complete_latest_reply(session)
    forecast = next(row for row in records if row.get('kind') == 'participation_forecast')
    assert forecast['trusted'] and len(forecast['patterns']) == 5


def test_handoff_without_reply_is_not_replaced_by_participation_forecast():
    planner = Planner()
    session, records = fake_session(planner=planner)
    session.expected_continuations = fallback_forecast(
        '前の話', '前の返事', response_status='answer', needs_reaction=False,
    )
    session.expected_continuations_trusted = True
    session.dialogue.turn.return_value = {
        'status': 'handoff',
        'handoff_topic': 'general',
        'mode_after': 'normal',
        'reply': '',
    }
    session.receive({'kind': 'recognized', 'text': '今日は楽しかった'})
    session.handle(session.inbox.get())

    dialogue_row = next(row for row in records if row.get('kind') == 'dialogue')
    assert dialogue_row['status'] == 'handoff'
    assert dialogue_row['reply'] == ''
    session.playback.submit.assert_not_called()
    assert not planner.forecast_calls
    assert session.expected_continuations is None
    assert not session.expected_continuations_trusted


def test_high_confidence_topic_discontinuity_is_logged_but_not_sent_to_dialogue():
    planner = Planner((possible_aside(), 'accepted'))
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': '晩ごはんできたよ'})
    session.handle(session.inbox.get())

    session.dialogue.turn.assert_not_called()
    suppressed = [row for row in records if row.get('status') == 'possibly_not_addressed']
    assert len(suppressed) == 1
    assert suppressed[0]['raw_text'] == '晩ごはんできたよ'
    assert suppressed[0]['marker'] == 'possibly_not_addressed'
    assert suppressed[0]['participation_assessment']['confidence'] == .94
    assert session.possible_aside['items'][0]['text'] == '晩ごはんできたよ'


def test_side_conversation_return_resolves_pending_block_and_replies_without_history_mix():
    planner = Planner((possible_aside(), 'accepted'))
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': '晩ごはんできたよ'})
    session.handle(session.inbox.get())
    session.receive({'kind': 'recognized', 'text': 'うるさくてごめんね'})
    session.handle(session.inbox.get())

    assert session.possible_aside is None
    assert session.playback.submit.call_args.args[0] == 'ええんやで。'
    resolution = next(
        row for row in records
        if row.get('resolution') == 'side_conversation_resolved'
    )
    assert resolution['resolution_cue'] == 'うるさくてごめんね'
    assert session.dialogue.turn.call_count == 0


def test_false_suppression_correction_replays_only_latest_pending_input_once():
    text = '今日は宅配便が遅かった'
    planner = Planner((possible_aside(text), 'accepted'))
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': text})
    session.handle(session.inbox.get())
    session.receive({'kind': 'recognized', 'text': 'ドギドに言ったんだけど'})
    session.handle(session.inbox.get())

    assert session.possible_aside is None
    assert session.dialogue.turn.call_count == 1
    assert session.dialogue.turn.call_args.args == (text,)
    assert session.playback.submit.call_args.args[0].startswith('ごめん、オレに言うてたんやな。')
    assert any(row.get('resolution') == 'false_positive_recovered' for row in records)


@pytest.mark.parametrize('text,reason', [
    ('掘るっていう漢字は何年生で習うのかな', 'clear_question'),
    ('今エンダーマン', 'minecraft_topic'),
    ('ところで晩ごはんできたよ', 'topic_shift_cue'),
    ('あれドギドいる？', 'direct_call'),
])
def test_explicit_acceptance_signals_bypass_model_suppression(text, reason):
    planner = Planner()
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': text})
    session.handle(session.inbox.get())

    assert not planner.assess_calls
    assert session.dialogue.turn.call_args.args == (text,)
    assert any(row.get('reason') == reason for row in records)


@pytest.mark.parametrize('assessment', [
    ParticipationAssessment(
        relation='possibly_not_addressed', matched_pattern_ids=[], topic_changed=True,
        clear_question=False, minecraft_topic=False, evidence='少し違う話', confidence=.7,
    ),
    ParticipationAssessment(
        relation='uncertain', matched_pattern_ids=[], topic_changed=True,
        clear_question=False, minecraft_topic=False, evidence='少し違う話', confidence=.96,
    ),
])
def test_uncertain_or_low_confidence_assessment_fails_open(assessment):
    planner = Planner((assessment, 'accepted'))
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': '少し違う話'})
    session.handle(session.inbox.get())

    assert session.dialogue.turn.call_args.args == ('少し違う話',)
    assert session.possible_aside is None
    assert any(row.get('reason') == 'uncertain_fail_open' for row in records)


def test_unavailable_assessment_fails_open_instead_of_suppressing_input():
    planner = Planner((None, 'invalid_payload'))
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': '晩ごはんできたよ'})
    session.handle(session.inbox.get())

    assert session.dialogue.turn.call_args.args == ('晩ごはんできたよ',)
    assert session.possible_aside is None
    assert any(row.get('reason') == 'assessment_unavailable' for row in records)


def test_possible_side_conversation_log_is_bounded_to_five_recent_inputs():
    texts = [f'家族の話その{index}' for index in range(6)]
    planner = Planner(*[(possible_aside(text), 'accepted') for text in texts])
    session, _ = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    for text in texts:
        session.receive({'kind': 'recognized', 'text': text})
        session.handle(session.inbox.get())

    assert [item['text'] for item in session.possible_aside['items']] == texts[-5:]
    session.dialogue.turn.assert_not_called()


def test_predicted_continuation_is_accepted_and_refreshes_five_patterns():
    planner = Planner((expected(), 'accepted'))
    session, records = fake_session(planner=planner)
    session.receive({'kind': 'recognized', 'text': 'プリプリプリプリ'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)
    session.receive({'kind': 'recognized', 'text': 'その続きやで'})
    session.handle(session.inbox.get())
    complete_latest_reply(session)

    assert session.dialogue.turn.call_args.args == ('その続きやで',)
    assert len(planner.forecast_calls) == 2
    assert planner.forecast_calls[1][2]['previous_accepted']['user_text'] == 'プリプリプリプリ'
    assert any(row.get('reason') == 'predicted_continuation' for row in records)


@pytest.mark.parametrize('command', ['/cancel', '/interrupt', '/listen', '/quit'])
def test_cancel_discards_queued_input_and_old_reply(command):
    session, records = fake_session()
    session.receive({'kind': 'recognized', 'text': '古い発話'})
    previous_epoch = session.epoch
    session.command(command)
    session.handle(session.inbox.get())
    session.deliver({'reply': '古い返事'}, epoch=previous_epoch)
    session.dialogue.turn.assert_not_called()
    session.playback.submit.assert_not_called()


def test_cancel_between_host_check_and_controller_start_does_not_revive_state():
    dialogue = LanguageDialogue(Mock())
    row = dialogue.turn('取り消した言葉', turn_id='old', source='voice', cancelled=lambda: True)
    assert row['status'] == 'interrupted'
    assert not dialogue.history and not dialogue.focus.question
    dialogue.llm.generate_structured_json.assert_not_called()


@pytest.mark.parametrize('event', [
    {'kind': 'capture_stopped'}, {'kind': 'capture_error'},
    {'kind': 'diagnostic', 'reason': 'microphone_stopped'},
    {'kind': 'diagnostic', 'reason': 'aec_failed'},
])
def test_capture_failure_invalidates_consent_before_delayed_playback(event):
    dialogue, web, _ = proposed()
    utterance_id = approve(dialogue)
    session, _ = fake_session(dialogue)
    session.receive(event)
    session.handle(dict(kind='playback_result', utterance_id=utterance_id,
                        departure=True, status='completed'))
    assert session.stopping.is_set() and not web.calls


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_actual_playback_notification_routes_original_id_and_never_repeats(status):
    dialogue, web, _ = proposed()
    utterance_id = approve(dialogue)
    session, records = fake_session(dialogue)
    event = dict(kind='playback_result', utterance_id=utterance_id, departure=True, status=status)
    session.handle(event)
    assert len(web.calls) == int(status == 'completed')
    if status == 'completed':
        assert any(row.get('status') == 'awaiting_report' for row in records)
        session.playback.submit.assert_not_called()
    session.handle(event)
    assert len(web.calls) == int(status == 'completed')


def test_forged_keyboard_completion_is_not_supported():
    dialogue, web, _ = proposed()
    approve(dialogue)
    session, _ = fake_session(dialogue)
    session.command('/speech-completed')
    assert not web.calls


def test_welcome_refresh_waits_for_matching_completed_audio():
    session, _ = fake_session()
    session.dialogue.observe_minecraft_focus.return_value = {'reply': 'おかえり', 'refresh_token': 'visit'}
    session.dialogue.refresh_after_return.return_value = {'reply': '', 'status': 'context_ready'}
    session.handle({'kind': 'focus', 'active': True})
    session.dialogue.refresh_after_return.assert_not_called()
    assert session.playback.submit.call_args.kwargs['refresh_token'] == 'visit'
    session.handle(dict(kind='playback_result', status='completed', refresh_token='visit'))
    assert session.dialogue.refresh_after_return.call_args.args == ('visit',)
    assert not session.dialogue.refresh_after_return.call_args.kwargs['host_cancelled']()


def test_inbox_replaces_stale_voice_but_keeps_playback_and_controls():
    now, records = [0], []
    inbox = Inbox(1, 8, records.append, clock=lambda: now[0])
    inbox.put({'kind': 'recognized', 'text': 'old'})
    inbox.put({'kind': 'playback_result'})
    inbox.put({'kind': 'recognized', 'text': 'new'})
    assert records[0]['reason'] == 'pending_replaced'
    now[0] = 9
    inbox.put({'kind': 'wake'})
    assert inbox.get()['kind'] == 'playback_result'
    assert inbox.get()['kind'] == 'wake'
    assert records[-1]['reason'] == 'stale'


def test_concurrent_microphone_close_waits_for_cleanup_completion():
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    microphone = Microphone.__new__(Microphone)
    microphone.closed, microphone.close_lock = threading.Event(), threading.Lock()
    microphone.reader = Mock()
    microphone.temporary = Mock()
    def close():
        entered.set()
        assert release.wait(2)
    microphone.capture = SimpleNamespace(close=close)
    one = threading.Thread(target=microphone.close)
    two = threading.Thread(target=lambda: (microphone.close(), completed.set()))
    try:
        one.start()
        assert entered.wait(2)
        two.start()
        assert not completed.wait(.05)
    finally:
        release.set()
        one.join(2)
        two.join(2)
    assert completed.is_set()
    microphone.reader.join.assert_called_once()
    microphone.temporary.cleanup.assert_called_once()


def test_host_cancelled_departure_and_refresh_do_not_start_web():
    dialogue, web, _ = proposed()
    utterance_id = approve(dialogue)
    row = dialogue.on_speech_playback_result(utterance_id, status='completed', event_id='late',
                                            cancelled=lambda: True)
    assert row['status'] == 'interrupted' and not web.calls
    assert dialogue.refresh_after_return('stale', host_cancelled=lambda: True)['status'] == 'interrupted'


def test_host_cancel_after_completion_precheck_is_checked_again_before_search():
    dialogue, web, _ = proposed()
    utterance_id = approve(dialogue)
    checks = iter([False, True, True])
    row = dialogue.on_speech_playback_result(utterance_id, status='completed', event_id='late',
                                            cancelled=lambda: next(checks))
    assert row['status'] == 'interrupted' and not web.calls


def test_capture_worker_protocol_has_text_not_pcm_and_no_server_callbacks(monkeypatch, capsys):
    import dogido_server.voice_input as voice
    def main(**kwargs):
        assert kwargs['settings'].voice_echo_cancellation == 'webrtc'
        kwargs['on_ready']()
        kwargs['on_transcript']('石炭')
        kwargs['diagnostic_sink'](reason='stt_finished')
        kwargs['on_stopped']()
        print('legacy capture line')
    monkeypatch.setattr(voice, 'main', main)
    monkeypatch.setattr('signal.signal', lambda *args: None)
    capture_worker()
    output = capsys.readouterr()
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert [row['kind'] for row in rows] == ['capture_ready', 'recognized', 'diagnostic', 'capture_stopped']
    assert rows[1]['text'] == '石炭'
    assert 'legacy capture line' in output.err


@pytest.mark.parametrize('independent', [True, False])
def test_voice_input_hooks_share_segmenter_and_default_keeps_http(monkeypatch, independent):
    import dogido_server.voice_input as voice
    seen, diagnostics = [], []
    settings = Settings(_env_file=None).model_copy(update={
        'voice_echo_cancellation': 'off', 'voice_vad_enabled': False,
        'voice_silence_ms': 30, 'voice_min_speech_ms': 30, 'voice_wake_word': '',
    })
    loud = array('h', [2000] * 480).tobytes()
    frames = iter([loud, b'\0' * 960, b''])
    capture = SimpleNamespace(stdout=io.BytesIO(), read_frame=lambda size: next(frames),
                              error_tail=lambda: '', close=lambda: seen.append('capture_closed'))
    class Worker:
        def __init__(self, process, **kwargs): self.process = process
        def submit(self, segment): self.process(segment); return True
        def close(self): seen.append('stt_closed')
    class Diagnostic:
        def __init__(self, send): self.send = send
        def __call__(self, **fields): self.send(**fields)
        def close(self): pass
    monkeypatch.setattr(voice, 'resolve_whisper_paths', lambda settings: (Path('cli'), Path('model')))
    monkeypatch.setattr(voice, 'resolve_vad_paths', lambda *args: None)
    monkeypatch.setattr(voice, 'spawn_capture', lambda *args, **kwargs: capture)
    monkeypatch.setattr(voice, 'SpeechRecognitionWorker', Worker)
    monkeypatch.setattr(voice, 'VoiceDiagnosticDispatcher', Diagnostic)
    monkeypatch.setattr(voice, 'transcribe_with_empty_retry', lambda *args, **kwargs: 'きょう')
    context, delivery, reporting = Mock(return_value='normal'), Mock(), Mock()
    monkeypatch.setattr(voice, 'fetch_voice_prompt_mode', context)
    monkeypatch.setattr(voice, 'deliver', delivery)
    monkeypatch.setattr(voice, 'report_voice_diagnostic', reporting)
    callbacks = dict(on_transcript=lambda text: seen.append(text),
                     diagnostic_sink=lambda **event: diagnostics.append(event),
                     on_ready=lambda: seen.append('ready'), on_stopped=lambda: seen.append('stopped'))
    voice.main(settings=settings, **(callbacks if independent else {}))
    if independent:
        assert seen == ['ready', 'きょう', 'stopped', 'capture_closed', 'stt_closed']
        assert any(d.get('reason') == 'recognized' for d in diagnostics)
        context.assert_not_called(); delivery.assert_not_called(); reporting.assert_not_called()
    else:
        context.assert_called_once(); delivery.assert_called_once(); assert reporting.called


def test_preflight_rejects_missing_enabled_silero_before_connecting_tts(monkeypatch):
    import dogido_server.voice_input as voice
    settings = Settings(_env_file=None).model_copy(update={'llm_backend': 'mlx', 'chat_llm_backend': 'mlx'})
    monkeypatch.setattr('dogido_server.language_dialogue.voice_test.find_spec', lambda name: object())
    monkeypatch.setattr(voice, 'resolve_whisper_paths', lambda settings: (Path('cli'), Path('model')))
    monkeypatch.setattr(voice, 'resolve_vad_paths', lambda *args: None)
    with pytest.raises(RuntimeError, match='Silero VAD'):
        preflight(settings)
