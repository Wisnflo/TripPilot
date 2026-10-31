import json
import math
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch,MagicMock
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.planning.context import public_context
from trippilot.tools.agent_tools import ToolBox
from trippilot.web.server import application


class PublicDataRegressionTest(unittest.TestCase):
    def test_chinese_hotel_property_uses_public_bilingual_ids(self):
        result=ToolBox().call('accommodations_select',{'city':'成都','key':'featurehoteltype','op':'contains','value':'健身'})
        rows=result['data']['rows']
        self.assertTrue(rows);self.assertTrue(any(r['featurehoteltype']=='Fitness Room' for r in rows))

    def test_no_air_service_is_not_a_nan_fare(self):
        task={'uid':'no-air','start_city':'苏州','target_city':'杭州','days':2,'people_number':1,'nature_language':'历史文化遗址考察'}
        packet=public_context(task,ToolBox(),time.monotonic()+30)
        rows=[*packet['outbound'].values(),*packet['inbound'].values()]
        self.assertTrue(rows)
        self.assertTrue(all(math.isfinite(v['Cost']) for v in rows))

    def test_shared_packet_keeps_explicit_hotel_features(self):
        task={'uid':'gym','start_city':'杭州','target_city':'成都','days':3,'people_number':2,'nature_language':'双床房，酒店要可以健身'}
        packet=public_context(task,ToolBox(),time.monotonic()+30)
        self.assertTrue(any(v.get('featurehoteltype')=='Fitness Room' for v in packet['hotel'].values()))


class LocalWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();handler,self.jobs=application(self.directory.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.jobs.close();self.thread.join();self.directory.cleanup()
    def request(self,path,body=None,headers=None):
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
        return urllib.request.urlopen(request,timeout=5)
    def test_public_examples_cover_all_three_demonstrations(self):
        with self.request('/api/examples') as response:data=json.load(response)
        self.assertEqual(set(data),{'planning','transport','clarification'})
        self.assertTrue(all(v['replay'] for v in data.values()))
        self.assertTrue(data['clarification']['demo_question'])
    def test_foreign_origin_and_missing_session_cannot_create_jobs(self):
        for headers in [{'Origin':'https://untrusted.example'},{}]:
            with self.assertRaises(urllib.error.HTTPError) as failure:self.request('/api/jobs',{'command':'plan'},headers)
            self.assertEqual(failure.exception.code,403)
        self.assertFalse(self.jobs.items)
    def test_valid_session_creates_and_cancels_only_its_job(self):
        with self.request('/') as response:page=response.read().decode()
        token=re.search("const session='([^']+)'",page).group(1)
        process=MagicMock();process.poll.return_value=None
        with patch('trippilot.web.server.subprocess.Popen',return_value=process):
            with self.request('/api/jobs',{'command':'plan','request':'去公园'}, {'X-TripPilot-Session':token}) as response:key=json.load(response)['id']
            with self.request('/api/jobs/'+key+'/cancel',{}, {'X-TripPilot-Session':token}) as response:self.assertEqual(json.load(response)['status'],'cancelling')
            process.send_signal.assert_called_once()
            process.poll.return_value=0


if __name__=='__main__':unittest.main()
