import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import server
from artifact_ui import decorate


class Artifacts(unittest.TestCase):
    def test_bad_metadata_is_never_injected(self):
        result=decorate({'html':'<html><body>Details</body></html>', 'aid':'<script>', 'linked_ids':['ABCDEF','abcdef','abcdef','\" onclick=alert(1)']})
        self.assertEqual(result.count('data-aid="abcdef"'),1)
        self.assertNotIn('onclick=alert',result)
        self.assertNotIn('data-aid="ABCDEF"',result)
        self.assertIn('data-aid="d2497e"',result)

    def test_http_store_preserves_and_displays_metadata(self):
        old_dir,old_token=server.DIR,server.TOKEN
        with tempfile.TemporaryDirectory() as tmp:
            server.DIR,server.TOKEN=tmp,'t'*32
            http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:%d'%http.server_address[1];page='a'*32
            try:
                data={'html':'<html><body><main>Private content</main></body></html>','expires':time.time()+600,'aid':'b8c3ff','linked_ids':['123abc','<bad>']}
                req=urllib.request.Request(url+'/api/pages/'+page,method='PUT',data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+server.TOKEN})
                with urllib.request.urlopen(req) as response:self.assertEqual(response.status,200)
                stored=json.loads((Path(tmp)/(page+'.json')).read_text());self.assertEqual(stored['linked_ids'],['123abc'])
                with urllib.request.urlopen(url+'/p/'+page) as response:
                    body=response.read().decode(); self.assertIn('data-aid="b8c3ff"',body);self.assertIn('data-aid="123abc"',body)
                    self.assertIn("default-src 'none'",response.headers['Content-Security-Policy'])
                    self.assertIn('noindex',response.headers['X-Robots-Tag'])
            finally:
                http.shutdown();http.server_close();thread.join();server.DIR,server.TOKEN=old_dir,old_token


if __name__=='__main__':unittest.main()
