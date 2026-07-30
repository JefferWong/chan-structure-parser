"""
增量/全量一致性验证器。

验证增量计算和全量重建的结果是否一致。
"""

from __future__ import annotations

from typing import Any


class ConsistencyChecker:
    """一致性检查器。

    验证项：
    1. 已确认结构完全匹配
    2. logical_id 完全匹配
    3. 未完成尾部结构允许状态差异，但必须有等价规则
    """

    def check(
        self,
        full_result: dict[str, Any],
        incremental_result: dict[str, Any],
    ) -> dict[str, Any]:
        """执行一致性检查。

        Returns:
            {
                "pass": bool,
                "checks": [...],
                "differences": [...],
            }
        """
        checks = []
        differences = []

        # 1. 合并K线一致性
        mb_check = self._check_merged_bars(full_result, incremental_result)
        checks.append(mb_check)
        if not mb_check["pass"]:
            differences.extend(mb_check.get("differences", []))

        # 2. 已确认分型一致性
        fx_check = self._check_confirmed_fractals(full_result, incremental_result)
        checks.append(fx_check)
        if not fx_check["pass"]:
            differences.extend(fx_check.get("differences", []))

        # 3. 已确认笔一致性
        st_check = self._check_confirmed_strokes(full_result, incremental_result)
        checks.append(st_check)
        if not st_check["pass"]:
            differences.extend(st_check.get("differences", []))

        # 4. logical_id 一致性
        lid_check = self._check_logical_ids(full_result, incremental_result)
        checks.append(lid_check)
        if not lid_check["pass"]:
            differences.extend(lid_check.get("differences", []))

        all_pass = all(c["pass"] for c in checks)

        return {
            "pass": all_pass,
            "checks": checks,
            "differences": differences,
            "summary": (
                "All consistency checks passed"
                if all_pass
                else f"{len(differences)} difference(s) found"
            ),
        }

    def _check_merged_bars(
        self, full: dict, incr: dict
    ) -> dict[str, Any]:
        """检查合并K线一致性。"""
        full_mb = full["structures"]["merged_bars"]
        incr_mb = incr["structures"]["merged_bars"]

        if len(full_mb) != len(incr_mb):
            return {
                "pass": False,
                "check": "merged_bar_count",
                "differences": [
                    f"Count mismatch: full={len(full_mb)}, incr={len(incr_mb)}"
                ],
            }

        diffs = []
        for i, (f, inc) in enumerate(zip(full_mb, incr_mb)):
            if f["open"] != inc["open"] or f["high"] != inc["high"] or \
               f["low"] != inc["low"] or f["close"] != inc["close"]:
                diffs.append(
                    f"MergedBar[{i}] OHLC mismatch: "
                    f"full=({f['open']},{f['high']},{f['low']},{f['close']}) "
                    f"incr=({inc['open']},{inc['high']},{inc['low']},{inc['close']})"
                )

        return {
            "pass": len(diffs) == 0,
            "check": "merged_bar_consistency",
            "differences": diffs,
        }

    def _check_confirmed_fractals(
        self, full: dict, incr: dict
    ) -> dict[str, Any]:
        """检查已确认分型一致性。"""
        full_fx = [
            f for f in full["structures"]["fractals"]
            if f["status"] == "CONFIRMED"
        ]
        incr_fx = [
            f for f in incr["structures"]["fractals"]
            if f["status"] == "CONFIRMED"
        ]

        diffs = []
        if len(full_fx) != len(incr_fx):
            diffs.append(
                f"Confirmed fractal count mismatch: "
                f"full={len(full_fx)}, incr={len(incr_fx)}"
            )
        else:
            for i, (f, inc) in enumerate(zip(full_fx, incr_fx)):
                if f["fractal_type"] != inc["fractal_type"] or \
                   f["merged_bar_index"] != inc["merged_bar_index"] or \
                   f["price"] != inc["price"]:
                    diffs.append(
                        f"Confirmed fractal[{i}] mismatch: "
                        f"full=({f['fractal_type']},{f['merged_bar_index']},{f['price']}) "
                        f"incr=({inc['fractal_type']},{inc['merged_bar_index']},{inc['price']})"
                    )

        return {
            "pass": len(diffs) == 0,
            "check": "confirmed_fractal_consistency",
            "differences": diffs,
        }

    def _check_confirmed_strokes(
        self, full: dict, incr: dict
    ) -> dict[str, Any]:
        """检查已确认笔一致性。"""
        full_st = [
            s for s in full["structures"]["strokes"]
            if s["status"] == "CONFIRMED"
        ]
        incr_st = [
            s for s in incr["structures"]["strokes"]
            if s["status"] == "CONFIRMED"
        ]

        diffs = []
        if len(full_st) != len(incr_st):
            diffs.append(
                f"Confirmed stroke count mismatch: "
                f"full={len(full_st)}, incr={len(incr_st)}"
            )
        else:
            for i, (f, inc) in enumerate(zip(full_st, incr_st)):
                if f["direction"] != inc["direction"] or \
                   f["start_bar_index"] != inc["start_bar_index"] or \
                   f["end_bar_index"] != inc["end_bar_index"] or \
                   f["start_price"] != inc["start_price"] or \
                   f["end_price"] != inc["end_price"]:
                    diffs.append(
                        f"Confirmed stroke[{i}] mismatch: "
                        f"full=({f['direction']},{f['start_bar_index']}->{f['end_bar_index']},"
                        f"{f['start_price']}->{f['end_price']}) "
                        f"incr=({inc['direction']},{inc['start_bar_index']}->{inc['end_bar_index']},"
                        f"{inc['start_price']}->{inc['end_price']})"
                    )

        return {
            "pass": len(diffs) == 0,
            "check": "confirmed_stroke_consistency",
            "differences": diffs,
        }

    def _check_logical_ids(
        self, full: dict, incr: dict
    ) -> dict[str, Any]:
        """检查 logical_id 一致性。"""
        full_fx_ids = {
            f["logical_id"] for f in full["structures"]["fractals"]
            if f.get("logical_id")
        }
        incr_fx_ids = {
            f["logical_id"] for f in incr["structures"]["fractals"]
            if f.get("logical_id")
        }

        full_st_ids = {
            s["logical_id"] for s in full["structures"]["strokes"]
            if s.get("logical_id")
        }
        incr_st_ids = {
            s["logical_id"] for s in incr["structures"]["strokes"]
            if s.get("logical_id")
        }

        diffs = []

        fx_only_full = full_fx_ids - incr_fx_ids
        fx_only_incr = incr_fx_ids - full_fx_ids
        st_only_full = full_st_ids - incr_st_ids
        st_only_incr = incr_st_ids - full_st_ids

        if fx_only_full:
            diffs.append(f"Fractals only in full: {fx_only_full}")
        if fx_only_incr:
            diffs.append(f"Fractals only in incr: {fx_only_incr}")
        if st_only_full:
            diffs.append(f"Strokes only in full: {st_only_full}")
        if st_only_incr:
            diffs.append(f"Strokes only in incr: {st_only_incr}")

        return {
            "pass": len(diffs) == 0,
            "check": "logical_id_consistency",
            "differences": diffs,
        }
