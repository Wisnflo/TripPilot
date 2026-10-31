#!/usr/bin/env python3
"""Launch the local itinerary workspace; optional read-only replay needs no API."""
import argparse
from pathlib import Path
import os
import sys
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.web.server import application
from trippilot.models.config import resolve_model_config,ConfigError


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);p.add_argument('--output',default='output/demo');a=p.parse_args()
    try:
        config=resolve_model_config(env_file=Path(os.environ['TRIPPILOT_CONFIG_FILE']) if os.environ.get('TRIPPILOT_CONFIG_FILE') else None)
        os.environ['CHINATRAVEL_OPENAI_API_KEY']=config.api_key;os.environ['CHINATRAVEL_OPENAI_MODEL']=config.model
        if config.base_url:os.environ['CHINATRAVEL_OPENAI_BASE_URL']=config.base_url
    except ConfigError:pass
    handler,jobs=application(ROOT/a.output);server=ThreadingHTTPServer(('127.0.0.1',a.port),handler)
    print('TripPilot: http://127.0.0.1:'+str(server.server_port),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();jobs.close()

if __name__=='__main__':main()
