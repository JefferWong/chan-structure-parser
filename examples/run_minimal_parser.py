"""
最小运行示例：端到端跑通数据加载 → 解析 → 输出 → 可视化。

使用随机生成的OHLC数据验证引擎的基本功能。
"""

import json
import random
import sys
import os
from datetime import datetime, timedelta

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yaml

from chan_parser.adapters import DataFrameAdapter
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.audit.consistency import ConsistencyChecker
from chan_parser.output.serializer import Serializer
from chan_parser.visualization import plot_chan_structure


def generate_sample_data(num_bars: int = 200, seed: int = 42) -> list[dict]:
    """生成随机OHLC数据，模拟真实走势。"""
    random.seed(seed)

    data = []
    base_date = datetime(2024, 1, 2, 9, 30)
    price = 100.0
    trend = 0  # 0=盘整, 1=上涨, -1=下跌
    trend_length = 0

    for i in range(num_bars):
        # 趋势切换
        trend_length += 1
        if trend_length > random.randint(15, 40):
            trend = random.choice([-1, 0, 0, 1, 1])  # 偏向盘整
            trend_length = 0

        # 价格变动
        drift = trend * random.uniform(0.2, 1.5)
        noise = random.gauss(0, 1.0)
        change = drift + noise

        open_price = price
        close_price = price + change

        # 生成OHLC
        high = max(open_price, close_price) + abs(random.gauss(0, 0.5))
        low = min(open_price, close_price) - abs(random.gauss(0, 0.5))
        low = max(low, 0.1)  # 防止负数

        timestamp = base_date + timedelta(minutes=30 * i)

        data.append({
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close_price, 2),
            "volume": random.randint(10000, 100000),
        })

        price = close_price

    return data


def main():
    print("=" * 60)
    print("  Chan Structure Parser — Minimal Run Example")
    print("=" * 60)

    # 1. 加载配置
    profile_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", "profiles", "minimal_strict_v1.yaml"
    )
    with open(profile_path, "r") as f:
        profile = yaml.safe_load(f)
    print(f"\n[1] Profile loaded: {profile['profile_id']} v{profile['profile_version']}")

    # 2. 生成/加载数据
    data = generate_sample_data(num_bars=200)
    adapter = DataFrameAdapter(data)
    raw_bars, quality = adapter.load()
    print(f"\n[2] Data loaded: {quality['raw_bar_count']} bars, "
          f"{quality['valid_bar_count']} valid, status={quality['status']}")

    if quality["status"] == "ERROR":
        print("ERROR: No valid bars to process.")
        return

    # 3. 全量重建
    print("\n[3] Running full rebuild...")
    full_engine = FullRebuildEngine(profile)
    full_result = full_engine.process(raw_bars)

    structures = full_result["structures"]
    print(f"    Merged bars: {len(structures['merged_bars'])}")
    print(f"    Fractals:    {len(structures['fractals'])}")
    print(f"    Strokes:     {len(structures['strokes'])}")

    # 打印分型和笔的摘要
    for f in structures["fractals"]:
        print(f"      {f['fractal_id']}: {f['fractal_type']} @ "
              f"bar[{f['merged_bar_index']}] price={f['price']:.2f} "
              f"status={f['status']}")

    for s in structures["strokes"]:
        print(f"      {s['stroke_id']}: {s['direction']} "
              f"bar[{s['start_bar_index']}->{s['end_bar_index']}] "
              f"price[{s['start_price']:.2f}->{s['end_price']:.2f}] "
              f"Δ={s['price_range']:.2f} status={s['status']}")

    # 4. 增量引擎验证
    print("\n[4] Running incremental engine...")
    incr_engine = IncrementalEngine(profile)
    incr_result = None

    for i, bar in enumerate(raw_bars):
        incr_result = incr_engine.append_one(bar)

        if i == 0:
            continue

    incr_structures = incr_result["structures"]
    print(f"    Merged bars: {len(incr_structures['merged_bars'])}")
    print(f"    Fractals:    {len(incr_structures['fractals'])}")
    print(f"    Strokes:     {len(incr_structures['strokes'])}")

    # 5. 一致性检查
    print("\n[5] Consistency check...")
    checker = ConsistencyChecker()
    consistency = checker.check(full_result, incr_result)
    print(f"    Result: {'PASS' if consistency['pass'] else 'FAIL'}")
    if not consistency["pass"]:
        for diff in consistency["differences"]:
            print(f"      DIFF: {diff}")

    # 6. 序列化输出
    print("\n[6] Serializing output...")
    serializer = Serializer()
    output_dir = os.path.join(os.path.dirname(__file__), "..")
    output_path = os.path.join(output_dir, "output.json")
    content_hash = serializer.save(full_result, output_path)
    print(f"    Saved to: {output_path}")
    print(f"    Content hash: {content_hash}")

    # 7. 可视化
    print("\n[7] Generating chart...")
    chart_path = os.path.join(output_dir, "output.png")
    plot_chan_structure(
        full_result,
        title="Chan Structure Parser — Sample Run",
        save_path=chart_path,
    )

    # 8. 验收门禁摘要
    print("\n[8] Acceptance gates summary:")
    runtime = full_result["runtime_state"]
    print(f"    Unfinished fractals: {runtime['unfinished_fractal_count']}")
    print(f"    Unfinished strokes:  {runtime['unfinished_stroke_count']}")
    print(f"    Incremental/Full consistency: {'PASS' if consistency['pass'] else 'FAIL'}")
    print(f"    Output SHA256: {full_result['audit']['output_sha256']}")

    print("\n" + "=" * 60)
    print("  Run complete.")
    print(f"  Output JSON: {output_path}")
    print(f"  Chart PNG:   {chart_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
