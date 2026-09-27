#!/usr/bin/env python3
"""Daily research scanner. No trading or external API requests."""
import argparse
import glob
import os
from pathlib import Path
import joblib
import numpy as np
import pandas as pd


def features_for_latest(df):
    df=df.sort_values(['饰品名称','date']).reset_index(drop=True)
    g=df.groupby('饰品名称',sort=False)
    O=df['开盘指数']; C=df['收盘指数']; H=df['最高指数']; L=df['最低指数']
    span=(H-L).replace(0,np.nan)
    f={}
    for n in [3,5,7,10,14,30,60]: f['ret'+str(n)]=C/g['收盘指数'].shift(n)-1
    for n in [7,14,30,60]:
        hi=g['最高指数'].transform(lambda s:s.rolling(n,min_periods=n).max())
        lo=g['最低指数'].transform(lambda s:s.rolling(n,min_periods=n).min())
        f['draw'+str(n)]=C/hi-1
        f['position'+str(n)]=(C-lo)/(hi-lo).replace(0,np.nan)
    f['body']=C/O-1
    f['body_range']=(C-O)/span
    f['close_location']=(C-L)/span
    f['upper']=(H-np.maximum(C,O))/span
    f['lower']=(np.minimum(C,O)-L)/span
    f['range']=span/O
    prevO=g['开盘指数'].shift(1); prevC=g['收盘指数'].shift(1)
    f['engulf']=((C>prevO)&(O<prevC)&(C>O)&(prevC<prevO)).astype(float)
    for n in [1,3,5]:
        past_hi=g['最高指数'].transform(lambda s:s.shift(1).rolling(n,min_periods=n).max())
        f['break'+str(n)]=(C>past_hi).astype(float)
    f['deceleration']=f['ret3']-f['ret7']*3/7
    for key,val in f.items(): df[key]=pd.Series(val,index=df.index).replace([np.inf,-np.inf],np.nan).astype('float32')
    return df


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',default='data/*.csv')
    ap.add_argument('--output',default='reports')
    ap.add_argument('--max-age-days',type=int,default=2)
    args=ap.parse_args()
    paths=sorted(glob.glob(args.input))
    if not paths: raise SystemExit('No CSV data in '+args.input+'; no report produced')
    columns=['饰品名称','品类','日期','开盘指数','收盘指数','最高指数','最低指数']
    frames=[]
    for path in paths:
        d=pd.read_csv(path,usecols=columns)
        frames.append(d)
    d=pd.concat(frames,ignore_index=True)
    d['date']=pd.to_datetime(d['日期'],errors='coerce')
    d=d.dropna(subset=['date']+columns[3:])
    if d.duplicated(['饰品名称','date']).any():
        raise SystemExit('Duplicate item/date rows across input files; aborting rather than silently choosing a price')
    d=d[(d['开盘指数']>0)&(d['收盘指数']>0)&(d['最低指数']>0)&
        (d['最高指数']>=d[['开盘指数','收盘指数']].max(axis=1))&
        (d['最低指数']<=d[['开盘指数','收盘指数']].min(axis=1))]
    d=features_for_latest(d)
    last=d.date.max();today=pd.Timestamp.now(tz='Asia/Tokyo').tz_localize(None).normalize()
    age=(today-last).days
    if age<0:raise SystemExit('Input data date is in the future')
    current=d[(d.date==last)&d.ret60.notna()].copy()
    if current.empty:raise SystemExit('Not enough 60-day history at latest date')
    pack=joblib.load('models/nakedK_7d.joblib') # only load models you trust
    score=pack['model'].predict(current[pack['features']].clip(-5,5))
    current['score']=pd.Series(score,index=current.index).rank(pct=True)*100
    current['strategy_1']=(current.score>=99.5)&(current.body>=.03)&(current.close_location>=.6)
    current['strategy_2']=(current.ret7<=-.15)&(current.position30<=.3)&(current.body>=.03)&(current.close_location>=.6)
    current['strategy_3']=(current.ret30>0)&(current.break5>0)&(current.close_location>=.7)&(current.body>0)
    current['strategy_count']=current[['strategy_1','strategy_2','strategy_3']].sum(axis=1)
    picks=current[current.strategy_count>0].sort_values(['strategy_count','score'],ascending=False).copy()
    picks['strategies']=picks.apply(lambda r:'、'.join(str(i) for i in (1,2,3) if r['strategy_'+str(i)]),axis=1)
    selected=picks[['饰品名称','品类','score','strategies','ret7','ret30','body','close_location','position30']].copy()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    selected.to_csv(out/'latest_candidates.csv',index=False,encoding='utf-8-sig')
    stale=age>args.max_age_days
    lines=['# 裸K每日候选观察','',f'- 运行日期（日本时间）：{today.date()}',f'- 最新K线日期：{last.date()}（距今 {age} 天）',f'- 有效饰品：{len(current)}；候选：{len(selected)}',f'- 状态：**{"数据过期：禁止把下表当成今日买点" if stale else "数据日期在允许范围内；仅供观察，非买入建议"}**','',
        '## 策略口径','1. 7天模型排序前0.5%＋阳线实体至少3%＋收盘位置至少60%。',
        '2. 过去7日跌幅至少15%＋处于30日区间最低30%＋阳线实体至少3%＋收盘位置至少60%。',
        '3. 30日涨幅为正＋收盘突破此前5日最高价＋收盘位置至少70%＋阳线。','',
        '信号只能在该日收盘后确定；理论回测为下一日开盘进入。**这些条件未经稳定盈利验证；指数不是可成交报价，未计费用、价差和滑点。**','',
        '## 候选（按命中策略数量、模型分数排序）','']
    if stale:lines += ['⚠️ 数据过期，下表是**历史信号**，不代表今天可用。','']
    if selected.empty:lines += ['无候选。']
    else:
        lines += ['| 饰品 | 品类 | 命中策略 | 7天模型分位分 | 7日涨跌 | 30日涨跌 |','|---|---|---:|---:|---:|---:|']
        for _,r in selected.head(50).iterrows():
            name=str(r['饰品名称']).replace('|','\\|');lines.append(f"| {name} | {r['品类']} | {r['strategies']} | {r['score']:.1f} | {r['ret7']:.1%} | {r['ret30']:.1%} |")
        if len(selected)>50: lines += ['',f'仅展示前50条；全部 {len(selected)} 条见CSV。']
    (out/'latest.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    print(f'Latest date {last.date()}, age {age}d, candidates {len(selected)}, stale={stale}')

if __name__=='__main__':main()
