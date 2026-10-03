"""No SDK initialization/model/server: pipe-only adapter boundary checks."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
import haiku_tokens
class TokenBoundary(unittest.TestCase):
    def test_import_does_not_initialize_dictionary_or_platform_sdk(self):
        # 辞書・端末SDKは要求を受けた時だけ初期化する。新しいプロセスでimportの副作用を検査する。
        run = subprocess.run([sys.executable, "-c",
            "import sys; import haiku_tokens; "
            "assert not {'fugashi', 'unidic_lite', 'apple_fm_sdk', 'foundry_local'} & sys.modules.keys()"],
            cwd=Path(haiku_tokens.__file__).parent, capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
    def test_policy_operation_rejected_and_owned_process_exits(self):
        run = subprocess.run([sys.executable,str(Path(haiku_tokens.__file__))],input='{"op":"haiku_context"}\n',capture_output=True,text=True,timeout=5)
        self.assertEqual(run.returncode,1)
        self.assertIn('error',json.loads(run.stdout))
    def test_incomplete_frame_rejected_and_eof_is_clean(self):
        for payload,code in [('',0),('{"op":"tts_tokens"}',1),('x'*(haiku_tokens.FRAME_LIMIT+1)+'\n',1)]:
            run=subprocess.run([sys.executable,str(Path(haiku_tokens.__file__))],input=payload,capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,code)
if __name__=='__main__':unittest.main()
