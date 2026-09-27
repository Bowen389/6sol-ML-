#!/usr/bin/env python3
"""从 SteamDT 获取日K；固定 type=2，先验证，再扫描全部饰品。"""

import argparse
import os
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

URL = "https://open.steamdt.com/open/cs2/item/v1/kline"
KLINE_TYPE = 2
TIMEZONE = ZoneInfo("Asia/Shanghai")
VERSION = "type2-daily-2026-09-27"


def decode_bars(payload, name, category):
    if payload.get("success") is not True:
        raise ValueError(
            f"API errorCode={payload.get('errorCode')}, "
            f"errorMsg={payload.get('errorMsg')}"
        )

    bars = payload.get("data")
    if not isinstance(bars, list):
        raise ValueError("API data 不是K线数组")

    rows = []
    for bar in bars:
        if not isinstance(bar, (list, tuple)) or len(bar) < 5:
            raise ValueError("K线格式不是 [时间戳, 开盘, 收盘, 最高, 最低]")

        timestamp = int(bar[0])
        date = datetime.fromtimestamp(
            timestamp, timezone.utc
        ).astimezone(TIMEZONE).date().isoformat()

        opening, closing, high, low = map(float, bar[1:5])

        if (
            min(opening, closing, high, low) <= 0
            or high < max(opening, closing)
            or low > min(opening, closing)
        ):
            raise ValueError("发现无效OHLC价格")

        rows.append(
            (name, category, date, timestamp, opening, closing, high, low)
        )

    dates = [row[2] for row in rows]
    if len(dates) != len(set(dates)):
        raise ValueError("同一上海日期有多根K线，返回的不是预期日K")

    if len(rows) < 60:
        raise ValueError(f"历史不足60根日K，实际只有{len(rows)}根")

    return rows


def request_item(session, name):
    response = session.post(
        URL,
        json={
            "marketHashName": name,
            "type": KLINE_TYPE,
            "platform": "ALL",
        },
        timeout=25,
    )

    if response.status_code in (401, 403):
        raise SystemExit(
            f"HTTP {response.status_code}：检查API Key或接口权限"
        )

    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="universe.csv")
    parser.add_argument("--output", default="data/live.csv")
    args = parser.parse_args()

    print(f"FETCH_VERSION={VERSION}; REQUEST_TYPE={KLINE_TYPE}", flush=True)

    api_key = os.environ.get("STEAMDT_API_KEY")
    if not api_key:
        raise SystemExit("缺少STEAMDT_API_KEY Secret")

    universe = pd.read_csv(args.universe).drop_duplicates("marketHashName")
    if universe.empty:
        raise SystemExit("universe.csv为空")

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    # 只检查第一个饰品；通过后才进行全量扫描。
    first = universe.iloc[0]
    first_name = first["marketHashName"]

    try:
        payload = request_item(session, first_name)
        bars = payload.get("data", [])

        if payload.get("success") is not True:
            raise ValueError(
                f"API errorCode={payload.get('errorCode')}, "
                f"errorMsg={payload.get('errorMsg')}"
            )

        if not isinstance(bars, list) or len(bars) < 60:
            raise ValueError(f"K线数量不足：{len(bars)}")

        timestamps = sorted(int(bar[0]) for bar in bars)
        intervals = [
            b - a for a, b in zip(timestamps, timestamps[1:])
        ]
        median_interval = statistics.median(intervals)

        print(
            f"首个饰品：{first_name}；K线{len(bars)}根；"
            f"相邻K线时间间隔中位数={median_interval}秒",
            flush=True,
        )

        if not 72_000 <= median_interval <= 100_800:
            raise ValueError(
                f"type=2返回的不是约24小时间隔的日K；"
                f"实际间隔中位数为{median_interval}秒"
            )

        first_rows = decode_bars(
            payload, first_name, first["category"]
        )

    except (requests.RequestException, ValueError, KeyError) as error:
        safe_error = str(error).replace(api_key, "[REDACTED]")
        raise SystemExit(f"首个饰品日K预检失败：{safe_error}")

    print("日K预检通过，开始扫描全部饰品。", flush=True)

    all_rows = list(first_rows)
    failures = []
    last_request = time.monotonic()

    # 首个饰品已获取，不重复请求。
    for count, (_, item) in enumerate(
        universe.iloc[1:].iterrows(), start=2
    ):
        name = item["marketHashName"]
        category = item["category"]
        success = False

        for attempt in range(5):
            # 控制在约每分钟90次以下，低于文档所列120次/分钟。
            wait = 0.66 - (time.monotonic() - last_request)
            if wait > 0:
                time.sleep(wait)

            try:
                last_request = time.monotonic()
                payload = request_item(session, name)
                all_rows.extend(decode_bars(payload, name, category))
                success = True
                break

            except requests.HTTPError as error:
                status = error.response.status_code
                if status not in (429, 500, 502, 503, 504):
                    failures.append((name, f"HTTP {status}"))
                    break
                if attempt == 4:
                    failures.append((name, f"HTTP {status}，重试后仍失败"))
                else:
                    time.sleep(min(60, 3 * 2**attempt + random.random()))

            except (requests.RequestException, ValueError, KeyError) as error:
                if attempt == 4:
                    failures.append((
                        name,
                        str(error).replace(api_key, "[REDACTED]")[:150],
                    ))
                else:
                    time.sleep(min(30, 2**attempt + random.random()))

        if not success and len(failures) >= 3 and count <= 5:
            raise SystemExit(
                f"扫描开始后连续失败；提前停止。错误示例：{failures[:3]}"
            )

        if count % 100 == 0:
            print(
                f"已处理 {count}/{len(universe)}；失败 {len(failures)}",
                flush=True,
            )

    coverage = 1 - len(failures) / len(universe)
    if coverage < 0.98:
        raise SystemExit(
            f"饰品覆盖率仅{coverage:.1%}，停止发布。"
            f"错误示例：{failures[:5]}"
        )

    df = pd.DataFrame(
        all_rows,
        columns=[
            "饰品名称", "品类", "日期", "更新时间",
            "开盘指数", "收盘指数", "最高指数", "最低指数",
        ],
    )

    if df.duplicated(["饰品名称", "日期"]).any():
        raise SystemExit("发现饰品/日期重复记录，停止发布")

    latest_date = df["日期"].max()
    latest_count = df.loc[
        df["日期"] == latest_date, "饰品名称"
    ].nunique()

    if latest_count < len(universe) * 0.8:
        raise SystemExit(
            f"最新日期{latest_date}仅有"
            f"{latest_count}/{len(universe)}个饰品更新，停止发布"
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    df.to_csv(temporary, index=False)
    temporary.replace(output)

    print(
        f"完成：{len(df)}根K线，覆盖率{coverage:.1%}，"
        f"最新日期{latest_date}，当日饰品{latest_count}个",
        flush=True,
    )


if __name__ == "__main__":
    main()
