"""金额的取整口径。

两件事以前是错的：

1. `round(x, 2)` 用的是**银行家舍入**（round-half-even）：`round(0.125, 2)` 得 0.12，
   `round(0.135, 2)` 得 0.14。财务口径要的是四舍五入（round-half-up），
   这个差别在单笔上只有半分钱，在对账时就是"两边差 0.01 但查不出哪来的"。
2. 浮点数没法精确表示 0.1 这类十进制小数，`0.1 + 0.2 != 0.3`。先把数转成
   `Decimal(str(x))` 再量化，取整就只依赖十进制的字面值，不受二进制表示影响。

范围说明：**没有把整个系统改成 Decimal**。汇率、配置、聚合中间量仍是 float——
全量改造要连接口出入参一起动，收益递减。当前口径是：
**对外输出的金额和结算类算式走 Decimal 确定性取整**，聚合中间量允许浮点误差
（远小于分）。要判断一个数该不该走这里：它会不会出现在给人看的报表上、
或者会不会被拿去和银行对账——会，就用 `f2`。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def D(value: object) -> Decimal:
    """任何数值转 Decimal。先转成字符串，避开二进制浮点的表示误差。"""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal(0)
    return Decimal(str(value))


def q2(value: object) -> Decimal:
    """量化到分，四舍五入。"""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def f2(value: object) -> float:
    """量化到分后转回 float，用于 JSON 输出。"""
    return float(q2(value))


def q(value: object, places: int) -> Decimal:
    exp = Decimal(1).scaleb(-places)
    return D(value).quantize(exp, rounding=ROUND_HALF_UP)


def f(value: object, places: int) -> float:
    return float(q(value, places))
