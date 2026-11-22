"""Regression tests extracted from the actual plugin, without Cinema 4D imports."""
import ast
import json
import os
import queue
import threading
import time
import types
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'c4d_plugin/mcp_server_plugin.pyp'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PLUGIN = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'C4DSocketServer')
NAMES = {'execute_on_main_thread', 'handle_client', 'handle_execute_python',
         'handle_get_execution_status', 'handle_ping', 'handle_viewport_screenshot'}
BODY = [n for n in PLUGIN.body if
        (isinstance(n, ast.FunctionDef) and n.name in NAMES) or
        (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and
         t.id in ('_SUPPORTED_COMMANDS', '_SAFE_COMMANDS') for t in n.targets))]
MODULE = ast.fix_missing_locations(ast.Module(body=[
    ast.ClassDef(name='Harness', bases=[], keywords=[], body=BODY, decorator_list=[])], type_ignores=[]))
ENV = dict(json=json, os=os, threading=threading, time=time, PLUGIN_ID=1,
           MCP_MAX_COMMAND_BYTES=5*1024*1024, MCP_MAX_RESPONSE_BYTES=8*1024*1024,
           MCP_MAX_OUTPUT_CHARS=65536, MCP_BUILD_ID='test', MCP_LOADED_SOURCE_SHA256='test',
           c4d=types.SimpleNamespace(SpecialEventAdd=lambda _: None, GetC4DVersion=lambda: 2026031,
                                    documents=types.SimpleNamespace(GetActiveDocument=lambda: None)))
exec(compile(MODULE, str(SOURCE), 'exec'), ENV)
Harness = ENV['Harness']


def server():
    s = Harness()
    s.logs = []
    s.log = s.logs.append
    s.msg_queue = queue.Queue()
    s._execution_lock = threading.Lock()
    s._executions = {}
    s.running = True
    s.auth_token = None
    s.safe_mode = False
    s._append_action_log = lambda _: None
    s._sanitize_command_params = lambda _: {}
    return s


class Socket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.sent = bytearray()
    def recv(self, _):
        return next(self.chunks, b'')
    def sendall(self, data):
        self.sent.extend(data)
    def close(self):
        pass
    def responses(self):
        return [json.loads(x) for x in self.sent.splitlines()]


class TransportTests(unittest.TestCase):
    def test_every_utf8_split(self):
        payload = (json.dumps({'command':'ping', 'echo':'café 蜘蛛', 'request_id':'r1'},
                              ensure_ascii=False)+'\n').encode()
        for split in range(1, len(payload)):
            s = server()
            sock = Socket([payload[:split], payload[split:]])
            s.handle_client(sock)
            result = sock.responses()
            self.assertEqual(len(result), 1, split)
            self.assertEqual(result[0]['echo'], 'café 蜘蛛')
            self.assertEqual(result[0]['request_id'], 'r1')

    def test_oversize_without_newline(self):
        old = ENV['MCP_MAX_COMMAND_BYTES']
        try:
            ENV['MCP_MAX_COMMAND_BYTES'] = 32
            sock = Socket([b'x'*20, b'x'*20])
            server().handle_client(sock)
            self.assertIn('payload too large', sock.responses()[0]['error'])
        finally:
            ENV['MCP_MAX_COMMAND_BYTES'] = old

    def test_invalid_utf8_then_valid_frame(self):
        sock = Socket([b'\xff\n{"command":"ping"}\n'])
        server().handle_client(sock)
        self.assertFalse(sock.responses()[0]['ok'])
        self.assertTrue(sock.responses()[1]['ok'])

    def test_auth_and_script_never_logged(self):
        s = server()
        s.auth_token = 'correct-secret'
        sock = Socket([(json.dumps({'command':'execute_python', 'auth_token':'wrong-secret',
                                    'script': 'private-script'})+'\n').encode()])
        s.handle_client(sock)
        self.assertFalse(sock.responses()[0]['ok'])
        for secret in ('correct-secret', 'wrong-secret', 'private-script'):
            self.assertNotIn(secret, '\n'.join(s.logs))

    def test_bounded_response(self):
        old = ENV['MCP_MAX_RESPONSE_BYTES']
        try:
            ENV['MCP_MAX_RESPONSE_BYTES'] = 512
            s = server()
            s.handle_ping = lambda _: {'huge': 'x'*2048}
            sock = Socket([b'{"command":"ping"}\n'])
            s.handle_client(sock)
            self.assertTrue(sock.responses()[0]['response_truncated'])
            self.assertLess(len(sock.sent), 512)
        finally:
            ENV['MCP_MAX_RESPONSE_BYTES'] = old


class ExecutionTests(unittest.TestCase):
    def test_timeout_cancels_before_start(self):
        s = server()
        effects = []
        result = s.execute_on_main_thread(lambda: effects.append(1), _timeout=.001)
        self.assertEqual(result['execution_state'], 'cancelled_before_start')
        s.msg_queue.get()[1]()
        self.assertEqual(effects, [])
        self.assertFalse(result['may_have_executed'])

    def test_running_timeout_reports_eventual_result_once(self):
        s = server()
        started, release = threading.Event(), threading.Event()
        effects, result = [], []
        def operation():
            started.set()
            release.wait(2)
            effects.append(1)
            return {'value': 42}
        caller = threading.Thread(target=lambda: result.append(
            s.execute_on_main_thread(operation, _timeout=.03)))
        caller.start()
        execute = s.msg_queue.get(timeout=1)[1]
        worker = threading.Thread(target=execute)
        worker.start()
        self.assertTrue(started.wait(1))
        caller.join(1)
        self.assertEqual(result[0]['execution_state'], 'running')
        release.set()
        worker.join(1)
        status = s.handle_get_execution_status({'execution_id':result[0]['execution_id']})
        self.assertEqual(status['execution_state'], 'completed')
        self.assertEqual(status['result'], {'value':42})
        self.assertEqual(effects, [1])

    def test_stdout_and_variables_bounded(self):
        s = server()
        s.execute_on_main_thread = lambda fn, **kwargs: fn()
        result = s.handle_execute_python({'script':"big = list(range(100000)); print('x'*80000)"})
        self.assertEqual(len(result['output']), 65536)
        self.assertTrue(result['output_truncated'])
        self.assertEqual(result['variables'], {})
        result = s.handle_execute_python({'script':"big = list(range(100000))",
                                          'include_variables': True})
        self.assertEqual(result['variables']['big'], '<list: 100000 items>')

    def test_error_restores_stdout(self):
        import sys
        s = server()
        s.execute_on_main_thread = lambda fn, **kwargs: fn()
        before = sys.stdout
        result = s.handle_execute_python({'script':"print('before'); raise ValueError('test')"})
        self.assertIn('error', result)
        self.assertIn('before', result['output'])
        self.assertIs(sys.stdout, before)


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        class RD(dict):
            def __bool__(self): return True
            def GetClone(self): return RD(self)
            def GetData(self): return dict(self)
            def Remove(self): pass
        class Doc:
            active = RD()
            frame = 3
            def GetActiveRenderData(self): return self.active
            def SetActiveRenderData(self, value): self.active = value
            def InsertRenderData(self, value): pass
            def GetTime(self): return self.frame
            def SetTime(self, value): self.frame = value
            def GetFps(self): return 30
            def ExecutePasses(self, *args): pass
            def GetActiveBaseDraw(self): return None
        self.doc = Doc()
        self.original_rd = self.doc.active
        self.old_c4d = ENV['c4d']
        fake = types.SimpleNamespace(
            documents=types.SimpleNamespace(GetActiveDocument=lambda: self.doc,
                                             RenderDocument=lambda *a: 0),
            bitmaps=types.SimpleNamespace(MultipassBitmap=lambda *a: types.SimpleNamespace(
                AddChannel=lambda *a: None, Save=lambda *a: 9)),
            BaseTime=lambda frame, fps: frame, EventAdd=lambda: None,
            COLORMODE_RGB=1, RDATA_RENDERENGINE=1, RDATA_XRES=2, RDATA_YRES=3,
            RDATA_FRAMESEQUENCE=4, RDATA_FRAMESEQUENCE_CURRENTFRAME=5,
            RDATA_RENDERENGINE_PREVIEWHARDWARE=300796274,
            BUILDFLAGS_INTERNALRENDERER=1, RENDERFLAGS_EXTERNAL=1,
            RENDERFLAGS_NODOCUMENTCLONE=2, RENDERRESULT_OK=0,
            FILTER_PNG=1, IMAGERESULT_OK=0)
        ENV['c4d'] = fake
        self.s = server()
        self.s.execute_on_main_thread = lambda fn, **kwargs: fn()
        self.s._renderer_name = lambda _: 'hardware'

    def tearDown(self):
        ENV['c4d'] = self.old_c4d

    def test_renderer_failure_restores_timeline_and_renderdata(self):
        def fail(*a): raise RuntimeError('forced render failure')
        ENV['c4d'].documents.RenderDocument = fail
        result = self.s.handle_viewport_screenshot({'frame': 42, 'inline': True})
        self.assertIn('forced render failure', result['error'])
        self.assertEqual(self.doc.frame, 3)
        self.assertIs(self.doc.active, self.original_rd)

    def test_failed_save_does_not_dump_inline(self):
        import tempfile
        result = self.s.handle_viewport_screenshot({'frame': 42,
            'save_path': os.path.join(tempfile.gettempdir(), 'mcp-fake-screenshot.png')})
        self.assertIn('no inline fallback', result['error'])
        self.assertNotIn('image_data', result)
        self.assertEqual(self.doc.frame, 3)
        self.assertIs(self.doc.active, self.original_rd)

    def test_dimension_and_safe_mode_guards(self):
        self.assertIn('error', self.s.handle_viewport_screenshot({'width': 100000}))
        self.s.safe_mode = True
        self.assertIn('safe-mode', self.s.handle_viewport_screenshot({})['error'])


if __name__ == '__main__':
    unittest.main()
