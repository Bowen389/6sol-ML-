#!/usr/bin/env python3
"""Fetch current K-lines using SteamDT API; fail closed on coverage/API errors."""
import argparse
import os
import random
import time
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests

URL='https://open.steamdt.com/open/cs2/item/v1/kline'
TZ=ZoneInfo('Asia/Shanghai')

def decode(payload,name,category):
    if payload.get('success') is not True:
        raise ValueError(f"SteamDT rejected request: code={payload.get('errorCode')}, msg={payload.get('errorMsg')}")
    bars=payload.get('data')
    if not isinstance(bars,list):raise ValueError('unexpected data type')
    rows=[]
    for bar in bars:
        if not isinstance(bar,(list,tuple)) or len(bar)<5:raise ValueError('unexpected K-line shape; expected [timestamp,open,close,high,low]')
        ts=int(bar[0]);dt=datetime.fromtimestamp(ts,timezone.utc).astimezone(TZ)
        # Explicit validation for daily granularity; the API type setting needs confirmation if this fails.
        values=[float(bar[i]) for i in range(1,5)]
        if not all(0<v<1e10 for v in values):raise ValueError('invalid price')
        op,cl,hi,lo=values
        if hi<max(op,cl) or lo>min(op,cl):raise ValueError('invalid OHLC')
        rows.append((name,category,dt.date().isoformat(),ts,op,cl,hi,lo))
    if len(rows)!=len({r[2] for r in rows}):raise ValueError('multiple bars on same Shanghai calendar date; wrong Kline type?')
    if len(rows)<60:raise ValueError(f'insufficient daily history: {len(rows)} bars')
    return rows

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--universe',default='universe.csv');ap.add_argument('--output',default='data/live.csv');ap.add_argument('--min-coverage',type=float,default=.98);args=ap.parse_args()
    key=os.environ.get('STEAMDT_API_KEY')
    if not key:raise SystemExit('Missing GitHub Actions secret STEAMDT_API_KEY')
    typ=int(os.getenv('STEAMDT_KLINE_TYPE','1'))
    if typ not in (1,2,3):raise SystemExit('STEAMDT_KLINE_TYPE must be 1, 2 or 3')
    universe=pd.read_csv(args.universe).drop_duplicates('marketHashName')
    if universe.empty:raise SystemExit('universe is empty')
    session=requests.Session();session.headers.update({'Authorization':f'Bearer {key}','Content-Type':'application/json'})
    rows=[];failures=[];last_req=0.0
    for count,(_,item) in enumerate(universe.iterrows(),1):
        name=item.marketHashName
        for attempt in range(5):
            # <=~92 requests/minute; SteamDT lists a 120/min K-line limit.
            delay=.66-(time.monotonic()-last_req)
            if delay>0:time.sleep(delay)
            try:
                last_req=time.monotonic()
                resp=session.post(URL,json={'marketHashName':name,'type':typ,'platform':'ALL'},timeout=25)
                if resp.status_code in (401,403):
                    raise SystemExit(f'Authorization/permission failure HTTP {resp.status_code}. Check Secret, Bearer permission and API access; stop before scanning all items.')
                if resp.status_code in (429,500,502,503,504):
                    if attempt==4:failures.append((name,f'HTTP {resp.status_code} after retries'))
                    else:time.sleep(min(60,2**attempt*3+random.random()))
                    continue
                if resp.status_code!=200:
                    failures.append((name,f'HTTP {resp.status_code}'))
                    break
                payload=resp.json()
                if payload.get('success') is not True:
                    code=payload.get('errorCode');msg=str(payload.get('errorMsg',''))[:120].replace(key,'[REDACTED]')
                    failures.append((name,f'API code={code}, msg={msg}'))
                    break  # Business errors do not benefit from retrying 5 times.
                rows.extend(decode(payload,name,item.category))
                break
            except (requests.RequestException,ValueError,KeyError,OverflowError) as err:
                safe=str(err)[:180].replace(key,'[REDACTED]')
                if attempt==4:failures.append((name,safe))
                else:time.sleep(min(30,2**attempt+random.random()))
        if count<=3 and failures:print(f'Initial API error {count}: {failures[-1]}',flush=True)
        if count>=3 and len(failures)==count:
            raise SystemExit(f'First {count} items all failed; stopping early. Examples: {failures[:3]}')
        if count%100==0:print(f'Fetched {count}/{len(universe)}; failed {len(failures)}',flush=True)
    coverage=1-len(failures)/len(universe)
    if coverage<args.min_coverage:raise SystemExit(f'Insufficient API coverage {coverage:.1%}; first errors: {failures[:8]}')
    if not rows:raise SystemExit('No rows fetched')
    df=pd.DataFrame(rows,columns=['饰品名称','品类','日期','更新时间','开盘指数','收盘指数','最高指数','最低指数'])
    if df.duplicated(['饰品名称','日期']).any():raise SystemExit('Duplicate bars; aborting')
    latest=df['日期'].max();recent=df[df['日期']==latest]['饰品名称'].nunique()
    if recent<len(universe)*.8:raise SystemExit(f'Latest date {latest}: only {recent}/{len(universe)} items updated; aborting instead of partial ranking')
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    tmp=out.with_suffix('.tmp');df.to_csv(tmp,index=False);tmp.replace(out)
    print(f'Success {len(df)} bars, {coverage:.1%} item coverage, latest={latest}, latest items={recent}; errors={failures[:5]}')
if __name__=='__main__':main()
