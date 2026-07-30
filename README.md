# Chan Structure Parser (缠论结构解析引擎)

> 第一阶段：K线包含处理 → 分型识别 → 笔构建

## 设计原则

1. **确定性**：同一输入 + 同一配置 + 同一版本 → 完全一致的输出
2. **无未来函数**：任意历史时点只使用当时已知数据
3. **可增量更新**：支持逐K线追加，局部重建
4. **可审计**：追加式事件日志 + 结构谱系追踪 + 确定性哈希
5. **规则显式化**：所有算法参数冻结在配置文件中，不依赖库默认行为

## 项目结构

```
chan-structure-parser/
├── pyproject.toml
├── configs/profiles/
│   └── minimal_strict_v1.yaml    # 冻结的规则配置
├── src/chan_parser/
│   ├── domain/                    # 领域对象
│   │   ├── raw_bar.py            # 原始K线
│   │   ├── merged_bar.py         # 合并K线
│   │   ├── fractal.py            # 分型
│   │   ├── stroke.py             # 笔
│   │   └── lifecycle.py          # 生命周期基类与事件模型
│   ├── engine/                    # 核心引擎
│   │   ├── inclusion.py          # K线包含处理
│   │   ├── fractal.py            # 分型识别
│   │   ├── stroke.py             # 笔构建
│   │   ├── incremental.py        # 增量计算
│   │   └── full_rebuild.py       # 全量重建
│   ├── audit/                     # 审计
│   │   ├── event_log.py          # 追加式事件日志
│   │   ├── consistency.py        # 增量/全量一致性检查
│   │   └── lineage.py            # 结构谱系追踪
│   ├── adapters/                  # 数据适配器
│   │   ├── csv_adapter.py
│   │   └── dataframe_adapter.py
│   ├── output/                    # 输出
│   │   └── serializer.py         # JSON序列化
│   └── visualization/             # 可视化
│       └── matplotlib_chart.py
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── replay/
│   ├── property/
│   └── consistency/
└── examples/
    └── run_minimal_parser.py
```

## 快速开始

```python
from chan_parser.adapters import DataFrameAdapter
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.visualization import plot_chan_structure
import yaml

# 加载配置
with open("configs/profiles/minimal_strict_v1.yaml") as f:
    profile = yaml.safe_load(f)

# 加载数据
data = [
    {"timestamp": "2024-01-02", "open": 100, "high": 105, "low": 98, "close": 103},
    {"timestamp": "2024-01-03", "open": 103, "high": 108, "low": 101, "close": 106},
    # ... 更多K线
]
adapter = DataFrameAdapter(data)
raw_bars, quality = adapter.load()

# 全量解析
engine = FullRebuildEngine(profile)
result = engine.process(raw_bars)

# 可视化
plot_chan_structure(result, title="300308.SZ 30min", save_path="output.png")
```

## 输出格式

```json
{
  "meta": {
    "profile_id": "minimal_strict_v1",
    "engine_version": "0.1.0",
    "analysis_mode": "close_only",
    "calculation_mode": "full_rebuild"
  },
  "data_quality": {
    "raw_bar_count": 800,
    "valid_bar_count": 798,
    "status": "WARNING"
  },
  "structures": {
    "merged_bars": [...],
    "fractals": [...],
    "strokes": [...]
  },
  "runtime_state": {
    "unfinished_fractal_count": 1,
    "unfinished_stroke_count": 1
  },
  "audit": {
    "output_sha256": "...",
    "event_log_sha256": "..."
  }
}
```

## 验收门禁

第一阶段完成标准（全部 PASS 才可进入线段和中枢阶段）：

- [ ] `DETERMINISTIC_REPLAY` — 同输入多次运行输出一致
- [ ] `INCREMENTAL_FULL_CONSISTENCY` — 增量与全量已确认结构一致
- [ ] `HISTORICAL_SNAPSHOT_IMMUTABLE` — 历史快照不被后续数据改写
- [ ] `NO_FUTURE_REFERENCE` — 任意历史时点不使用未来数据
- [ ] `STRUCTURE_LINEAGE_COMPLETE` — 所有结构对象可追溯血缘
- [ ] `VISUAL_JSON_ALIGNMENT` — 可视化与JSON结构对齐

## License

MIT
