"""ゲーム・GPU・モデルなしで、受信照合と枠の縮小、共有排他を検査する。"""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from PIL import Image
import probe


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game = Path(self.tmp.name)
        self.cid = 'view_12345678-1234-1234-1234-123456789abc'
        self.directory = self.game / 'dogido-vision/captures' / self.cid
        self.directory.mkdir(parents=True)
        Image.new('RGB', (800, 400), (30, 40, 50)).save(self.directory / 'frame.png')
        self.data = {'schema_version': 1, 'capture_id': self.cid, 'adapter_build': probe.BUILD,
                     'captured_at': probe.now().isoformat(), 'viewpoint': 'dogido_client_render_eye',
                     'camera_restored': True, 'boot_id': 'boot', 'context': {'body_id': 'one'},
                     'annotation_status': 'available', 'width': 800, 'height': 400,
                     'player_box': {'left': 100, 'top': 100, 'right': 200, 'bottom': 300},
                     'image_sha256': probe.digest(self.directory / 'frame.png')}
        self.retention = {'schema_version': 1, 'expires_at': (probe.now() + timedelta(hours=24)).isoformat(),
                          'images': [{'filename': 'frame.png', 'sha256': self.data['image_sha256']}]}
        image = Image.new('RGB', (192, 96), (30, 40, 50))
        image.putpixel((24, 24), (40, 255, 80))
        image.save(self.directory / 'player-192.png')
        self.data.update(annotated_image='player-192.png', annotated_width=192, annotated_height=96,
                         annotated_sha256=probe.digest(self.directory / 'player-192.png'))
        self.retention['images'].append({'filename': 'player-192.png', 'sha256': self.data['annotated_sha256']})
        probe.save(self.directory / 'frame.json', self.data)
        probe.save(self.directory / 'retention.json', self.retention)

    def test_native_thumbnail_reused_without_changing_pixels(self):
        result = probe.annotate(self.game, self.cid, {'boot_id': 'boot', 'context': {'body_id': 'one'}})
        self.assertEqual(result['annotated']['sha256'], self.data['annotated_sha256'])
        self.assertEqual(result['annotated']['size'], [192, 96])
        self.assertEqual(result['annotated']['player_box'], [24, 24, 48, 72])
        self.assertEqual(probe.digest(self.directory / 'frame.png'), self.data['image_sha256'])
        with Image.open(self.directory / 'player-192.png') as image:
            self.assertEqual(image.getpixel((24, 24)), (40, 255, 80))
        with probe.retention_lock(self.directory):
            self.assertEqual(len(probe.retained(self.directory)['images']), 2)

    def test_expired_or_modified_image_never_annotated(self):
        self.retention['expires_at'] = (probe.now() - timedelta(seconds=1)).isoformat()
        probe.save(self.directory / 'retention.json', self.retention)
        with self.assertRaisesRegex(ValueError, 'expired_image'):
            probe.annotate(self.game, self.cid)
        self.assertEqual(probe.digest(self.directory / 'player-192.png'), self.data['annotated_sha256'])
        self.retention['expires_at'] = (probe.now() + timedelta(hours=24)).isoformat()
        probe.save(self.directory / 'retention.json', self.retention)
        (self.directory / 'frame.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image_hash_mismatch'):
            probe.annotate(self.game, self.cid)

    def test_stale_context_and_unrestored_camera_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'capture_context_mismatch'):
            probe.annotate(self.game, self.cid, {'boot_id': 'other', 'context': {'body_id': 'one'}})
        self.data['camera_restored'] = False
        probe.save(self.directory / 'frame.json', self.data)
        with self.assertRaisesRegex(ValueError, 'camera_not_verified'):
            probe.annotate(self.game, self.cid)

    def test_invalid_and_unavailable_box_are_rejected(self):
        for value in [float('nan'), -1, 801]:
            self.data['player_box']['left'] = value
            with self.assertRaisesRegex(ValueError, 'invalid_player_box'):
                probe.validate(self.data, self.cid)
        self.data['annotation_status'] = 'owner_occluded_or_unavailable'
        with self.assertRaisesRegex(ValueError, 'annotation_unavailable'):
            probe.validate(self.data, self.cid)

    def test_same_lock_used_by_java_prevents_concurrent_annotation(self):
        (self.directory / 'retention.lock').mkdir()
        with self.assertRaises(FileExistsError):
            probe.annotate(self.game, self.cid)
        self.assertTrue((self.directory / 'retention.lock').is_dir())
        self.assertEqual(probe.digest(self.directory / 'player-192.png'), self.data['annotated_sha256'])

    def test_failed_touch_preserves_native_image(self):
        with patch.object(probe, 'used', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                probe.annotate(self.game, self.cid)
        self.assertEqual(probe.digest(self.directory / 'player-192.png'), self.data['annotated_sha256'])
        self.assertTrue((self.directory / 'frame.png').exists())
        self.assertFalse((self.directory / 'retention.lock').exists())

    def test_old_ready_and_wrong_build_do_not_claim_readiness(self):
        start = probe.now()
        status = {'schema_version': 1, 'adapter_build': probe.BUILD, 'status': 'ready',
                  'updated_at': (start - timedelta(seconds=1)).isoformat(), 'boot_id': 'boot', 'context': {'body_id': 'one'}}
        probe.save(self.game / 'dogido-vision/ready.json', status)
        with self.assertRaisesRegex(ValueError, 'stale_ready_record'):
            probe.ready(self.game, start)
        status['updated_at'] = probe.now().isoformat()
        probe.save(self.game / 'dogido-vision/ready.json', status)
        self.assertEqual(probe.ready(self.game, start)['boot_id'], 'boot')
        status['adapter_build'] = 'old'
        probe.save(self.game / 'dogido-vision/ready.json', status)
        with self.assertRaisesRegex(ValueError, 'waiting_for_body_prepare'):
            probe.ready(self.game, start)

    def test_missing_or_corrupt_native_thumbnail_is_not_regenerated(self):
        self.data.pop('annotated_image')
        probe.save(self.directory / 'frame.json', self.data)
        with self.assertRaisesRegex(ValueError, 'native_thumbnail_missing'):
            probe.annotate(self.game, self.cid)
        self.data['annotated_image'] = 'player-192.png'
        probe.save(self.directory / 'frame.json', self.data)
        (self.directory / 'player-192.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image_hash_mismatch'):
            probe.annotate(self.game, self.cid)

    def test_path_traversal_never_accepted(self):
        with self.assertRaisesRegex(ValueError, 'invalid_capture_id'):
            probe.capture_dir(self.game, '../original')


if __name__ == '__main__':
    unittest.main()
