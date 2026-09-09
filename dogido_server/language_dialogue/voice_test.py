"""ユーザー操作の独立音声試験: 実マイク/AEC/STT→国語対話→実TTS→同意後の実Chrome。"""

import argparse
from datetime import datetime, timezone
from importlib.util import find_spec
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
from urllib.parse import urlparse
from uuid import uuid4

from .conversation_turns import DialogueWorker, TurnLedger
from .participation import (
    ParticipationEvent,
    ParticipationState,
    companion_reaction,
    corrects_false_suppression,
    direct_call_body,
    has_topic_shift_cue,
    is_obvious_minecraft_topic,
    is_obvious_question,
    is_playful_vocalization,
    resolves_side_conversation,
    transition,
)
from .participation_planner import ParticipationPlanner, fallback_forecast
from .voice_test_io import Inbox, Microphone, Playback, capture_worker


PROBE = ('ドギドやで。いまはスピーカーから出た俺の声を、マイクが拾い直さへんか試してるんや。'
         '静かに聞く回と、俺が話してる途中に声を重ねる回を、分けて試してな。')
CONTROLS = '/listen /talk /say /cancel /interrupt /release /away /back /quit'
POSSIBLE_ASIDE_CONFIDENCE = 0.85


def preflight(settings):
    """依存・ローカル音声エンジンの読取のみ。録音/合成/再生/モデル/Chromeは開始しない。"""
    from dogido_server.voice_input import resolve_whisper_paths, resolve_vad_paths
    from dogido_server.voice_capture import echo_command
    from .chrome_web import DEFAULT_COMMAND, ROOT
    import httpx

    if settings.llm_route_settings('chat').llm_backend != 'mlx':
        raise RuntimeError('既存のMLX会話モデル設定が必要です。自動変更しません。')
    for name in ('mcp', 'mlx_lm'):
        if find_spec(name) is None:
            raise RuntimeError(f'既存Pythonの依存が不足しています: {name}')
    cli, model = resolve_whisper_paths(settings)
    vad = resolve_vad_paths(settings, cli)
    if settings.voice_vad_enabled and vad is None:
        raise RuntimeError('Silero VADが設定上有効ですが実行ファイル/モデルが見つかりません。')
    echo_command(settings)
    if not DEFAULT_COMMAND.is_file() or not os.access(DEFAULT_COMMAND, os.X_OK):
        raise RuntimeError('専用chrome-webが未導入です。')
    if json.loads((ROOT / 'scripts/chrome-web-child-config.json').read_text()).get('show_browser') is not True:
        raise RuntimeError('Chrome可視設定が無効です。')
    if not any((base / 'Google Chrome.app/Contents/MacOS/Google Chrome').is_file()
               for base in (Path('/Applications'), Path.home() / 'Applications')):
        raise RuntimeError('Google Chromeが見つかりません。')
    if settings.tts_backend == 'voicevox':
        url = urlparse(settings.voicevox_url)
        if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('この独立試験はローカルVOICEVOX専用です。外部TTSへ送信しません。')
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(settings.voicevox_url.rstrip('/') + '/version')
            response.raise_for_status()
        print(f'TTS: VOICEVOX / 話者 {settings.voicevox_speaker} / エンジン応答あり', flush=True)
    elif settings.tts_backend == 'say':
        print(f'TTS: 設定済みmacOS say ({settings.say_voice or "既定音声"})', flush=True)
    else:
        raise RuntimeError('TTSがnoopです。無音を再生成功として扱いません。')
    print(f'STT: {cli.name} / {model.name} / Silero={"準備済み" if vad else "設定で無効"}', flush=True)
    print('AEC・Chrome・MLX依存を確認。実機キャプチャ・再生・モデル実ロードはまだです。', flush=True)


class VoiceSession:
    def __init__(
        self,
        dialogue,
        playback,
        inbox,
        record,
        *,
        participation_planner=None,
        dialogue_worker=None,
        turn_ledger=None,
    ):
        self.dialogue, self.playback, self.inbox, self.record = dialogue, playback, inbox, record
        self.participation_planner = participation_planner
        self.dialogue_worker = dialogue_worker
        self.turns = turn_ledger or TurnLedger(
            clock=dialogue.clock,
            ttl_seconds=dialogue.ttl_seconds,
        )
        self.talk, self.ready = True, False
        self.participation = ParticipationState.ACTIVE
        self.must_accept_next = True
        self.expected_continuations = None
        self.expected_continuations_trusted = False
        self.last_accepted = {}
        self.possible_aside = None
        self._work_ids = set()
        self._work_context = {}
        self._deferred_input = None
        self._playback_context = {}
        self._pending_playback_ids = set()
        self.epoch = 0
        self.stopping = threading.Event()
        self.lock = threading.RLock()

    def command(self, command):
        # controllerの中断APIは生成中も有効。キーボード制御はSTT本文とは分離。
        with self.lock:
            if command == '/quit' and self.stopping.is_set():
                return
            if command in {'/quit', '/cancel', '/interrupt', '/listen'}:
                self.epoch += 1
                for turn in self.turns.cancel_pending(resolution=command.lstrip('/')):
                    self._record_turn_lifecycle(turn, 'cancelled')
                self._work_ids.clear()
                self._work_context.clear()
                self._deferred_input = None
                self._playback_context.clear()
                self._pending_playback_ids.clear()
                self.record(dict(kind='control', **(self.dialogue.interrupt() if command in {
                    '/quit', '/interrupt'
                } else self.dialogue.cancel())))
                self.playback.cancel()
            if command == '/quit':
                self._close_possible_aside('session_stopped')
                self._participation(ParticipationEvent.CAPTURE_STOPPED)
                self.stopping.set()
            elif command == '/cancel':
                self._close_possible_aside('cancelled_unconfirmed')
                self._reset_prediction(require_first=True)
            elif command == '/listen':
                self._close_possible_aside('listen_only_unconfirmed')
                self._reset_prediction(require_first=False)
                self.talk = False
                self._participation(ParticipationEvent.EXPLICIT_LISTEN)
            elif command == '/talk':
                self.talk = True
                self._close_possible_aside('explicit_talk_reset')
                self._reset_prediction(require_first=True)
                self._participation(ParticipationEvent.EXPLICIT_TALK)
                self.dialogue.release()
            elif command == '/release':
                self.record(dict(kind='control', **self.dialogue.release()))
            elif command == '/say':
                if self.ready:
                    self.playback.submit(PROBE)
                else:
                    self.record(dict(kind='notice', reason='capture_not_ready'))
            elif command in {'/away', '/back'}:
                self.inbox.put(dict(kind='focus', active=command == '/back', session_epoch=self.epoch))
            elif command not in {'/quit', '/cancel', '/interrupt', '/listen', '/talk'}:
                self.record(dict(kind='notice', reason='音声で話しかけてください。制御: ' + CONTROLS))
            self.record(dict(kind='mode', talk=self.talk, command=command))
        self.inbox.put(dict(kind='wake'))

    def close(self):
        if self.dialogue_worker is None:
            return True
        # 15秒の単発Web読み取りを中断通知後に抜ける猶予を持たせる。
        return self.dialogue_worker.close(timeout=20)

    def _record_turn_lifecycle(self, turn, lifecycle):
        if turn is None:
            return
        self.record(dict(kind='turn_lifecycle', lifecycle=lifecycle, turn=turn))

    def _confirm_route_reply(self, turn_id, reply):
        confirm = getattr(self.dialogue, 'confirm_delivered_reply', None)
        if callable(confirm):
            confirm(turn_id, reply)

    def _discard_route_reply(self, turn_id):
        discard = getattr(self.dialogue, 'discard_deferred_reply', None)
        if callable(discard):
            discard(turn_id)

    def _participation(self, event):
        decision = transition(self.participation, event)
        self.participation = decision.after
        self.record(dict(kind='participation', state_before=decision.before.value,
                         state_after=decision.after.value, event=decision.event.value,
                         reason=decision.reason))
        return decision

    def _reset_prediction(self, *, require_first):
        self.must_accept_next = require_first
        self.expected_continuations = None
        self.expected_continuations_trusted = False
        self.last_accepted = {}

    def _invalidate_forecast(self):
        """受理済みの新ターンへ、ひとつ前の予測を持ち越さない。"""
        self.expected_continuations = None
        self.expected_continuations_trusted = False

    def _close_possible_aside(self, resolution, **extra):
        with self.lock:
            aside = self.possible_aside
            if not aside:
                return None
            self.possible_aside = None
            self.record(dict(
                kind='participation_resolution',
                resolution=resolution,
                aside_id=aside['aside_id'],
                suppressed_turn_ids=[item['turn_id'] for item in aside['items']],
                **extra,
            ))
            return aside

    def _suppress_possible_aside(self, text, turn_id, assessment, assessment_status):
        if self.possible_aside is None:
            self.possible_aside = {
                'aside_id': uuid4().hex,
                'items': [],
            }
        item = {'turn_id': turn_id, 'text': text}
        self.possible_aside['items'] = [*self.possible_aside['items'][-4:], item]
        self.record(dict(
            kind='dialogue_suppressed',
            status='possibly_not_addressed',
            marker='possibly_not_addressed',
            reason='high_confidence_topic_discontinuity',
            reply='',
            raw_text=text,
            turn_id=turn_id,
            aside_id=self.possible_aside['aside_id'],
            participation_state=self.participation.value,
            assessment_status=assessment_status,
            participation_assessment=assessment.model_dump(),
            expected_continuations=[
                pattern.model_dump() for pattern in self.expected_continuations.patterns
            ],
        ))

    def _request_forecast(
        self,
        text,
        row,
        *,
        previous_accepted,
        needs_reaction,
        epoch,
    ):
        status = 'planner_unavailable'
        forecast = fallback_forecast(
            text,
            row.get('reply') or '',
            response_status=str(row.get('status') or ''),
            needs_reaction=needs_reaction,
        )
        if self.participation_planner is not None:
            try:
                forecast, status = self.participation_planner.forecast(
                    text,
                    row.get('reply') or '',
                    response_status=str(row.get('status') or ''),
                    handoff_topic=str(row.get('handoff_topic') or ''),
                    previous_accepted=previous_accepted,
                    needs_reaction=needs_reaction,
                    cancelled=lambda: epoch != self.epoch or self.stopping.is_set(),
                )
            except Exception as exc:
                status = f'planner_error:{type(exc).__name__}'
        return forecast, status

    def _store_forecast(self, forecast, status, *, turn_id, epoch):
        with self.lock:
            if epoch != self.epoch or self.stopping.is_set():
                return
            self.expected_continuations = forecast
            self.expected_continuations_trusted = status in {
                'accepted', 'accepted_reaction_fallback',
            }
            self.record(dict(
                kind='participation_forecast',
                turn_id=turn_id,
                status=status,
                trusted=self.expected_continuations_trusted,
                patterns=[pattern.model_dump() for pattern in forecast.patterns],
            ))

    def _finish_accepted(self, text, turn_id, row, *, epoch, recovery_cue=''):
        row = dict(row or {})
        row.setdefault('turn_id', turn_id)
        row.setdefault('raw_text', text)
        if row.get('status') in {'interrupted', 'busy', 'paused', 'duplicate'}:
            self._discard_route_reply(turn_id)
            self.deliver(row, epoch=epoch)
            return
        with self.lock:
            previous_accepted = dict(self.last_accepted)
            # この予測は、いま受理した発話までの入口にだけ使う。新しい返答が
            # 再生完了するまでは、失敗・無音・Web遷移も含めてfail-openに戻す。
            self._invalidate_forecast()
        if recovery_cue:
            row = dict(row, participation_recovery='false_positive_recovered',
                       recovery_cue=recovery_cue)
            prefix = 'ごめん、オレに言うてたんやな。'
            row['reply'] = prefix + (row.get('reply') or '')
        turn = self.turns.begin(
            turn_id,
            epoch=epoch,
            raw_text=text,
            semantic_text=text,
        )
        self._record_turn_lifecycle(turn, 'accepted')
        turn = self.turns.routed(
            turn_id,
            route=str(row.get('route_owner') or 'language_dialogue'),
            status=str(row.get('status') or 'unknown'),
        )
        self._record_turn_lifecycle(turn, 'routed')
        delivery = self.deliver(row, epoch=epoch)
        if delivery is False:
            self._discard_route_reply(turn_id)
            return
        with self.lock:
            self.must_accept_next = False
            self.dialogue.last_activity = self.dialogue.clock()
        if isinstance(delivery, str):
            with self.lock:
                self._playback_context[delivery] = {
                    'text': text,
                    'turn_id': turn_id,
                    'row': dict(row),
                    'previous_accepted': previous_accepted,
                    'epoch': epoch,
                }
            return
        turn = self.turns.finish_without_reply(
            turn_id,
            resolution='route_returned_no_reply',
        )
        self._record_turn_lifecycle(turn, 'no_reply')
        self._discard_route_reply(turn_id)
        self._drain_deferred_input()

    def _host_reply(self, text, turn_id, *, status, reply):
        return {
            'turn_id': turn_id,
            'raw_text': text,
            'source': 'voice',
            'mode_before': self.dialogue.mode,
            'search': {'terms': [], 'facts': [], 'status': 'not_requested', 'error': ''},
            'interpretation': None,
            'reply': reply,
            'references': [],
            'status': status,
            'mode_after': self.dialogue.mode,
            'route_owner': 'host_code',
        }

    def _compute_accepted(self, text, turn_id, *, epoch, conversation_history=''):
        if is_playful_vocalization(text):
            row = self._host_reply(
                text, turn_id, status='casual_reaction', reply=companion_reaction(text),
            )
            row['dialogue_act'] = 'playful_vocalization'
            return row
        return self.dialogue.turn(
            text,
            turn_id=turn_id,
            source='voice',
            cancelled=lambda: epoch != self.epoch or self.stopping.is_set(),
            conversation_history=conversation_history,
            defer_reply_history=True,
        )

    def _process_accepted(self, text, turn_id, *, epoch, recovery_cue=''):
        row = self._compute_accepted(
            text,
            turn_id,
            epoch=epoch,
            conversation_history=self.turns.prompt_history(now=self.dialogue.clock()),
        )
        self._finish_accepted(text, turn_id, row, epoch=epoch, recovery_cue=recovery_cue)

    def _assess_admission(self, text, turn_id, *, epoch, state=None):
        if state is None:
            with self.lock:
                state = {
                    'must_accept_next': self.must_accept_next,
                    'trusted': self.expected_continuations_trusted,
                    'forecast': self.expected_continuations,
                    'last_accepted': dict(self.last_accepted),
                }
        forecast = state['forecast']
        if state['must_accept_next']:
            return True, 'first_input_always_accept', None, 'not_requested'
        if direct_call_body(text) is not None:
            return True, 'direct_call', None, 'not_requested'
        if has_topic_shift_cue(text):
            return True, 'topic_shift_cue', None, 'not_requested'
        if is_obvious_question(text):
            return True, 'clear_question', None, 'not_requested'
        if is_obvious_minecraft_topic(text):
            return True, 'minecraft_topic', None, 'not_requested'
        if (
            not state['trusted']
            or forecast is None
            or self.participation_planner is None
        ):
            return True, 'no_trusted_forecast', None, 'not_requested'
        try:
            assessment, status = self.participation_planner.assess(
                text,
                turn_id=turn_id,
                last_accepted=state['last_accepted'],
                forecast=forecast,
                cancelled=lambda: epoch != self.epoch or self.stopping.is_set(),
            )
        except Exception as exc:
            assessment, status = None, f'planner_error:{type(exc).__name__}'
        if assessment is None or status != 'accepted':
            return True, 'assessment_unavailable', assessment, status
        if assessment.matched_pattern_ids or assessment.relation == 'expected':
            return True, 'predicted_continuation', assessment, status
        if assessment.clear_question:
            return True, 'clear_question', assessment, status
        if assessment.minecraft_topic:
            return True, 'minecraft_topic', assessment, status
        suppress = (
            assessment.relation == 'possibly_not_addressed'
            and assessment.topic_changed
            and float(assessment.confidence) >= POSSIBLE_ASIDE_CONFIDENCE
        )
        return (not suppress), (
            'high_confidence_topic_discontinuity' if suppress else 'uncertain_fail_open'
        ), assessment, status

    def _record_admission(self, text, turn_id, accepted, reason, assessment, status):
        self.record(dict(
            kind='participation_assessment',
            turn_id=turn_id,
            raw_text=text,
            decision='accept' if accepted else 'suppress',
            reason=reason,
            assessment_status=status,
            participation_assessment=(
                assessment.model_dump() if assessment is not None else None
            ),
            expected_pattern_ids=(
                [pattern.pattern_id for pattern in self.expected_continuations.patterns]
                if self.expected_continuations is not None else []
            ),
        ))

    def _submit_work(self, work_kind, *, epoch, turn_id='', run, context=None):
        if self.dialogue_worker is None:
            return False
        work_id = f'{work_kind}:' + uuid4().hex
        accepted = self.dialogue_worker.submit(
            work_id=work_id,
            work_kind=work_kind,
            session_epoch=epoch,
            turn_id=turn_id,
            run=run,
        )
        if accepted:
            with self.lock:
                self._work_ids.add(work_id)
                self._work_context[work_id] = dict(context or {})
            self.record(dict(
                kind='dialogue_work_queued',
                work_id=work_id,
                work_kind=work_kind,
                turn_id=turn_id,
                session_epoch=epoch,
            ))
        return accepted

    def _schedule_turn(self, text, turn_id, *, epoch, recovery_cue=''):
        with self.lock:
            state = {
                'must_accept_next': self.must_accept_next,
                'trusted': self.expected_continuations_trusted,
                'forecast': self.expected_continuations,
                'last_accepted': dict(self.last_accepted),
            }
        history = self.turns.prompt_history(now=self.dialogue.clock())

        def run():
            accepted, reason, assessment, status = self._assess_admission(
                text,
                turn_id,
                epoch=epoch,
                state=state,
            )
            row = None
            if accepted and epoch == self.epoch and not self.stopping.is_set():
                row = self._compute_accepted(
                    text,
                    turn_id,
                    epoch=epoch,
                    conversation_history=history,
                )
            return {
                'text': text,
                'accepted': accepted,
                'reason': reason,
                'assessment': assessment,
                'assessment_status': status,
                'row': row,
                'recovery_cue': recovery_cue,
            }

        if not self._submit_work(
            'turn',
            epoch=epoch,
            turn_id=turn_id,
            run=run,
            context={'text': text, 'recovery_cue': recovery_cue},
        ):
            self.record(dict(
                kind='input_dropped',
                reason='dialogue_worker_queue_unavailable',
                turn_id=turn_id,
                raw_text=text,
            ))

    def _schedule_forecast(self, context):
        epoch = context['epoch']

        def run():
            forecast, status = self._request_forecast(
                context['text'],
                context['row'],
                previous_accepted=context['previous_accepted'],
                needs_reaction=False,
                epoch=epoch,
            )
            return {
                'forecast': forecast,
                'status': status,
                'turn_id': context['turn_id'],
            }

        if self.dialogue_worker is None:
            result = run()
            self._store_forecast(
                result['forecast'], result['status'], turn_id=result['turn_id'], epoch=epoch,
            )
            return False
        if not self._submit_work(
            'forecast', epoch=epoch, turn_id=context['turn_id'], run=run,
        ):
            self.record(dict(
                kind='participation_forecast',
                turn_id=context['turn_id'],
                status='worker_queue_unavailable',
                trusted=False,
                patterns=[],
            ))
            return False
        return True

    def _schedule_control(self, work_kind, *, epoch, run):
        if self.dialogue_worker is None:
            self.deliver(run(), epoch=epoch)
            return False
        if not self._submit_work(work_kind, epoch=epoch, run=run):
            self.record(dict(
                kind='control',
                control=work_kind,
                status='worker_queue_unavailable',
            ))
            return False
        return True

    def _defer_input(self, event, *, reason):
        with self.lock:
            previous = self._deferred_input
            self._deferred_input = dict(event)
        if previous is not None:
            self.record(dict(
                kind='input_dropped',
                reason='deferred_input_replaced',
                raw_text=previous.get('text', ''),
            ))
        self.record(dict(
            kind='input_deferred',
            reason=reason,
            raw_text=event.get('text', ''),
        ))

    def _drain_deferred_input(self):
        with self.lock:
            if self._work_ids or self._pending_playback_ids or self._deferred_input is None:
                return
            event = self._deferred_input
            self._deferred_input = None
            if event.get('session_epoch', self.epoch) != self.epoch:
                return
            self.inbox.put(event)

    def receive(self, event):
        if event['kind'] == 'dialogue_work_result':
            self.record({
                'kind': 'dialogue_work_completed',
                'work_id': event.get('work_id', ''),
                'work_kind': event.get('work_kind', ''),
                'turn_id': event.get('turn_id', ''),
                'error': event.get('error', ''),
                'session_epoch': event.get('session_epoch'),
            })
        else:
            self.record(event)  # LLMが処理中でもSTT受信と棄却理由は直ちに見える。
        if event['kind'] == 'capture_ready':
            self.ready = True
        elif event['kind'] in {'capture_stopped', 'capture_error'} or (
            event['kind'] == 'diagnostic' and event.get('reason') in {'microphone_stopped', 'aec_failed'}
        ):
            self.command('/quit')  # 録音停止後に案内完了からWebを開かない。
        elif event['kind'] in {
            'recognized', 'playback_started', 'playback_result', 'dialogue_work_result',
        }:
            with self.lock:
                if not self.stopping.is_set():
                    queued = dict(event, talk=self.talk)
                    queued.setdefault('session_epoch', self.epoch)
                    self.inbox.put(queued)

    def deliver(self, row, *, epoch):
        with self.lock:
            if epoch != self.epoch or self.stopping.is_set() or not self.talk or self.dialogue.paused:
                self.record(dict(row, kind='dialogue_suppressed', delivery='cancelled_or_listen'))
                return False
            delivered = dict(row, kind='dialogue')
            if row.get('reply'):
                speech = dict(row.get('speech', {}))
                utterance_id = speech.get('utterance_id') or 'voice-test:' + uuid4().hex
                speech['utterance_id'] = utterance_id
                delivered['speech'] = speech
                turn_id = str(row.get('turn_id') or '')
                if turn_id:
                    turn = self.turns.select_reply(
                        turn_id,
                        reply=row['reply'],
                        utterance_id=utterance_id,
                    )
                    self._record_turn_lifecycle(turn, 'selected')
                    turn = self.turns.queued(utterance_id)
                    self._record_turn_lifecycle(turn, 'queued')
                self.record(delivered)
                self._pending_playback_ids.add(utterance_id)
                self.playback.submit(row['reply'], utterance_id=utterance_id,
                                     departure=bool(speech.get('completion_required')),
                                     refresh_token=row.get('refresh_token'))
                return utterance_id
            self.record(delivered)
            return True

    def handle(self, event):
        kind = event['kind']
        if kind == 'dialogue_work_result':
            with self.lock:
                self._work_ids.discard(event.get('work_id'))
                work_context = self._work_context.pop(event.get('work_id'), {})
        else:
            work_context = {}
        with self.lock:
            epoch = self.epoch
            if self.stopping.is_set() or event.get('session_epoch', epoch) != epoch:
                return
        if kind == 'recognized':
            if event.get('talk') and self.talk:
                if not isinstance(event.get('text'), str) or not 0 < len(event['text']) <= 1000:
                    self.record(dict(kind='input_dropped', reason='dialogue_text_contract'))
                    return
                with self.lock:
                    if self._work_ids or self._pending_playback_ids:
                        reason = (
                            'dialogue_work_in_progress'
                            if self._work_ids else 'playback_in_progress'
                        )
                        self._defer_input(event, reason=reason)
                        return
                text = event['text']
                turn_id = uuid4().hex
                with self.lock:
                    possible_aside = self.possible_aside
                if possible_aside and resolves_side_conversation(text):
                    closed = self._close_possible_aside(
                        'side_conversation_resolved', resolution_cue=text,
                    )
                    if closed is None:
                        return
                    self._participation(ParticipationEvent.SIDE_CONVERSATION_RESOLVED)
                    self._finish_accepted(
                        text,
                        turn_id,
                        self._host_reply(
                            text, turn_id, status='side_conversation_resolved', reply='ええんやで。',
                        ),
                        epoch=epoch,
                    )
                    return
                if possible_aside and corrects_false_suppression(text):
                    recovered = possible_aside['items'][-1]
                    aside = self._close_possible_aside(
                        'false_positive_recovered',
                        resolution_cue=text,
                        recovered_turn_id=recovered['turn_id'],
                        unresolved_turn_ids=[
                            item['turn_id'] for item in possible_aside['items'][:-1]
                        ],
                    )
                    if aside is None:
                        return
                    self._participation(ParticipationEvent.FALSE_SUPPRESSION_CORRECTED)
                    if self.dialogue_worker is None:
                        self._process_accepted(
                            recovered['text'], recovered['turn_id'], epoch=epoch,
                            recovery_cue=text,
                        )
                    else:
                        self._schedule_turn(
                            recovered['text'], recovered['turn_id'], epoch=epoch,
                            recovery_cue=text,
                        )
                    return
                if (self.participation is ParticipationState.ACTIVE
                        and not self.must_accept_next
                        and self.dialogue.clock() - self.dialogue.last_activity
                        >= self.dialogue.ttl_seconds):
                    self._participation(ParticipationEvent.IDLE_TIMEOUT)
                if self.participation is ParticipationState.QUIET:
                    addressed = direct_call_body(text)
                    if addressed is None:
                        self.record(dict(kind='dialogue_suppressed', status='quiet_not_called',
                                         reply='', raw_text=text,
                                         participation_state=self.participation.value))
                        return
                    self._participation(ParticipationEvent.DIRECT_CALL)
                    if not addressed:
                        self._finish_accepted(
                            text,
                            turn_id,
                            self._host_reply(
                                text, turn_id, status='casual', reply='おるで。どないしたん？',
                            ),
                            epoch=epoch,
                        )
                        return
                    text = addressed
                if self.dialogue_worker is not None:
                    self._schedule_turn(text, turn_id, epoch=epoch)
                    return
                accepted, reason, assessment, assessment_status = self._assess_admission(
                    text, turn_id, epoch=epoch,
                )
                with self.lock:
                    if epoch != self.epoch or self.stopping.is_set():
                        return
                    self._record_admission(
                        text, turn_id, accepted, reason, assessment, assessment_status,
                    )
                    if not accepted:
                        self._suppress_possible_aside(
                            text, turn_id, assessment, assessment_status,
                        )
                        return
                    if self.possible_aside:
                        self._close_possible_aside(
                            'unresolved_before_addressed_turn', accepted_turn_id=turn_id,
                        )
                self._process_accepted(text, turn_id, epoch=epoch)
        elif kind == 'focus':
            self.deliver(self.dialogue.observe_minecraft_focus(event['active'], event_id=uuid4().hex), epoch=epoch)
        elif kind == 'playback_started':
            if not self.playback.valid(event):
                return
            turn = self.turns.started(str(event.get('utterance_id') or ''))
            self._record_turn_lifecycle(turn, 'started')
        elif kind == 'playback_result':
            if not self.playback.valid(event):
                return
            utterance_id = str(event.get('utterance_id') or '')
            with self.lock:
                self._pending_playback_ids.discard(utterance_id)
                context = self._playback_context.pop(utterance_id, None)
            turn = self.turns.resolve(
                utterance_id,
                event['status'],
                resolution=str(event.get('reason') or ''),
            )
            self._record_turn_lifecycle(turn, event['status'])
            if context and event['status'] == 'completed':
                self._confirm_route_reply(
                    context['turn_id'],
                    str(context['row'].get('reply') or ''),
                )
                with self.lock:
                    self.last_accepted = {
                        'user_text': context['text'][:500],
                        'dogido_reply': str(context['row'].get('reply') or '')[:420],
                        'status': str(context['row'].get('status') or ''),
                        'handoff_topic': str(context['row'].get('handoff_topic') or ''),
                    }
            elif context:
                self._discard_route_reply(context['turn_id'])
            if event.get('departure'):
                event_id = uuid4().hex
                self._schedule_control(
                    'web_departure',
                    epoch=epoch,
                    run=lambda: self.dialogue.on_speech_playback_result(
                        utterance_id,
                        status=event['status'],
                        event_id=event_id,
                        cancelled=lambda: epoch != self.epoch or self.stopping.is_set(),
                    ),
                )
            elif event.get('refresh_token') and event['status'] == 'completed':
                refresh_token = event['refresh_token']
                self._schedule_control(
                    'return_refresh',
                    epoch=epoch,
                    run=lambda: self.dialogue.refresh_after_return(
                        refresh_token,
                        host_cancelled=lambda: epoch != self.epoch or self.stopping.is_set(),
                    ),
                )
            elif context and event['status'] == 'completed':
                self._schedule_forecast(context)
            self._drain_deferred_input()
        elif kind == 'dialogue_work_result':
            if event.get('error'):
                self.record(dict(
                    kind='dialogue_work_failed',
                    work_kind=event.get('work_kind'),
                    turn_id=event.get('turn_id'),
                    error=event['error'],
                ))
                if event.get('work_kind') == 'turn' and work_context.get('text'):
                    row = self._host_reply(
                        work_context['text'],
                        event['turn_id'],
                        status='generation_unavailable',
                        reply='ごめん、今ちょっと言葉が出てこんかった。',
                    )
                    self._finish_accepted(
                        work_context['text'],
                        event['turn_id'],
                        row,
                        epoch=epoch,
                        recovery_cue=work_context.get('recovery_cue', ''),
                    )
                self._drain_deferred_input()
                return
            result = event.get('result') or {}
            if event.get('work_kind') == 'turn':
                text = result['text']
                accepted = bool(result['accepted'])
                assessment = result.get('assessment')
                assessment_status = result.get('assessment_status', 'invalid_payload')
                self._record_admission(
                    text,
                    event['turn_id'],
                    accepted,
                    result['reason'],
                    assessment,
                    assessment_status,
                )
                if not accepted:
                    self._suppress_possible_aside(
                        text, event['turn_id'], assessment, assessment_status,
                    )
                    self._drain_deferred_input()
                    return
                if self.possible_aside:
                    self._close_possible_aside(
                        'unresolved_before_addressed_turn', accepted_turn_id=event['turn_id'],
                    )
                self._finish_accepted(
                    text,
                    event['turn_id'],
                    result['row'],
                    epoch=epoch,
                    recovery_cue=result.get('recovery_cue', ''),
                )
            elif event.get('work_kind') == 'forecast':
                self._store_forecast(
                    result['forecast'],
                    result['status'],
                    turn_id=result['turn_id'],
                    epoch=epoch,
                )
            elif event.get('work_kind') in {'web_departure', 'return_refresh'}:
                self.deliver(result, epoch=epoch)
            self._drain_deferred_input()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--capture-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.capture_worker:
        capture_worker()
        return 0
    from dogido_server.config import Settings
    settings = Settings().model_copy(update={'voice_echo_cancellation': 'webrtc'})
    try:
        preflight(settings)
    except (Exception, SystemExit) as exc:
        print(f'起動準備で停止: {exc}', file=sys.stderr)
        return 2
    if args.check:
        return 0
    if args.output is None:
        parser.error('--output 新規ディレクトリ が必要です')
    if not sys.stdin.isatty():
        parser.error('ユーザー操作のTerminalから起動してください。録音は自動実行しません。')
    print('実マイクとMacの再生音全体をAECに使います。外部STT送信なし。STT一時WAV以外の録音保存なし。', flush=True)
    print('認識文・応答・診断はローカルログへ保存。同意した質問だけGoogleへ送ります。', flush=True)
    answer = input('開始するには Enter、開始せず戻るには q: ')
    if answer.strip():
        return 0
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from dogido_server.llm.client import DogidoLLM
    from dogido_server.audio import VoicevoxSpeechBackend, SaySpeechBackend
    from .controller import LanguageDialogue
    from .chrome_web import ChromeWebClient
    from .google_overview import GoogleOverviewResearch
    llm = DogidoLLM(settings.llm_route_settings('chat'))
    print('既存の会話モデルを読み込み中。まだマイクは開始していません。', flush=True)
    if not llm.preload():
        print('モデル利用不可: ' + str(llm.disabled_reason()), file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = dict(kind='user_operated_voice_test', model=settings.llm_route_settings('chat').mlx_model_id,
                    echo='webrtc', microphone_during_tts=True, tts_backend=settings.tts_backend,
                    focus_events='simulated_not_os_observed', playback_events='process_exit_observed',
                    participation='first_input_open_then_five_pattern_fail_open',
                    suppressed_input_log='possibly_not_addressed_not_dialogue_history',
                    conversation_turns='completed_playback_only_five_exchanges_300s',
                    dialogue_execution='bounded_serial_background_worker_epoch_checked',
                    audio_recording_saved=False, human_review='pending')
    (args.output / 'run.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    temporary = tempfile.TemporaryDirectory(prefix='dogido-voice-test-')
    log_lock = threading.Lock()
    client = ChromeWebClient(child_view=True, overview_search=True)
    microphone = playback = session = dialogue_worker = None
    with (args.output / 'events.jsonl').open('w', encoding='utf-8') as logs:
        def record(event):
            event = dict(event, at=datetime.now(timezone.utc).isoformat())
            with log_lock:
                logs.write(json.dumps(event, ensure_ascii=False) + '\n')
                logs.flush()
                kind = event['kind']
                if kind == 'recognized':
                    print('聞き取り: ' + event['text'], flush=True)
                elif kind == 'dialogue':
                    print('ドギド: ' + (event.get('reply') or '[' + str(event.get('status', '')) + ']'), flush=True)
                elif kind == 'capture_ready':
                    print('マイク入力中。声で話しかけてください。' + CONTROLS, flush=True)
                else:
                    print(kind + ': ' + json.dumps(event, ensure_ascii=False), flush=True)

        def receive(event):
            session.receive(event)

        try:
            backend = (VoicevoxSpeechBackend(settings.model_copy(update={'voicevox_temp_dir': Path(temporary.name)}))
                       if settings.tts_backend == 'voicevox' else SaySpeechBackend(settings.say_voice))
            inbox = Inbox(settings.voice_stt_max_pending_segments, settings.voice_stt_max_segment_age_sec, record)
            playback = Playback(backend, receive, max_pending=settings.audio_max_pending_batches,
                                speed_scale=settings.voicevox_speed_scale_peace)
            dialogue = LanguageDialogue(llm, web=GoogleOverviewResearch(client),
                                        on_event=lambda event: record(dict(kind='web', **event)))
            dialogue_worker = DialogueWorker(receive)
            session = VoiceSession(
                dialogue,
                playback,
                inbox,
                record,
                participation_planner=ParticipationPlanner(llm),
                dialogue_worker=dialogue_worker,
            )
            print('マイク初期化中。OSの権限確認後、「マイク入力中」の表示を待ってください。', flush=True)
            microphone = Microphone(receive)

            def keyboard():
                for line in sys.stdin:
                    session.command(line.strip())
                    if session.stopping.is_set():
                        microphone.close()
                        return
                session.command('/quit')
                microphone.close()

            threading.Thread(target=keyboard, name='voice-test-controls', daemon=True).start()
            def terminate(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, terminate)
            while not session.stopping.is_set():
                session.handle(inbox.get())
        except KeyboardInterrupt:
            print('\n終了処理中…', flush=True)
        finally:
            if session:
                session.command('/quit')
            if microphone:
                microphone.close()
            stopped = playback.close() if playback else True
            worker_stopped = session.close() if session else True
            client.close()
            if stopped and worker_stopped:
                temporary.cleanup()
            else:
                record(dict(
                    kind='cleanup_warning',
                    reason=(
                        'background_work_still_running'
                        if not worker_stopped else 'tts_preparation_still_running'
                    ),
                ))
    print(f'音声試験を終了しました。ログ: {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
